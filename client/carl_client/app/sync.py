"""CARL client config sync. The dashboard on the server sends the client config
(its Connect tab, or carl push on the server). This tool applies it to
OpenCode and Pi on this computer: it runs the installer ({installer} next to this
file) again, with its backups.

Usage: carl-sync.py COMMAND

  watch           The sync service. It keeps one connection to the dashboard API
                  and applies each new config. No port opens on this computer.
                  {service_managers} keeps it running ({installer} sets
                  that up).
  once            Check one time and apply a new config. Without the service,
                  OpenCode and Pi run this when they start.
  apply           Apply the config that waits.
  auto on|off     on (the default): apply new configs at once. off: new configs
                  wait until you apply them.
  status          Show the state as JSON. The /carl panel in OpenCode and Pi
                  reads it.
  register on|off For {installer}: the sync service is installed (on) or not
                  (off). Without the service, OpenCode and Pi check for a new
                  config when they start.
  set KEY=VALUE [KEY=VALUE ...]
                  Change switches of the setup and write the configs again with
                  the models that this computer has now (no new config from the
                  dashboard). The /carl panel uses it. The keys:
                    NO_CODER, NO_BACKGROUND_SUBAGENTS, NO_REMINDER, NO_BROWSER,
                    NO_LSP, NO_SIDEBAR, NO_SWITCHER, NO_CACHE, NO_MODEL_CHECK:
                      1 turns the part off, on turns it on again.
                    WEB_SEARCH: exa, parallel or off.
                    CODER: 1 (the coder also with 1 slot) or auto (with 2
                      slots or more). /carl sets CODER=1 when you turn the
                      coder on.
                    CODER_THINKING: MODEL:VALUE, the coder's thinking with
                      that model on this computer (over the dashboard's
                      Coder thinking). VALUE: main (same as main), on, off,
                      low, medium or xhigh; default removes the model's
                      value (the dashboard's applies again). The other
                      models keep theirs.
                    CODER_MODEL: PROVIDER/MODEL, an external model of
                      the client for the coder (it then uses no slot of
                      the server), or main (the main session's model,
                      the default).
                    CODER_MODEL_THINKING: the thinking level (Pi) or
                      variant (OpenCode) of that external model; default
                      removes it (the model's own default). A new
                      CODER_MODEL removes it too.
                    CODER_TESTS: after (the default), before or off: when
                      CARL's test session runs for new code (before:
                      test-first, needs a full spec). OpenCode and Pi read
                      it at each coder task (no restart).
                    CODER_REQUEST_CHECK: reminder (the default), on or off:
                      the check that the brief carries the literals of
                      your request (no restart).
                    CODER_RUN_GATE: on (the default) or off: CARL checks
                      the coder's work after each code session.
                    CODER_FIX_ROUNDS: 0, 1 (the default), 2 or 3: the fix
                      rounds that the run gate starts by itself.
                  It shows the result as JSON: what changed, which client
                  (OpenCode, Pi) must restart, and the server's slots (as the
                  installer read them; null when the server does not answer).
                  Exit code 2: a key or a value that it does not know (it then
                  changes nothing).

The server's address comes from remote.json next to this file. The server writes
it at every start. Before the key goes out, the dashboard API must answer
/carl/ping (no key) with the ID in remote.json; when it moved (its port was
taken, or the server's port changed), the next ports are asked the same way,
and remote.json takes the place found. A server that moved to another port: the
installer runs again, so OpenCode and Pi use the new address. The API key comes from api-key next to this file, or from
~/.config/carl/api-key. A sync applies again the switches of the last install
(the clients, the coder, web search, LSP, browser and the others, in
~/.config/carl/client-install.env: {setup_command} and "set" write them).
So a sync changes only what the server decides: the installed models.
State: ~/.config/carl/client-sync.json. The installer's output:
~/.config/carl/client-sync.log.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..domain import remote
from ..domain import switches as sw
from ..domain.thinking import Thinking
from ..ports import DashboardApi, Installer, Lock, SyncStore

BACKOFF = (5, 10, 30, 60)
PING_WORKERS = 8                  # the ports asked at once for a moved dashboard API
# What can go wrong when the dashboard is away or answers badly (the service then tries again).
NET_ERRORS: Tuple[type, ...] = (OSError, ValueError)
INSTALL_ERRORS = (OSError, RuntimeError, subprocess.SubprocessError)

Json = Dict[str, Any]


@dataclass(frozen=True)
class Host:
    """This computer as the dashboard's Clients list shows it."""
    name: str
    user: str
    system: str          # "Darwin arm64"


class Sync:
    """The sync of one client folder (here: the folder of carl-sync.py) over its ports. mode: how this run reaches the
    dashboard: "service" (watch) or "check" (once)."""

    def __init__(self, here: str, store: SyncStore, api: DashboardApi, installer: Installer, lock: Lock,
                 server: remote.Server, thinking: Thinking, host: Host, environ: Mapping[str, str],
                 net_errors: Tuple[type, ...] = NET_ERRORS, words: Optional[Mapping[str, str]] = None) -> None:
        self.here = here
        self.store = store
        self.api = api
        self.installer = installer
        self.lock = lock
        self.server_rules = server
        self.thinking = thinking
        self.host = host
        self.environ = environ
        self.net_errors = net_errors
        self.words = words if words is not None else os_words()
        self.mode = "check"

    # -- the state and the switches
    def state(self) -> Json:
        return self.store.state()

    def update(self, **kv: Any) -> Json:
        return self.store.update(**kv)

    def install_env(self) -> Dict[str, str]:
        """The switches of the last install (KEY=value lines; only the known keys)."""
        return sw.parse_env(self.store.install_lines())

    def coder_model(self) -> str:
        """The coder's model on this computer (Phase 23.4.5; the dashboard's Clients list): "main" (the main session's
        model, on CARL's server) or the external PROVIDER/MODEL of client-install.env's CODER_MODEL."""
        v = self.install_env().get(sw.CODER_MODEL, "")
        return v if sw.CODER_MODEL_RE.fullmatch(v) and len(v) <= 200 else sw.CODER_MODEL_MAIN

    def who(self) -> str:
        """This client, for the dashboard's Clients list (X-Carl-Client): a stable id, the host name, the user,
        the system, the config it has, auto-apply, and whether the service runs."""
        st = self.state()
        if not st.get("id"):
            st = self.update(id=uuid.uuid4().hex[:12])
        return json.dumps({"id": st["id"], "host": self.host.name[:60], "user": self.host.user[:40],
                           "os": self.host.system[:40], "applied": st.get("applied") or "",
                           "auto_apply": st.get("auto_apply", True), "mode": self.mode,
                           "coder_model": self.coder_model()}, separators=(",", ":"))

    # -- the server
    def find_api(self, api: str, doc: Mapping[str, Any]) -> str:
        """The dashboard API: where remote.json says, else (it moved: its port was taken, or the server's default
        port changed) on the next ports after the server's and after CARL's default, asked without the key for the
        ID this folder knows; the place found goes into remote.json. Without a known ID nothing is looked for."""
        want = remote.carl_id(doc)
        if remote.is_ours(self.api.ping(api), want):
            return api
        cands = remote.candidates(api, doc, self.server_rules)
        if cands:
            # PING_WORKERS at a time, the first in order wins: a closed port answers at once on macOS and Linux, but
            # Windows tries a refused connection again for about a second, so one by one would take up to a minute
            pool = ThreadPoolExecutor(max_workers=min(PING_WORKERS, len(cands)))
            pings: List[Any] = []
            try:
                pings = [pool.submit(self.api.ping, cand, 1.0) for cand in cands]
                for cand, ping in zip(cands, pings):
                    if remote.is_ours(ping.result(), want):
                        self.store.write_remote(cache_api=cand)
                        return cand
            finally:
                for ping in pings:
                    ping.cancel()                      # the ports not asked yet are not asked
                pool.shutdown(wait=False)
        raise self.api.no_api(api)

    def server_word(self) -> str:
        """The server's computer in a sentence (data/words.json)."""
        from ..adapters import package_data
        return package_data.server_words()["server"]

    def server(self) -> Tuple[str, str]:
        """(the dashboard's API, the key); ValueError when this folder has no remote.json; URLError when no CARL
        dashboard answers there (the key is never sent to another program: find_api)."""
        doc = self.store.remote()
        api = remote.api_of(doc, self.server_word())
        key = self.store.key()
        if not key:
            raise ValueError(remote.no_key_text(self.server_word()))
        return self.find_api(api, doc), key

    def server_moved(self, api: str, key: str) -> bool:
        """The server's port now (the dashboard's /carl/client/server) against remote.json's; when it moved,
        remote.json takes the new one (the installer then writes the clients' address again). False when the
        dashboard is older."""
        port = remote.moved_port(self.api.server(api, key, self.who()), self.store.remote())
        if port is None:
            return False
        self.store.write_remote(port=port)
        return True

    def fetch_config(self, api: str, key: str, etag: str) -> Optional[Mapping[str, Any]]:
        return self.api.config(api, key, self.who(), etag)

    # -- applying
    def apply(self, doc: Mapping[str, Any]) -> None:
        """Write the installed models and run the installer again (its backups, its merge rules)."""
        self.store.write_models(doc["models"])
        env = {**self.environ, **self.install_env(), "NO_PROFILE": "1", "CARL_SYNC": "1"}
        rc = self.installer.run(env, f"client config {doc['version']}")
        if rc != 0:
            raise RuntimeError(f"The installer stopped with an error (exit code {rc}). Its output is in "
                               f"{self.installer.log}.")

    def set_switches(self, want: Dict[str, str]) -> Json:
        """Change the switches in client-install.env, then run the installer again with the models this folder has
        (installed-models.json: no new config). One at a time with a sync (the same lock). The result: what changed,
        ok or the error, and which client must restart."""
        with self.lock.hold(self.store.sync_lock):
            before = self.install_env()
            want = sw.planned(want, before, self.thinking.coder_values)
            self.store.write_install(sw.env_text(self.store.install_lines(), want))
            after = self.install_env()
            changed = {k: {"from": sw.shown(k, before), "to": sw.shown(k, after)} for k in want
                       if sw.shown(k, before) != sw.shown(k, after)}
            # not NO_PROFILE: a switch may need the shell profile's line for OpenCode's tool switches, as ./setup adds it
            env = {**self.environ, **after, "CARL_SYNC": "1"}
            for k, v in want.items():
                if sw.drops_line(k, v):
                    env.pop(k, None)                     # a switch from the environment must not undo the change
            restart = sw.restart_after(want, changed, after)
            result: Json = {"set": want, "changed": changed, "ok": True, "error": None, "restart": restart,
                            "new_terminal": sw.needs_new_terminal(restart, want), "slots": None}
            # the slot count the installer reads for its coder rule (/props total_slots): /carl warns when it turns the
            # coder on and the server runs 1 slot
            slots_out = self.store.slots_file()
            env["CARL_SLOTS_OUT"] = slots_out
            try:
                rc = self.installer.run(env, f"set {' '.join(f'{k}={v}' for k, v in want.items())}")
                if rc != 0:
                    raise RuntimeError(f"The installer stopped with an error (exit code {rc}). Its output is in "
                                       f"{self.installer.log}.")
            except INSTALL_ERRORS as e:
                result.update(ok=False, error=self.api.describe(e), restart=[], new_terminal=False)
            result["slots"] = self.store.read_slots(slots_out)
            return result

    def once(self, apply_waiting: bool = False) -> Json:
        """Check the published config; apply a new one (auto-apply), or keep it waiting."""
        with self.lock.hold(self.store.sync_lock):     # one sync at a time
            st = self.state()
            try:
                api, key = self.server()
                moved = self.server_moved(api, key)        # the server's port changed: the clients' address too
                doc = self.fetch_config(api, key, "" if apply_waiting or moved else str(st.get("applied") or ""))
            except self.net_errors as e:
                return self.update(checked=time.time(), error=self.api.describe(e))
            if self.api.server_version():                  # /carl compares the client package with it
                st = self.update(server_version=self.api.server_version())
            if doc is None or (doc["version"] == st.get("applied") and not moved):
                return self.update(checked=time.time(), error=None, pending=None)
            if not (st.get("auto_apply", True) or apply_waiting or moved):
                return self.update(checked=time.time(), error=None, pending=doc["version"])
            try:
                self.apply(doc)
            except INSTALL_ERRORS as e:
                return self.update(checked=time.time(), error=self.api.describe(e), pending=doc["version"])
            done = self.update(checked=time.time(), error=None, pending=None, applied=doc["version"],
                               applied_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            try:                                           # tell the dashboard at once (its Clients list)
                self.fetch_config(api, key, doc["version"])
            except self.net_errors:
                pass
            return done

    def report(self, api: str, key: str) -> None:
        """Tell the dashboard this client's state (who(): its config, the coder's model) with a check of the config
        it has (a 304 as a rule); a new config is not applied here (the dashboard sends its event for that)."""
        try:
            self.fetch_config(api, key, str(self.state().get("applied") or ""))
        except self.net_errors:
            pass

    def watch(self) -> None:
        """The service: listen, apply on a push, reconnect (5 s, 10 s, 30 s, then every minute)."""
        self.mode = "service"
        self.update(service=True, bundle=self.here)
        fails = 0
        while True:
            try:
                api, key = self.server()
                told = self.who()                          # what the stream's request told the dashboard
                for name in self.api.events(api, key, told):
                    self.update(alive=time.time(), connected=True)
                    fails = 0
                    if name == "config":
                        self.once()
                    elif self.who() != told:               # a /carl change (the coder's model): tell the dashboard
                        self.report(api, key)
                    told = self.who()
                self.update(connected=False)               # the dashboard closed the stream (it stopped)
            except self.net_errors as e:
                self.update(connected=False, error=self.api.describe(e))
            time.sleep(BACKOFF[min(fails, len(BACKOFF) - 1)])
            fails += 1


def os_words(platform: str = sys.platform) -> Mapping[str, str]:
    """The words of this OS in the help text (data/words.json, sync: installer, service_managers, setup_command)."""
    from ..adapters import package_data
    return package_data.sync_words(platform)


def help_text(words: Mapping[str, str]) -> str:
    """The help of carl-sync.py: this module's docstring with each {word} written out for this OS."""
    text = __doc__ or ""
    for key, value in words.items():
        text = text.replace("{" + key + "}", value)
    return text


def main(argv: List[str], sync: Sync) -> int:
    cmd = argv[0] if argv else "status"
    if cmd in ("-h", "--help", "help"):
        print(help_text(sync.words))
    elif cmd == "watch":
        sync.watch()
    elif cmd == "once":
        print(json.dumps(sync.once(), indent=1))
    elif cmd == "apply":
        print(json.dumps(sync.once(apply_waiting=True), indent=1))
    elif cmd == "auto" and len(argv) == 2 and argv[1] in ("on", "off"):
        print(json.dumps(sync.update(auto_apply=argv[1] == "on"), indent=1))
    elif cmd == "register" and len(argv) == 2 and argv[1] in ("on", "off"):
        sync.update(service=argv[1] == "on", bundle=sync.here)
    elif cmd == "set":
        try:
            want = sw.parse_set(argv[1:], sync.thinking.coder_values)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        result = sync.set_switches(want)
        print(json.dumps(result, indent=1))
        return 0 if result["ok"] else 1
    elif cmd == "status":
        print(json.dumps({**sync.state(), "bundle": sync.here}, indent=1))
    else:
        print(help_text(sync.words), file=sys.stderr)
        return 2
    return 0


def os_lock(platform: str = sys.platform) -> Lock:
    """The Lock adapter for this OS: msvcrt's lock on Windows, flock on macOS and Linux (each imports a module that
    only its OS has)."""
    if platform == "win32":
        from ..adapters.lock_msvcrt import MsvcrtLock
        return MsvcrtLock()
    from ..adapters.lock_fcntl import FcntlLock
    return FcntlLock()


def build(here: str) -> Sync:
    """The sync of the client folder `here` with this computer's adapters (the lock for this OS)."""
    import getpass
    import platform
    import socket

    from ..adapters import package_data
    from ..adapters.dashboard_http import NET_ERRORS as HTTP_ERRORS, DashboardHttp
    from ..adapters.install_sh import InstallSh
    from ..adapters.sync_files import SyncFiles
    from . import install
    conf = os.path.join(os.path.expanduser("~"), ".config", "carl")
    lock = os_lock()
    store = SyncFiles(conf, here, lock)
    host = Host(socket.gethostname(), getpass.getuser(), f"{platform.system()} {platform.machine()}")
    return Sync(here, store, DashboardHttp(), InstallSh(here, store.log, install.step), lock, package_data.server(),
                package_data.thinking().thinking, host, os.environ, HTTP_ERRORS)
