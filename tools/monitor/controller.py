"""Turns keys and clicks into actions on the UI state: tabs, card levels, the quit dialog,
the Settings panels (rows, picker, typed values, models, Auto-tune) and the client configs."""
from __future__ import annotations

import json
import os
import signal
import time
from typing import Callable, Dict, List, NamedTuple, Optional, cast

from . import diskcache, fsio, system
from .api import FETCH_ERRORS, Endpoint
from carl_core.domain.cards import editable_card
from carl_core.domain.tuning import DEPTHS, as_depth
from carl_core.domain.types import ModelInfo as CoreModelInfo

from .card_form import CardForm
from .clients import LABELS, config_text, fill_template, masked, model_list, served
from .collector import SHAPE_ERRORS
from .fmt import R, RED, home_short, size
from .jobs import ServerJobs
from .keys import (BACKSPACE, DOWN, END, END_ALT, ENTER, ESC, LEFT, LEFTKEY, PGDN, PGUP, RIGHT, UP, WHEEL_DOWN,
                   WHEEL_UP, Click, split_mouse, strip_escapes)
from .logtail import LogTail
from .model import JSONDict, ModelInfo, ServerData, clean
from .settings import NUMERIC, Pending, SettingsService, parse_typed, step_choice
from .arrange import FILTERS, SORTS
from .settings_view import AUTO_CHOICES, DISK_CHOICES, SettingsView
from .state import (CONNECT_SUBPANELS, TUNE_ALL, SP_CACHE, SP_FIT, SP_MODELS, SP_ROUTER, SP_SERVER, SP_TUNE, SUBPANELS, TABS, Confirm, PickItem, Picker, TextPrompt,
                    UIState)
from .store import ModelList

TEMPLATES = {"opencode": "opencode/opencode.json", "pi": "pi/models.json"}     # client config templates in client/
SETTINGS_ACTIONS = ("msort", "mfilter", "msort-", "mfilter-", "msortpick", "mfilterpick",
                    "museit", "mdl", "mverify", "mdelete", "mdelyes", "mhf", "mcancel", "mtune", "mautodl", "medit",
                    "tprev", "tnext", "tpick", "tquick", "trun", "tyes", "tcancel", "tclear", "fuse", "fdl", "fgoal",
                    "fscope")
MODEL_KEYS = {"\r": "museit", "\n": "museit", "d": "mdl", "v": "mverify", "x": "mdelete", "h": "mhf", "c": "mcancel",
              "u": "mtune", "e": "medit", "s": "msort", "f": "mfilter",
              "S": "msort-", "F": "mfilter-"}
ARRANGE_KEYS = {"s": "msort", "S": "msort-", "f": "mfilter", "F": "mfilter-"}   # every model list
FIT_KEYS = {"\r": "fuse", "\n": "fuse", "d": "fdl", "g": "fgoal", "f": "fscope"}
TUNE_ALL_LABEL = "all downloaded models, one after another"
CACHE_KEYS = {"d": "cache:disk", "p": "cache:prefix", "s": "cache:sessions", "o": "cache:save", "t": "cache:auto",
              "w": "cache:swa", "h": "cache:share",
              "c": "cache:clear"}
TUNE_KEYS = {"\r": "trun", "\n": "trun", RIGHT: "tnext", LEFTKEY: "tprev", "c": "tcancel", " ": "tquick"}
SCROLL_KEYS = {UP: 1, DOWN: -1, PGUP: 10, PGDN: -10}
PANEL_PASSTHROUGH = ("q", "Q", "\x03", "\t")       # keys the Models / Auto-tune panels leave to the app
READ_ONLY = ("catalogue models are read-only: only custom models (Hugging Face downloads and files in the models "
             "folder) have a card you can edit")


class Region(NamedTuple):
    """A clickable screen area: 1-based row and columns (x1 exclusive)."""
    y: int
    x0: int
    x1: int
    action: str


class Controller:
    """Acts on input. data is the latest snapshot and regions the clickable areas of the
    last frame; the app keeps both current."""

    def __init__(self, ui: UIState, svc: SettingsService, jobs: ServerJobs, view: SettingsView, endpoint: Endpoint,
                 tail: LogTail, repo: str, home: str) -> None:
        self.ui = ui
        self.svc = svc
        self.models: ModelList = svc.models
        self.store = svc.store
        self.jobs = jobs
        self.view = view
        self.endpoint = endpoint
        self.tail = tail
        self.repo = repo
        self.home = home
        self.data = ServerData()
        self.regions: List[Region] = []

    def pending_init(self, d: ServerData) -> Pending:
        """The Server panel's starting values; a config.json that can't be read is shown and skipped."""
        try:
            cfg = self.store.load_config()
        except Exception as e:      # carl.ConfigError or a broken file: say so, start from the defaults
            self.ui.toast(f"{RED}config.json: {e}{R}", 15)
            cfg = self.store.empty_config()
        return self.svc.pending_init(d, cfg)

    # ------------------------------------------------------------ client configs
    def preview_text(self, kind: str, d: ServerData, mask: bool = False) -> str:
        """Config text for the running server. mask=True hides the key (on-screen preview)."""
        if not d.up:
            return "(the server isn't reachable yet: the config appears once the model has loaded)"
        ep = self.endpoint
        rel = TEMPLATES.get(kind)
        templates = {}
        if rel:     # read each time: install.sh may update the bundle while the monitor runs
            templates[kind] = fill_template(fsio.read_text(os.path.join(self.repo, "client", rel)), ep.host, ep.port, self.home)
        s = served(d, self.alias_from_server)
        text = config_text(kind, s, templates, ep.base, ep.key, model_list(self.models.client_list(), s))
        return masked(text, ep.key) if mask else text

    def alias_from_server(self) -> Optional[str]:
        """The first model id of /v1/models."""
        try:
            return clean(str(json.loads(self.endpoint.get("/v1/models"))["data"][0]["id"]))
        except FETCH_ERRORS + SHAPE_ERRORS:
            return None

    def show_config(self, kind: str) -> None:
        """Show a config on the Connect tab and copy it to the clipboard."""
        ui = self.ui
        ui.preview, ui.prev_scroll, ui.tab = kind, 0, 1
        ui.install_shown = False                # the preview takes the installer's place
        if not self.data.up:
            ui.toast("server not reachable yet: nothing copied")
            return
        ok = system.copy_to_clipboard(self.preview_text(kind, self.data))
        ui.copied = kind if ok else None
        ui.toast(LABELS[kind] + (" copied to the clipboard" if ok else ": clipboard unavailable, select it on screen"))

    def install_action(self, act: str) -> None:
        """The Connect tab's installer: insall / insconfig ask first, insyes runs it, insno cancels
        the question, insshow / insclose show / hide its output, inscancel stops it."""
        ui = self.ui
        ui.tab = 1
        running = bool(ui.install and not ui.install.done)
        if act == "inspush":
            self.jobs.push_client_config()
            return
        if act in ("insall", "insconfig"):
            if running:
                ui.install_shown = True
                ui.toast("the installer is already running: its output is below", 5)
            else:
                ui.install_ask = "all" if act == "insall" else "config"
        elif act == "insyes" and ui.install_ask:
            config_only, ui.install_ask = ui.install_ask == "config", None
            self.jobs.start_install(config_only)
        elif act == "insno":
            ui.install_ask = None
        elif act == "insshow":
            ui.install_shown = bool(ui.install)
        elif act == "insclose":
            ui.install_shown = False
        elif act == "inscancel" and running:
            self.jobs.cancel_install()
            ui.toast("installer stopped: running it again is safe (backups stay)", 8)

    # ------------------------------------------------------------ actions
    def do(self, action: str) -> None:
        """Run an action: a clicked region's or button's, or one a key stands for."""
        ui, d = self.ui, self.data
        if action.startswith(("set", "sp:", "pick", "mrow:", "smodel:", "msortset:", "mfilterset:", "c2no", "card",
                              "fgoal:", "fscope:", "rmode", "rload:", "runload:", "tdepth:", "cache:")) \
                or action in SETTINGS_ACTIONS:
            self.settings_action(action)
        elif action.startswith("level:"):
            nm = action[6:]
            if nm in ui.levels:
                ui.levels[nm] = (ui.levels[nm] + 1) % 3
        elif action.startswith("tab:"):
            ui.tab = int(action[4:])
        elif action == "quit":
            ui.quit = True
        elif action == "cancel":
            ui.quit = False
        elif action == "key":
            ui.key_shown = not ui.key_shown
        elif action in LABELS:
            self.show_config(action)
        elif action.startswith("ins"):
            self.install_action(action)
        elif action.startswith("csp:"):
            ui.connect_sp = int(action[4:])
        elif action == "clforget":
            n = self.jobs.forget_clients(7 * 86400)
            ui.toast(f"forgot {n} client(s) not seen for a week", 6)
        elif action == "wrap":
            ui.wrap = not ui.wrap
        elif action == "errors":
            ui.errors_only = not ui.errors_only
            ui.log_scroll = 0
        elif action == "follow":
            ui.log_scroll = 0
        elif action == "detach":
            pid = d.target_pid
            if pid and not d.exited:
                ui.exit_msg = (f"Monitor closed; the server is still running (pid {pid}) at {self.endpoint.base}.\n"
                               f"  re-attach: ./carl.sh monitor --port {self.endpoint.port}\n  stop:      kill {pid}")
            raise SystemExit
        elif action == "stop":
            pid = d.target_pid
            if pid:
                def stop() -> None:
                    self.jobs.save_before_stop(self.data)   # the sessions in the slots (save = auto, switch, stop)
                    system.kill(pid, signal.SIGTERM)
                    ui.stopping = (pid, time.time() + 30)
                self.jobs.when_idle(d, "stop the server", stop)
            ui.quit = False
        elif action == "drain:now" and ui.drain:
            go, ui.drain = ui.drain.go, None
            go()
        elif action == "drain:wait" and ui.drain:
            ui.drain.waiting, ui.drain.since = True, time.time()
        elif action == "drain:cancel":
            ui.drain = None

    def settings_action(self, act: str) -> None:
        """Actions of the Settings panels, the picker and the confirmations."""
        ui, d, svc = self.ui, self.data, self.svc
        # ---- picker, confirmations, panels
        if act.startswith("pick:"):
            if ui.picker:
                ui.picker.sel = int(act[5:])
            return
        if act == "pickok":
            self.picker_choose()
            return
        if act == "pickno":
            ui.picker = None
            return
        if act == "c2no":
            ui.confirm2 = None
            return
        if act.startswith("sp:"):
            ui.sp = int(act[3:])
            return
        if act.startswith("card"):                      # the card edit form's rows and buttons
            if ui.card:
                self.card_button(act, ui.card)
            return
        # ---- models panel
        if act.startswith("mrow:"):
            ui.mrow = int(act[5:])
            return
        if act.startswith("smodel:"):                    # the Server panel's model list
            self.choose_model(act[7:])
            return
        if act in ("msortpick", "mfilterpick"):         # a drop-down of every sort / filter
            ui.picker = self.view.arrange_picker("sort" if act == "msortpick" else "filter", ui.msort, ui.mfilter)
            return
        if act.startswith(("msort", "mfilter")):        # msort / msort- (step), msortset:N (choose)
            kind = "sort" if act.startswith("msort") else "filter"
            if ":" in act:
                self.set_arrangement(kind, int(act.split(":", 1)[1]))
            else:
                cur = ui.msort if kind == "sort" else ui.mfilter
                self.set_arrangement(kind, cur + (-1 if act.endswith("-") else 1))
            return
        ms = self.visible()
        m = ms[min(ui.mrow, len(ms) - 1)] if ms else None
        if act == "museit" and m:
            chosen = ui.pending if ui.pending is not None else self.pending_init(d)
            ui.pending = chosen
            chosen["model"] = m["name"]
            svc.load_profile(chosen, m["name"])
            ui.sp, ui.set_row = SP_SERVER, 0            # the model row
            ui.toast(f"{m['name']} selected: press a to start it" + ("" if m["status"] == "downloaded" else " once it is downloaded"), 6)
            return
        if act == "mdl" and m:
            self.jobs.start_download(m["name"])
            return
        if act == "mautodl":                            # Auto fit's pick: download it, show it in the Models panel
            c, ui.confirm2 = ui.confirm2, None
            if c and c.model:
                self.jobs.start_download(c.model)
                ui.sp = SP_MODELS
                ui.mrow = next((i for i, x in enumerate(self.visible()) if x["name"] == c.model), ui.mrow)
            return
        if act == "mverify" and m:
            self.jobs.verify(m["name"])
            return
        if act == "mdelete" and m:
            if d.cmd and m["path"] in d.cmd:
                ui.toast("that model is loaded: stop the server first", 6)
                return
            ui.confirm2 = Confirm("DELETE?", [f"Delete {m['name']} ({size(m['bytes'])})?", home_short(m["path"], self.home)],
                                  "mdelyes", m["name"])
            return
        if act == "mdelyes":
            c, ui.confirm2 = ui.confirm2, None
            mm = self.models.by_name(c.model) if c else None
            if mm:
                try:
                    self.store.delete(mm)
                    ui.toast(f"deleted {mm['name']} · update the OpenCode / Pi lists: Connect tab, u", 8)
                except Exception as e:  # a file in use, permissions, ...: say so, keep running
                    ui.toast(f"{RED}delete failed: {e}{R}", 10)
                self.models.get(refresh=True)
            return
        if act == "mhf":
            ui.text = TextPrompt("Hugging Face repo (OWNER/REPO, or a URL to a .gguf):", "hf")
            return
        if act == "mcancel" and ui.dl:
            self.jobs.cancel_download()
            return
        if act == "mtune" and m:
            ui.tune_model, ui.sp = m["name"], SP_TUNE
            return
        if act == "medit" and m:
            self.open_card(m)
            return
        # ---- auto fit panel
        if act.startswith(("fgoal", "fscope")):
            self.auto_choice(act)
            return
        if act == "fuse":
            if ui.pending is None:
                ui.pending = self.pending_init(d)
            self.auto_fit(ui.pending)
            if ui.confirm2 is None:                     # downloaded: the Server panel shows the plan
                ui.sp = SP_SERVER
            return
        if act == "fdl":
            fit = svc.auto_fit(ui.pending or {})
            if fit and fit.pick and not fit.pick.downloaded:
                self.jobs.start_download(fit.pick.name)
            else:
                ui.toast("auto fit's pick is already downloaded", 5)
            return
        # ---- caching panel
        if act.startswith("cache:"):
            self.cache_action(act[6:])
            return
        # ---- router panel
        if act.startswith(("rmode", "rload:", "runload:")):
            self.router_action(act)
            return
        # ---- auto-tune panel
        if act in ("tprev", "tnext"):
            names = [x["name"] for x in self.models.downloaded()] + [TUNE_ALL]
            if len(names) > 1:
                i = names.index(ui.tune_model) if ui.tune_model in names else 0
                ui.tune_model = names[(i + (1 if act == "tnext" else -1)) % len(names)]
            return
        if act == "tpick":
            items: List[PickItem] = [(x["name"], x) for x in self.models.downloaded()]
            ui.picker = Picker("AUTO-TUNE WHICH MODEL?", items + [(TUNE_ALL_LABEL, TUNE_ALL_LABEL)], "picktune")
            return
        if act == "tquick":                             # quick -> default -> long -> quick
            ui.tune_depth = DEPTHS[(DEPTHS.index(as_depth(ui.tune_depth)) + 1) % len(DEPTHS)]
            return
        if act.startswith("tdepth:"):
            ui.tune_depth = as_depth(act[7:])
            return
        if act == "trun":
            self.jobs.run_tune(d)
            return
        if act == "tyes":
            ui.confirm2 = None
            self.jobs.run_tune(d, confirmed=True)
            return
        if act == "tcancel" and ui.tune:
            self.jobs.cancel_tune()
            return
        if act == "tclear":
            try:
                cfg = self.store.load_config()
                (cfg.get("models") or {}).pop(ui.tune_model, None)
                self.store.save_config(cfg)
            except Exception as e:      # config.json unreadable or not writable: say so
                ui.toast(f"{RED}config.json: {e}{R}", 10)
                return
            ui.pending = None
            ui.toast(f"{ui.tune_model}: the tuned values apply (config.json overrides cleared)", 8)
            return
        # ---- server panel
        p = ui.pending
        if p is None:
            return
        rws = svc.rows(p)
        if act == "setpick":
            ui.set_row = 1
            self.open_model_picker()
        elif act.startswith("setrow:"):
            ui.set_row = min(int(act[7:]), len(rws) - 1)
        elif act.startswith(("setinc:", "setdec:")):
            i = min(int(act[7:]), len(rws) - 1)
            ui.set_row = i
            row = rws[i]
            if row.choices:
                p[row.key] = step_choice(row.choices, p[row.key], 1 if act.startswith("setinc") else -1)
            if row.key == "model":
                svc.load_profile(p, str(p["model"]))
        elif act == "setrevert":
            ui.pending = None
        elif act == "setdefaults":
            ui.pending = svc.defaults_for(p)
        elif act == "setautofit":
            ui.sp = SP_FIT
        elif act == "setnofit":
            ui.toast("this setup does not fit or is not downloaded: see Status (fit)", 6)
        elif act == "setapply" and not ui.restart:
            if not svc.fit_cached(p)[0]:
                ui.toast("this setup does not fit or is not downloaded: see Status (fit)", 6)
                return
            if d.cmd and "llama-server" not in d.cmd:
                ui.toast("another server (not llama.cpp) uses this port: stop it first", 8)
                return
            ui.confirm = True
        elif act == "setno":
            ui.confirm = False
        elif act == "setyes":
            ui.confirm = False
            self.jobs.restart(p, d)

    def router_action(self, act: str) -> None:
        """The Router panel: rmode:MODE asks to switch the model switching mode, rmodeyes saves it
        (llama.mode) and restarts a running server in it; rload:ID / runload:ID load or unload a
        router's model (in the background: a load takes 30 s to 2 min)."""
        ui, d = self.ui, self.data
        if act.startswith("rmode:"):
            mode = act[6:]
            if mode not in ("single", "router"):
                return
            running = "router" if d.router is not None else "single" if d.up else None
            what = ("OpenCode / Pi switch models (router mode)" if mode == "router" else
                    "the dashboard picks the model (single model)")
            ui.confirm2 = Confirm("MODEL SWITCHING?", [
                f"Switch to: {what}. Saved as llama.mode = {mode} in config.json.",
                "The OpenCode / Pi configs on this Mac are updated with it (when CARL set them up here).",
                ("The server restarts in this mode now (requests in progress stop; the model loads again)."
                 if running and running != mode else "The next server start uses it."),
                *(["Router mode: every downloaded model that fits is offered; the clients' configs list them all "
                   "(Connect tab: update them).",
                   "WARNING: every switch empties the prompt cache: the model that loads starts cold. OpenCode and Pi "
                   "put a session back from the disk cache (about a second after the load, Settings > Caching); other "
                   "clients re-read the whole conversation (minutes for a long one). Switch with this in consideration."]
                  if mode == "router" else [])],
                "rmodeyes", mode)
            return
        if act == "rmodeyes":
            c, ui.confirm2 = ui.confirm2, None
            target = c.model if c else None
            if target not in ("single", "router"):
                return
            mode = str(target)
            try:
                cfg = self.store.load_config()
                llama = cfg.setdefault("llama", {})
                if mode == "single":
                    llama.pop("mode", None)
                else:
                    llama["mode"] = mode
                self.store.save_config(cfg)
            except Exception as e:      # config.json unreadable or not writable: say so
                ui.toast(f"{RED}config.json: {e}{R}", 10)
                return
            running = "router" if d.router is not None else "single" if d.up else None
            # the clients on this Mac follow: their configs are updated (after the restart: the
            # installer reads the new server), when CARL set them up here
            update = bool(fsio.installed_here(self.endpoint.base, self.home))
            if running and running != mode and not ui.restart:
                if ui.pending is None:
                    ui.pending = self.pending_init(d)
                ui.install_after_restart = update
                self.jobs.restart(ui.pending, d)
            else:
                ui.toast(f"saved: llama.mode = {mode}" + ("" if running == mode else " (the next start uses it)"), 8)
                if update:
                    self.jobs.start_install(config_only=True)
            return
        name = act.split(":", 1)[1]
        self.jobs.router_load(name, d, unload=act.startswith("runload:"))

    def cache_action(self, act: str) -> None:
        """The Caching panel: disk[:GB] (the next limit, or that one), prefix[:on|off],
        sessions[:on|off], save[:MODE] and swa[:MODE] (the next one, or that one), saved to
        config.json "cache" at once; clear asks,
        clearyes removes every saved state (diskcache.py)."""
        ui, jobs = self.ui, self.jobs
        folder = jobs.paths.slots
        if act == "clear":
            files = diskcache.listing(folder)
            if not files:
                ui.toast("the disk cache is empty", 5)
                return
            ui.confirm2 = Confirm("CLEAR THE DISK CACHE?", [
                f"Removes {len(files)} saved prompt states ({diskcache.gb(diskcache.used(files))}) from "
                f"{home_short(folder, self.home)}: OpenCode's pre-read prompts and the saved conversations.",
                "The server keeps what it holds now; after the next start, each one is read again on first use."],
                "cache:clearyes")
            return
        if act == "clearyes":
            ui.confirm2 = None
            diskcache.remove(folder, [f.name for f in diskcache.listing(folder)])
            ui.toast("disk cache cleared", 6)
            return
        conf = jobs.cache_conf(fresh=True)
        key, _, value = act.partition(":")
        new: object
        if key == "disk":
            gbs = sorted({*DISK_CHOICES, conf.disk_gb})
            new = int(value) if value.isdigit() else gbs[(gbs.index(conf.disk_gb) + 1) % len(gbs)]
            text = f"disk limit: {new} GB"
        elif key in ("prefix", "sessions", "share"):
            cur = {"prefix": conf.prefix, "sessions": conf.sessions, "share": conf.share}[key]
            new = value == "on" if value else not cur
            text = f"{dict(prefix='prompts', sessions='saved conversations', share='shared pieces')[key]}: {'on' if new else 'off'}"
        elif key == "auto":
            autos = sorted({*AUTO_CHOICES, conf.auto_s})
            new = int(value) if value.isdigit() else autos[(autos.index(conf.auto_s) + 1) % len(autos)]
            text = f"auto saves after {new} s of unsaved reading"
        elif key in ("save", "swa"):
            opts = diskcache.SAVES if key == "save" else diskcache.SWAS
            mode = conf.save if key == "save" else conf.swa
            new = value if value in opts else opts[(opts.index(mode) + 1) % len(opts)]
            text = (f"save: {new}" if key == "save" else f"SWA models: {new} (applies at the next start)")
        else:
            return
        try:
            cfg = self.store.load_config()
            sec = cfg.setdefault("cache", {})
            sec[{"disk": "disk_gb", "auto": "auto_s"}.get(key, key)] = new
            self.store.save_config(cfg)
        except Exception as e:      # config.json unreadable or not writable: say so
            ui.toast(f"{RED}config.json: {e}{R}", 10)
            return
        conf = jobs.cache_conf(fresh=True)
        if key == "disk":
            before = len(diskcache.listing(folder))
            jobs.trim_cache()
            gone = before - len(diskcache.listing(folder))
            text += f" ({gone} oldest saved states removed to fit)" if gone else ""
        ui.toast(f"{text} (saved)", 8)

    def auto_choice(self, act: str) -> None:
        """The Auto fit panel's goal / scope: fgoal / fscope switch to the other one, fgoal:X /
        fscope:X choose X. Saved to config.json at once."""
        ui = self.ui
        if ui.pending is None:
            ui.pending = self.pending_init(self.data)
        p = ui.pending
        key = "goal" if act.startswith("fgoal") else "scope"
        opts = ("everyday", "hard-code") if key == "goal" else ("catalogue", "downloaded")
        value = act.split(":", 1)[1] if ":" in act else opts[1 - opts.index(str(p.get(key, opts[0])))]
        try:
            self.svc.save_auto_choice(p, key, value)
        except Exception as e:      # config.json unreadable or not writable, a bad value: say so
            ui.toast(f"{RED}auto fit: {e}{R}", 10)
            return
        fit = self.svc.auto_fit(p)
        ui.toast(f"auto fit {'goal' if key == 'goal' else 'from'}: {value} (saved) → "
                 + (fit.summary() if fit else "unavailable"), 8)

    def auto_fit(self, p: Pending) -> None:
        """Auto fit (A): the pick for this Mac with the goal and scope rows, its context, slots and
        KV, in one step; a pick that isn't downloaded is offered for download."""
        ui = self.ui
        fit = self.svc.apply_auto_fit(p)
        if fit is None:
            ui.toast(f"{RED}auto fit unavailable: {self.models.fit_error or 'no model list'}{R}", 10)
        elif fit.pick is None or fit.plan is None:
            ui.toast(f"{RED}auto fit: {fit.because()}{R}", 12)
        elif not fit.pick.downloaded:
            m = self.models.by_name(fit.pick.name)
            ui.confirm2 = Confirm("DOWNLOAD?", [
                f"Auto fit picked {fit.pick.name} ({size(m['bytes']) if m else '?'}) for this Mac: {fit.plan.label()}.",
                f"Why: {fit.because()}.", "",
                "Download it now? The Models panel shows the progress; press a to start it once it is here.",
                "No: the settings stay chosen (a start needs the download first)."], "mautodl", fit.pick.name)
        else:
            ui.toast(f"auto fit: {fit.pick.name}, {fit.plan.label()} chosen: press a to start it", 8)

    def server_list_keys(self, rest: str) -> bool:
        """Keys for the Server panel's model list: m focuses it (↑↓ Enter, m / Esc leave); s / f sort
        and filter it (and every model list)."""
        ui = self.ui
        items = ["auto"] + [m["name"] for m in self.visible()]
        if rest == "m" or (ui.slist and rest == ESC):
            ui.slist = not ui.slist
        elif rest in ARRANGE_KEYS:
            self.settings_action(ARRANGE_KEYS[rest])
        elif ui.slist and UP in rest:
            ui.srow = max(ui.srow - 1, 0)
        elif ui.slist and DOWN in rest:
            ui.srow = min(ui.srow + 1, len(items) - 1)
        elif ui.slist and rest in ENTER and items:
            self.choose_model(items[min(ui.srow, len(items) - 1)])
            ui.slist = False
        return True

    def set_arrangement(self, kind: str, index: int) -> None:
        """Set the model lists' sort or filter (index wraps), keeping the selected model when it is
        still listed; an open model drop-down is rebuilt in the new order."""
        ui = self.ui
        name = self.visible()[ui.mrow]["name"] if self.visible() else None
        if kind == "sort":
            ui.msort = index % len(SORTS)
        else:
            ui.mfilter = index % len(FILTERS)
        ms = self.visible()
        ui.mrow = next((i for i, x in enumerate(ms) if x["name"] == name), 0)

    def visible(self) -> List[ModelInfo]:
        """The Models panel's list: sorted and filtered as the user chose."""
        return self.view.visible(self.ui.msort, self.ui.mfilter)

    def open_model_picker(self) -> None:
        """Open the model drop-down on the pending model."""
        self.ui.picker = self.view.model_picker(str((self.ui.pending or {}).get("model", "auto")), self.ui.msort,
                                                self.ui.mfilter, self.ui.pending)

    def choose_model(self, name: str) -> None:
        """Make name the pending model and load its profile (the picker and the Server panel's list)."""
        ui = self.ui
        if ui.pending is None:
            return
        ui.pending["model"] = name
        self.svc.load_profile(ui.pending, name)
        m = self.models.by_name(self.svc.resolved_model(ui.pending))
        if m and m["status"] != "downloaded":
            ui.toast(f"{m['name']} is not downloaded: press ] for the Models panel, then d to download it", 8)

    def picker_choose(self) -> None:
        """Use the picker's selection: a model for the Server panel, a file to download, a model to tune."""
        ui = self.ui
        pk, ui.picker = ui.picker, None
        if pk is None:
            return
        val = pk.items[pk.sel][0]
        if pk.on_pick == "pickmodel":
            self.choose_model(str(val))
        elif pk.on_pick == "pickhf" and ui.hf:
            self.jobs.start_download(f"hf:{ui.hf.repo}/{val}")
        elif pk.on_pick == "picktune":
            ui.tune_model = TUNE_ALL if val == TUNE_ALL_LABEL else val
        elif pk.on_pick == "pickcard" and ui.card:
            ui.card.add_pick(str(val))
        elif pk.on_pick in ("picksort", "pickfilter"):
            self.set_arrangement("sort" if pk.on_pick == "picksort" else "filter", int(str(val)))
            if pk.reopen:                               # back to the model drop-down it came from
                ui.picker = self.view.model_picker(pk.reopen, ui.msort, ui.mfilter, ui.pending)

    def commit_edit(self, key: str) -> None:
        """Settings: store a typed value (Enter) if it is a valid number for that row."""
        ui = self.ui
        value, err = parse_typed(key, ui.edit or "")
        ui.edit = None
        if value is None:
            ui.toast(err, 5)
        elif ui.pending is not None:
            ui.pending[key] = value

    # ------------------------------------------------------------ input
    def settings_keys(self, rest: str) -> bool:
        """Keys in the Settings tab. Returns True when the input was used here."""
        ui = self.ui
        if ui.text:
            tx = ui.text
            for ch in rest:
                if ch in ENTER:
                    v = tx.value.strip()
                    ui.text = None
                    if v and tx.on_enter == "hf":
                        self.jobs.hf_lookup(v)
                    return True
                if ch == ESC:
                    ui.text = None
                    return True
                if ch in BACKSPACE:
                    tx.value = tx.value[:-1]
                elif ch.isprintable():
                    tx.value += ch
            return True
        if ui.picker:
            pk = ui.picker
            if UP in rest:
                pk.sel = max(pk.sel - 1, 0)
            elif DOWN in rest:
                pk.sel = min(pk.sel + 1, len(pk.items) - 1)
            elif PGUP in rest:
                pk.sel = max(pk.sel - 10, 0)
            elif PGDN in rest:
                pk.sel = min(pk.sel + 10, len(pk.items) - 1)
            elif rest in ARRANGE_KEYS and pk.on_pick == "pickmodel":
                cur = str(pk.items[pk.sel][0])
                self.settings_action(ARRANGE_KEYS[rest])
                ui.picker = self.view.model_picker(cur, ui.msort, ui.mfilter, ui.pending)
            elif rest in ENTER:
                self.picker_choose()
            elif rest == ESC:
                ui.picker = None
            return True
        if ui.confirm2:
            for ch in rest:
                if ch in "yY":
                    self.do(ui.confirm2.yes)
                    break
                if ch in "nN" + ESC:
                    ui.confirm2 = None
                    break
            return True
        if ui.card and ui.sp == SP_MODELS:
            return self.card_keys(rest)
        if rest in ("[", "]"):
            ui.sp = (ui.sp + (1 if rest == "]" else -1)) % len(SUBPANELS)
            return True
        if ui.sp == SP_MODELS:
            if UP in rest:
                ui.mrow = max(ui.mrow - 1, 0)
                return True
            if DOWN in rest:
                ui.mrow = max(min(ui.mrow + 1, len(self.visible()) - 1), 0)
                return True
            if rest in MODEL_KEYS:
                self.settings_action(MODEL_KEYS[rest])
                return True
            return rest not in PANEL_PASSTHROUGH and not rest.isdigit()
        if ui.sp == SP_FIT:
            for seq, step in SCROLL_KEYS.items():
                if seq in rest:
                    self.scroll(step * (1 if abs(step) > 1 else 3))
                    return True
            if rest in FIT_KEYS:
                self.settings_action(FIT_KEYS[rest])
                return True
            return rest not in PANEL_PASSTHROUGH and not rest.isdigit() and rest != "A"
        if ui.sp == SP_ROUTER:
            return rest not in PANEL_PASSTHROUGH and not rest.isdigit()
        if ui.sp == SP_CACHE:
            if rest in CACHE_KEYS:
                self.settings_action(CACHE_KEYS[rest])
                return True
            return rest not in PANEL_PASSTHROUGH and not rest.isdigit()
        if ui.sp == SP_TUNE:
            if rest in TUNE_KEYS:
                self.settings_action(TUNE_KEYS[rest])
                return True
            return rest not in PANEL_PASSTHROUGH and not rest.isdigit()
        if ui.sp == SP_SERVER and ui.pending is not None and (ui.slist or rest == "m" or rest in ARRANGE_KEYS):
            return self.server_list_keys(rest)
        if ui.sp == SP_SERVER and ui.pending is not None and rest in ENTER and self.selected_key() == "model":
            self.open_model_picker()
            return True
        return False

    # ------------------------------------------------------------ card edit mode
    def open_card(self, m: ModelInfo) -> None:
        """e on a model: the edit form for a custom model's card; a catalogue model's is read-only.
        A card without arch / quant starts with what the GGUF header says."""
        ui = self.ui
        if not m.get("custom"):
            ui.toast(READ_ONLY, 8)
            return
        values: JSONDict = dict(editable_card(cast(CoreModelInfo, m)))     # the same record, carl.py's type
        notes = {} if "label" in values else {"label": f"(the file name: {os.path.basename(m['path'])})"}
        if m["status"] == "downloaded" and not ("arch" in values and "quant" in values and "thinking" in values):
            try:
                info = self.store.header_info(m["path"])
            except Exception:           # an unreadable header: the user fills them in
                info = {}
            for key in ("arch", "quant", "thinking"):
                if key not in values and info.get(key) not in (None, "", "?"):
                    values[key] = info[key]
                    notes[key] = "(from the GGUF header)"
        ui.card = CardForm(m["name"], values, notes)

    def card_keys(self, rest: str) -> bool:
        """Keys of the card edit form. While a text is typed every key is text (Enter keeps it, Esc
        drops it); else ↑↓ select, Enter edits, ← → / space change, x clears, s saves, Esc cancels.
        True when the input was used here."""
        ui = self.ui
        f = ui.card
        if f is None:
            return False
        if f.typing is not None:
            if rest == ESC:
                f.cancel_typing()
                return True
            text = strip_escapes(rest)              # arrow keys, bracketed-paste markers
            for i, ch in enumerate(text):
                if ch in ENTER and i == len(text) - 1:
                    f.commit()
                elif ch in ENTER or ch == "\t":
                    f.type_text(" ")                # a pasted line break
                elif ch in BACKSPACE:
                    f.backspace()
                elif ch.isprintable():
                    f.type_text(ch)
            return True
        if rest == ESC:
            self.close_card(f)
        elif UP in rest or DOWN in rest:
            f.move(-1 if UP in rest else 1)
        elif RIGHT in rest or LEFTKEY in rest or rest == " ":
            f.cycle(-1 if LEFTKEY in rest else 1)
        elif rest in ENTER:
            if f.enter():
                self.open_card_picker(f)
        elif rest == "x":
            f.clear()
        elif rest == "s":
            self.save_card(f)
        elif rest in ("[", "]"):
            ui.toast("press s to save the card or Esc to cancel it first", 5)
        else:
            return rest not in PANEL_PASSTHROUGH and not rest.isdigit()
        return True

    def card_button(self, act: str, f: CardForm) -> None:
        """A click in the form: a row selects it; Save, Cancel, Edit field, Clear field."""
        if act.startswith("cardrow:"):
            f.select(int(act[8:]))
        elif act == "cardsave":
            self.save_card(f)
        elif act == "cardcancel":
            if f.typing is not None:
                f.cancel_typing()
            else:
                self.close_card(f)
        elif act == "cardenter":
            if f.typing is not None:
                f.commit()
            elif f.enter():
                self.open_card_picker(f)
        elif act == "cardclear" and f.typing is None:
            f.clear()

    def close_card(self, f: CardForm) -> None:
        """Esc / Cancel: leave the form; nothing is saved."""
        self.ui.card = None
        self.ui.toast("card edit cancelled: nothing saved" if f.changed else "card edit closed", 5)

    def open_card_picker(self, f: CardForm) -> None:
        """The model drop-down for a new pick-instead entry (every model but this one)."""
        items: List[PickItem] = [(m["name"], m) for m in self.visible_all() if m["name"] != f.model]
        self.ui.picker = Picker("PICK INSTEAD: WHICH MODEL?", items, "pickcard",
                                foot="Press ↑ ↓ to select the alternative and Enter to choose it (then type when it "
                                     "is the better pick), Esc to go back to the card.")

    def visible_all(self) -> List[ModelInfo]:
        """Every model, in the lists' sort order (no filter)."""
        return self.view.visible(self.ui.msort, 0)

    def save_card(self, f: CardForm) -> None:
        """s / Save: keep a text being typed, check and store the card; errors stay in the form.
        The lists, the MODEL card, sort and filters read it at once."""
        ui = self.ui
        if not f.commit():
            return
        try:
            self.store.save_card(f.model, f.card())
        except Exception as e:      # carl.ConfigError (a rule the card breaks), models.json unwritable
            msg, head = str(e), f"{f.model}: card: "
            f.error = msg[len(head):] if msg.startswith(head) else msg
            return
        ui.card = None
        self.models.get(refresh=True)
        ui.mrow = next((i for i, x in enumerate(self.visible()) if x["name"] == f.model), ui.mrow)
        ui.toast(f"card saved for {f.model}: the lists and its MODEL card show it", 6)

    def selected_key(self) -> str:
        """The key of the selected Server-panel row."""
        rws = self.svc.rows(self.ui.pending or {})
        return rws[min(self.ui.set_row, len(rws) - 1)].key

    def scroll(self, step: int) -> None:
        """Up / down (step > 0 = up): moves the Settings row by one, else scrolls the tab's list."""
        ui = self.ui
        if ui.tab == 4 and ui.sp == SP_FIT:
            ui.fit_scroll = max(0, ui.fit_scroll - step)
        elif ui.tab == 4:
            if abs(step) != 1:                      # wheel / PgUp PgDn: scroll the Server panel (cards below the rows)
                ui.set_scroll = max(0, ui.set_scroll - step)
                return
            n = len(self.svc.rows(ui.pending)) if ui.pending else 1 + len(self.svc.schema.llama)
            ui.set_row = (ui.set_row - step) % n
            ui.set_scroll = 0                       # the rows are at the top: keep the selected one in view
        elif ui.tab == 3:
            ui.log_scroll = max(0, min(ui.log_scroll + step, max(len(self.tail.book.lines) - 5, 0)))
        elif ui.tab == 2:
            ui.req_scroll = max(0, ui.req_scroll - step)
        elif ui.tab == 1:
            ui.prev_scroll = max(0, ui.prev_scroll - step)
        else:
            ui.scroll = max(0, ui.scroll - step)

    def handle_click(self, c: Click) -> None:
        """A left click runs the action under it (only the dialog's, while one is open); the wheel scrolls."""
        ui = self.ui
        if c.button in (WHEEL_UP, WHEEL_DOWN):     # wheel: up = back in the log, up in lists
            self.scroll(3 if c.button == WHEEL_UP else -3)
            return
        if c.button != LEFT:
            return
        for r in self.regions:
            if r.y == c.y and r.x0 <= c.x < r.x1:
                if ui.drain and not r.action.startswith("drain:"):
                    return
                if ui.quit and r.action not in ("stop", "detach", "cancel", "quit"):
                    return
                if ui.confirm and r.action not in ("setyes", "setno"):
                    return
                self.do(r.action)
                return

    def handle_input(self, data: str) -> bool:
        """Act on keys and clicks. True = refresh the data now (space)."""
        ui = self.ui
        clicks, rest = split_mouse(data)
        for c in clicks:
            self.handle_click(c)
        if ui.drain:                              # a reply runs: y now, w wait, n / Esc cancel
            for ch in rest:
                if ch in "yYsS":
                    self.do("drain:now")
                    break
                if ch in "wW" and not ui.drain.waiting:
                    self.do("drain:wait")
                    break
                if ch in "nN" + ESC:
                    self.do("drain:cancel")
                    break
            return False
        if ui.confirm and not ui.quit:
            if rest == ESC:
                self.do("setno")
            for ch in rest:
                if ch in "yY":
                    self.do("setyes")
                elif ch in "nN":
                    self.do("setno")
            return False
        if ui.tab == 4 and not ui.quit and rest and ui.edit is None and self.settings_keys(rest):
            return False
        if ui.tab == 4 and ui.pending is not None and not ui.quit and ui.sp == SP_SERVER:
            key = self.selected_key()
            if ui.edit is not None:               # typing a value: digits . k, Backspace, Enter, Esc
                for ch in rest:
                    if ch in "0123456789.kK":
                        ui.edit += ch.lower()
                    elif ch in BACKSPACE:
                        ui.edit = ui.edit[:-1]
                    elif ch in ENTER:
                        self.commit_edit(key)
                        break
                    elif ch == ESC:
                        ui.edit = None
                        break
                return False
            if rest in ENTER and key in NUMERIC:
                ui.edit = ""
                return False
        if ui.quit:
            if rest == ESC:
                self.do("cancel")
            for ch in rest:
                if ch in "sS":
                    self.do("stop")
                elif ch in "dDqQ":
                    self.do("detach")
                elif ch in "nNcC":
                    self.do("cancel")
            return False
        for seq, step in SCROLL_KEYS.items():
            if seq in rest:
                self.scroll(step)
        if ui.tab == 4 and ui.pending is not None and ui.sp == SP_SERVER:
            if RIGHT in rest:
                self.do(f"setinc:{ui.set_row}")
            if LEFTKEY in rest:
                self.do(f"setdec:{ui.set_row}")
        if END in rest or END_ALT in rest:
            ui.log_scroll = 0
        if ui.tab == 1 and ui.install_ask and rest == ESC:
            self.do("insno")
            return False
        return self.keys(strip_escapes(rest))

    def keys(self, rest: str) -> bool:
        """Plain key presses. True = refresh now."""
        ui = self.ui
        simple: Dict[str, Callable[[], None]] = {
            "k": lambda: self.do("key"), "o": lambda: self.do("opencode"), "p": lambda: self.do("pi"),
            "t": lambda: self.do("curl"), "w": lambda: self.do("wrap"), "f": lambda: self.do("errors"),
            "e": lambda: ui.levels.update({x: 2 for x in ui.levels}), "c": lambda: ui.levels.update({x: 0 for x in ui.levels})}
        for ch in rest:
            if ch in "qQ\x03":
                self.do("quit")
            elif ch in "12345":
                ui.tab = int(ch) - 1
            elif ui.tab == 1 and ui.install_ask and ch in "yYnN":
                self.do("insyes" if ch in "yY" else "insno")
            elif ui.tab == 1 and ch in "iuxP":
                self.do({"i": "insall", "u": "insconfig", "x": "insclose", "P": "inspush"}[ch])
            elif ui.tab == 1 and ch in "[]":
                ui.connect_sp = (ui.connect_sp + 1) % len(CONNECT_SUBPANELS)
            elif ui.tab == 4 and ui.sp == SP_SERVER and ch in "arxA":
                self.do({"a": "setapply", "r": "setrevert", "x": "setdefaults", "A": "setautofit"}[ch])
            elif ch == "\t":
                ui.tab = (ui.tab + 1) % len(TABS)
            elif ch in simple:
                simple[ch]()
            elif ch in "+=":
                ui.lines = min(ui.lines + 2, 60)
            elif ch in "-_":
                ui.lines = max(ui.lines - 2, 2)
            elif ch == "?":
                ui.help = not ui.help
            elif ch == " ":
                return True
        return False
