"""The Settings tab's actions and keys: the drop-downs and questions, and each panel's (Server,
Models, Auto fit, Auto-tune, Router, Caching). The card edit form: card_actions.py."""
from __future__ import annotations

from typing import Callable, List

from carl_core.domain.tuning import DEPTHS, as_depth

from . import diskcache, fsio
from .api import Endpoint
from .arrange import FILTERS, SORTS
from .card_actions import CardActions
from .fmt import R, RED, home_short, size
from .jobs import ServerJobs
from .keys import BACKSPACE, DOWN, ENTER, ESC, LEFTKEY, PANEL_PASSTHROUGH, PGDN, PGUP, RIGHT, SCROLL_KEYS, UP
from .model import ModelInfo, ServerData
from .settings import Pending, SettingsService, parse_typed, step_choice
from .settings_panels.caching import AUTO_CHOICES, DISK_CHOICES
from .settings_view import SettingsView
from .state import (SP_CACHE, SP_FIT, SP_MODELS, SP_ROUTER, SP_SERVER, SP_TUNE, SUBPANELS, TUNE_ALL, Confirm, PickItem,
                    Picker, TextPrompt, UIState)

MODEL_KEYS = {"\r": "museit", "\n": "museit", "d": "mdl", "v": "mverify", "x": "mdelete", "h": "mhf", "c": "mcancel",
              "u": "mtune", "e": "medit", "s": "msort", "f": "mfilter",
              "S": "msort-", "F": "mfilter-"}
ARRANGE_KEYS = {"s": "msort", "S": "msort-", "f": "mfilter", "F": "mfilter-"}   # every model list
FIT_KEYS = {"\r": "fuse", "\n": "fuse", "d": "fdl", "g": "fgoal", "f": "fscope"}
TUNE_ALL_LABEL = "all downloaded models, one after the other"
# Actions without a prefix the controller routes here by itself (controller.Controller.do).
SETTINGS_ACTIONS = ("msort", "mfilter", "msort-", "mfilter-", "msortpick", "mfilterpick",
                    "museit", "mdl", "mverify", "mdelete", "mdelyes", "mhf", "mcancel", "mtune", "mautodl", "medit",
                    "tprev", "tnext", "tpick", "tquick", "trun", "tyes", "tcancel", "tclear", "fuse", "fdl", "fgoal",
                    "fscope")
CACHE_KEYS = {"d": "cache:disk", "p": "cache:prefix", "s": "cache:sessions", "o": "cache:save", "t": "cache:auto",
              "w": "cache:swa", "h": "cache:share",
              "c": "cache:clear"}
TUNE_KEYS = {"\r": "trun", "\n": "trun", RIGHT: "tnext", LEFTKEY: "tprev", "c": "tcancel", " ": "tquick"}


class SettingsActions:
    """Acts on the Settings tab. snapshot() is the latest data the app collected; run(action) runs
    an action the way a click does (a question's Yes)."""

    def __init__(self, ui: UIState, svc: SettingsService, jobs: ServerJobs, view: SettingsView, endpoint: Endpoint,
                 home: str, snapshot: Callable[[], ServerData], run: Callable[[str], None],
                 scroll: Callable[[int], None]) -> None:
        self.ui = ui
        self.svc = svc
        self.models = svc.models
        self.store = svc.store
        self.jobs = jobs
        self.view = view
        self.endpoint = endpoint
        self.home = home
        self.snapshot = snapshot
        self.run = run
        self.scroll = scroll
        self.card = CardActions(ui, svc, view)

    def pending_init(self, d: ServerData) -> Pending:
        """The Server panel's starting values; a config.json that can't be read is shown and skipped."""
        try:
            cfg = self.store.load_config()
        except Exception as e:      # carl.ConfigError or a broken file: say so, start from the defaults
            self.ui.toast(f"{RED}config.json: {e}{R}", 15)
            cfg = self.store.empty_config()
        return self.svc.pending_init(d, cfg)

    # ------------------------------------------------------------ actions
    def action(self, act: str) -> None:
        """An action of the Settings panels, the drop-downs or the questions."""
        if act.startswith(("pick", "c2no", "sp:", "card")):
            self.dialog_action(act)
        elif act.startswith(("m", "smodel:")):
            self.models_action(act)
        elif act.startswith(("fgoal", "fscope", "fuse", "fdl")):
            self.fit_action(act)
        elif act.startswith("cache:"):
            self.cache_action(act[6:])
        elif act.startswith(("rmode", "rload:", "runload:")):
            self.router_action(act)
        elif act.startswith("t"):
            self.tune_action(act)
        else:
            self.server_action(act)

    def dialog_action(self, act: str) -> None:
        """The drop-down (pick:N selects, pickok / pickno), a question's Cancel (c2no), the panel bar
        (sp:N) and the card edit form (card...)."""
        ui = self.ui
        if act.startswith("pick:"):
            if ui.picker:
                ui.picker.sel = int(act[5:])
        elif act == "pickok":
            self.picker_choose()
        elif act == "pickno":
            ui.picker = None
        elif act == "c2no":
            ui.confirm2 = None
        elif act.startswith("sp:"):
            ui.sp = int(act[3:])
        elif act.startswith("card") and ui.card:
            self.card.button(act, ui.card)

    def models_action(self, act: str) -> None:
        """The Models panel (and the Server panel's model list: smodel:NAME; the sort and filter of
        every model list)."""
        ui, d = self.ui, self.snapshot()
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
        if act == "mautodl":                            # Auto fit's pick: download it, show it in the Models panel
            c, ui.confirm2 = ui.confirm2, None
            if c and c.model:
                self.jobs.start_download(c.model)
                ui.sp = SP_MODELS
                ui.mrow = next((i for i, x in enumerate(self.visible()) if x["name"] == c.model), ui.mrow)
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
        if act == "mcancel":
            if ui.dl:
                self.jobs.cancel_download()
            return
        ms = self.visible()
        m = ms[min(ui.mrow, len(ms) - 1)] if ms else None
        if m is None:
            return
        if act == "museit":
            chosen = ui.pending if ui.pending is not None else self.pending_init(d)
            ui.pending = chosen
            chosen["model"] = m["name"]
            self.svc.load_profile(chosen, m["name"])
            ui.sp, ui.set_row = SP_SERVER, 0            # the model row
            ui.toast(f"{m['name']} selected: press a to start it" + ("" if m["status"] == "downloaded" else " after the download"), 6)
        elif act == "mdl":
            self.jobs.start_download(m["name"])
        elif act == "mverify":
            self.jobs.verify(m["name"])
        elif act == "mdelete":
            if d.cmd and m["path"] in d.cmd:
                ui.toast("that model is loaded: stop the server first", 6)
                return
            ui.confirm2 = Confirm("DELETE?", [f"Delete {m['name']} ({size(m['bytes'])})?", home_short(m["path"], self.home)],
                                  "mdelyes", m["name"])
        elif act == "mtune":
            ui.tune_model, ui.sp = m["name"], SP_TUNE
        elif act == "medit":
            self.card.open(m)

    def fit_action(self, act: str) -> None:
        """The Auto fit panel: the goal and the model set (fgoal / fscope), Use this, Download it."""
        ui = self.ui
        if act.startswith(("fgoal", "fscope")):
            self.auto_choice(act)
        elif act == "fuse":
            if ui.pending is None:
                ui.pending = self.pending_init(self.snapshot())
            self.auto_fit(ui.pending)
            if ui.confirm2 is None:                     # downloaded: the Server panel shows the plan
                ui.sp = SP_SERVER
        elif act == "fdl":
            fit = self.svc.auto_fit(ui.pending or {})
            if fit and fit.pick and not fit.pick.downloaded:
                self.jobs.start_download(fit.pick.name)
            else:
                ui.toast("auto fit's pick is already downloaded", 5)

    def tune_action(self, act: str) -> None:
        """The Auto-tune panel: the model (tprev / tnext / tpick), the mode (tquick, tdepth:MODE), a run
        (trun, tyes after the question, tcancel) and tclear (the tuned values apply again)."""
        ui = self.ui
        if act in ("tprev", "tnext"):
            names = [x["name"] for x in self.models.downloaded()] + [TUNE_ALL]
            if len(names) > 1:
                i = names.index(ui.tune_model) if ui.tune_model in names else 0
                ui.tune_model = names[(i + (1 if act == "tnext" else -1)) % len(names)]
        elif act == "tpick":
            items: List[PickItem] = [(x["name"], x) for x in self.models.downloaded()]
            ui.picker = Picker("AUTO-TUNE WHICH MODEL?", items + [(TUNE_ALL_LABEL, TUNE_ALL_LABEL)], "picktune")
        elif act == "tquick":                           # quick -> default -> long -> quick
            ui.tune_depth = DEPTHS[(DEPTHS.index(as_depth(ui.tune_depth)) + 1) % len(DEPTHS)]
        elif act.startswith("tdepth:"):
            ui.tune_depth = as_depth(act[7:])
        elif act == "trun":
            self.jobs.run_tune(self.snapshot())
        elif act == "tyes":
            ui.confirm2 = None
            self.jobs.run_tune(self.snapshot(), confirmed=True)
        elif act == "tcancel" and ui.tune:
            self.jobs.cancel_tune()
        elif act == "tclear":
            try:
                cfg = self.store.load_config()
                (cfg.get("models") or {}).pop(ui.tune_model, None)
                self.store.save_config(cfg)
            except Exception as e:      # config.json unreadable or not writable: say so
                ui.toast(f"{RED}config.json: {e}{R}", 10)
                return
            ui.pending = None
            ui.toast(f"{ui.tune_model}: the tuned values apply (the config.json overrides are cleared)", 8)

    def server_action(self, act: str) -> None:
        """The Server panel: a row (setrow:N), a value (setinc:N / setdec:N), the model drop-down
        (setpick), Revert, Tuned values, Auto fit, Apply and its question."""
        ui, d, svc = self.ui, self.snapshot(), self.svc
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
        ui, d = self.ui, self.snapshot()
        if act.startswith("rmode:"):
            mode = act[6:]
            if mode not in ("single", "router"):
                return
            running = "router" if d.router is not None else "single" if d.up else None
            what = ("OpenCode / Pi switch models (router mode)" if mode == "router" else
                    "the dashboard picks the model (single model)")
            ui.confirm2 = Confirm("MODEL SWITCHING?", [
                f"Switch to: {what}. CARL saves llama.mode = {mode} in config.json.",
                "CARL also updates the OpenCode / Pi configs on this Mac (if CARL set them up here).",
                ("The server restarts in this mode now. Requests in progress stop, and the model loads again."
                 if running and running != mode else "The next server start uses it."),
                *(["Router mode: the router offers all downloaded models that fit. The client configs must list them "
                   "all (update them in the Connect tab).",
                   "WARNING: Each switch empties the prompt cache. The model that loads starts cold. OpenCode and Pi "
                   "restore a session from the disk cache about one second after the load (Settings > Caching). Other "
                   "clients read the full conversation again. For a long conversation, this takes minutes. Switch with "
                   "this in consideration."]
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
        if act.startswith(("rload:", "runload:")):
            self.jobs.router_load(act.split(":", 1)[1], d, unload=act.startswith("runload:"))

    def cache_action(self, act: str) -> None:
        """The Caching panel: disk[:GB] (the next limit, or that one), prefix / sessions / share
        [:on|off], save[:MODE], auto[:S] and swa[:MODE] (the next one, or that one), saved to
        config.json "cache" at once; clear asks, clearyes removes every saved state (diskcache.py)."""
        ui, jobs = self.ui, self.jobs
        folder = jobs.paths.slots
        if act == "clear":
            files = diskcache.listing(folder)
            if not files:
                ui.toast("the disk cache is empty", 5)
                return
            ui.confirm2 = Confirm("CLEAR THE DISK CACHE?", [
                f"This removes {len(files)} saved prompt states ({diskcache.gb(diskcache.used(files))}) from "
                f"{home_short(folder, self.home)}: the pre-read prompts of OpenCode and the saved conversations.",
                "The server keeps the states that it holds now. After the next start, the server reads each prompt "
                "again when a client uses it first."],
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
            text = f"auto: CARL saves after {new} s of unsaved read time"
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
        jobs.cache_conf(fresh=True)
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
            ui.pending = self.pending_init(self.snapshot())
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
                "Download it now? The Models panel shows the progress. After the download, press a to start it.",
                "No: the settings stay as they are. A start is possible only after the download."], "mautodl",
                fit.pick.name)
        else:
            ui.toast(f"auto fit: {fit.pick.name}, {fit.plan.label()} chosen: press a to start it", 8)

    # ------------------------------------------------------------ the model lists
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
            ui.toast(f"{m['name']} is not downloaded. To download it, press ] for the Models panel, then d.", 8)

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

    # ------------------------------------------------------------ the Server panel's rows
    def selected_key(self) -> str:
        """The key of the selected Server-panel row."""
        rws = self.svc.rows(self.ui.pending or {})
        return rws[min(self.ui.set_row, len(rws) - 1)].key

    def commit_edit(self, key: str) -> None:
        """Settings: store a typed value (Enter) if it is a valid number for that row."""
        ui = self.ui
        value, err = parse_typed(key, ui.edit or "")
        ui.edit = None
        if value is None:
            ui.toast(err, 5)
        elif ui.pending is not None:
            ui.pending[key] = value

    def server_list_keys(self, rest: str) -> bool:
        """Keys for the Server panel's model list: m focuses it (↑↓ Enter, m / Esc leave); s / f sort
        and filter it (and every model list)."""
        ui = self.ui
        items = ["auto"] + [m["name"] for m in self.visible()]
        if rest == "m" or (ui.slist and rest == ESC):
            ui.slist = not ui.slist
        elif rest in ARRANGE_KEYS:
            self.action(ARRANGE_KEYS[rest])
        elif ui.slist and UP in rest:
            ui.srow = max(ui.srow - 1, 0)
        elif ui.slist and DOWN in rest:
            ui.srow = min(ui.srow + 1, len(items) - 1)
        elif ui.slist and rest in ENTER and items:
            self.choose_model(items[min(ui.srow, len(items) - 1)])
            ui.slist = False
        return True

    # ------------------------------------------------------------ keys
    def keys(self, rest: str) -> bool:
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
                self.action(ARRANGE_KEYS[rest])
                ui.picker = self.view.model_picker(cur, ui.msort, ui.mfilter, ui.pending)
            elif rest in ENTER:
                self.picker_choose()
            elif rest == ESC:
                ui.picker = None
            return True
        if ui.confirm2:
            for ch in rest:
                if ch in "yY":
                    self.run(ui.confirm2.yes)
                    break
                if ch in "nN" + ESC:
                    ui.confirm2 = None
                    break
            return True
        if ui.card and ui.sp == SP_MODELS:
            return self.card.keys(rest)
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
                self.action(MODEL_KEYS[rest])
                return True
            return rest not in PANEL_PASSTHROUGH and not rest.isdigit()
        if ui.sp == SP_FIT:
            for seq, step in SCROLL_KEYS.items():
                if seq in rest:
                    self.scroll(step * (1 if abs(step) > 1 else 3))
                    return True
            if rest in FIT_KEYS:
                self.action(FIT_KEYS[rest])
                return True
            return rest not in PANEL_PASSTHROUGH and not rest.isdigit() and rest != "A"
        if ui.sp == SP_ROUTER:
            return rest not in PANEL_PASSTHROUGH and not rest.isdigit()
        if ui.sp == SP_CACHE:
            if rest in CACHE_KEYS:
                self.action(CACHE_KEYS[rest])
                return True
            return rest not in PANEL_PASSTHROUGH and not rest.isdigit()
        if ui.sp == SP_TUNE:
            if rest in TUNE_KEYS:
                self.action(TUNE_KEYS[rest])
                return True
            return rest not in PANEL_PASSTHROUGH and not rest.isdigit()
        if ui.sp == SP_SERVER and ui.pending is not None and (ui.slist or rest == "m" or rest in ARRANGE_KEYS):
            return self.server_list_keys(rest)
        if ui.sp == SP_SERVER and ui.pending is not None and rest in ENTER and self.selected_key() == "model":
            self.open_model_picker()
            return True
        return False
