"""The Settings tab's actions and keys: the drop-downs and questions, and each panel's (Server,
Models, Agents, Auto fit, Auto-tune, Router, Caching). Keys come one at a time (controller.Controller.key);
a text being typed takes a whole read (keys()). The card edit form: card_actions.py."""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from carl_core.domain.tuning import DEPTHS, as_depth
from carl_core.domain.units import file_size, memory

from . import diskcache, fsio
from .api import Endpoint
from .arrange import FILTERS, SORTS
from .card_actions import CardActions
from .fmt import R, RED, home_short, row
from .jobs import ServerJobs
from .keys import BACKSPACE, DOWN, ENTER, ESC, LEFTKEY, PGDN, PGUP, RIGHT, UP
from .model import ModelInfo, ServerData, draft_bytes
from .settings import (CODER_OFF_NOTE, NUMERIC, THINKING_LABELS, THINKING_ROWS, AgentsInfo, Pending, SettingsService,
                       coder_off, parse_typed, plan_words, shown_value, spec_value, step_choice)
from .settings_panels.agents import AGENT_ROWS
from .settings_panels.caching import AUTO_CHOICES, CACHE_ROWS, DISK_CHOICES
from .settings_view import SettingsView
from .state import (SP_AGENTS, SP_CACHE, SP_FIT, SP_MODELS, SP_ROUTER, SP_SERVER, SP_TUNE, SUBPANELS, TUNE_ALL, Confirm,
                    PickItem, Picker, TextPrompt, UIState)
from .words import plural, status_name

MODEL_KEYS = {"\r": "museit", "\n": "museit", "d": "mdl", "v": "mverify", "x": "mdelete", "h": "mhf", "c": "mcancel",
              "u": "mtune", "e": "medit", "s": "msort", "f": "mfilter", "S": "msort-", "F": "mfilter-", "t": "mspeeds"}
ARRANGE_KEYS = {"s": "msort", "S": "msort-", "f": "mfilter", "F": "mfilter-"}   # every model list
FIT_KEYS = {"\r": "fuse", "\n": "fuse", "d": "fdl", "g": "fgoal", "f": "fscope"}
TUNE_KEYS = {"\r": "trun", "\n": "trun", RIGHT: "tnext", LEFTKEY: "tprev", "c": "tcancel", " ": "tquick", "x": "tclear"}
SERVER_KEYS = {"a": "setapply", "r": "setrevert", "x": "setdefaults", "A": "setautofit"}
AGENTS_KEYS = {"r": "agents:undo", "x": "agents:rec"}
TUNE_ALL_LABEL = "All downloaded models, one after the other"
# Actions without a prefix the controller routes here by itself (controller.Controller.do).
SETTINGS_ACTIONS = ("msort", "mfilter", "msort-", "mfilter-", "msortpick", "mfilterpick",
                    "museit", "mspeeds", "mdl", "mverify", "mdelete", "mdelyes", "mhf", "mcancel", "mtune", "mautodl", "medit",
                    "tprev", "tnext", "tpick", "tquick", "trun", "tyes", "tcancel", "tclear", "fuse", "fdl", "fgoal",
                    "fscope")
CACHE_NAMES = {"disk": "disk limit", "prefix": "saved prompts", "sessions": "saved sessions", "save": "when CARL saves",
               "auto": "save after", "share": "shared storage", "swa": "Gemma models (sliding window)",
               "move": "other templates"}


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
            self.ui.toast(f"{RED}CARL cannot read config.json: {e}{R}", 15)
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
        elif act.startswith("agents:"):
            self.agents_action(act[7:])
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
        if act == "mspeeds":                             # t: the list as the speeds table, or back
            ui.mspeeds = not ui.mspeeds
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
        if act == "mautodl":                            # Auto fit's choice: download it, show it in the Models panel
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
                    ui.toast(f"Deleted {mm['name']}. OpenCode and Pi still list it: press u in the Connect tab.", 8)
                except Exception as e:  # a file in use, permissions, ...: say so, keep running
                    ui.toast(f"{RED}CARL cannot delete it: {e}{R}", 10)
                self.models.get(refresh=True)
            return
        if act == "mhf":
            ui.text = TextPrompt("Hugging Face repo (OWNER/REPO, or the address of a .gguf file):", "hf")
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
            ui.toast(f"{m['name']} is your choice in the Server panel. "
                     + ("Press a to start it." if m["status"] == "downloaded" else
                        "Download it first: the Models panel, d."), 6)
        elif act == "mdl":
            self.jobs.start_download(m["name"])
        elif act == "mverify":
            self.jobs.verify(m["name"])
        elif act == "mdelete":
            if d.cmd and m["path"] in d.cmd:
                ui.toast(f"{m['name']} is running now: CARL cannot delete it. Start another model first (Settings > "
                         f"Server).", 8, error=True)
                return
            lines = [row("model", f"{file_size(m['bytes']):>8}   {home_short(m['path'], self.home)}")]
            if m.get("draft"):
                lines.append(row("MTP drafter", f"{file_size(draft_bytes(m)):>8}   "
                                                f"{home_short(str(m.get('draft_path', '')), self.home)}"))
            lines += ["", "This cannot be undone. Then update OpenCode and Pi: the Connect tab, u."]
            ui.confirm2 = Confirm("DELETE THE MODEL?", lines, "mdelyes", m["name"], yes_label="Delete")
        elif act == "mtune":
            ui.tune_model, ui.sp = m["name"], SP_TUNE
        elif act == "medit":
            self.card.open(m)

    def fit_action(self, act: str) -> None:
        """The Auto fit panel: the goal and the candidates (fgoal / fscope), Use this, Download it."""
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
                ui.toast("Auto fit's choice is downloaded already.", 5)

    def tune_action(self, act: str) -> None:
        """The Auto-tune panel: the model (tprev / tnext / tpick), the length (tquick, tdepth:DEPTH), a run
        (trun, tyes after the question, tcancel) and tclear (the recommended settings apply again)."""
        ui = self.ui
        if act in ("tprev", "tnext"):
            if ui.tune and not ui.tune.done:
                ui.toast(f"Auto-tune is running for {ui.tune.model}. To change the model, cancel the run first (c).", 6)
                return
            names = [x["name"] for x in self.models.downloaded()] + [TUNE_ALL]
            if len(names) > 1:
                i = names.index(ui.tune_model) if ui.tune_model in names else 0
                ui.tune_model = names[(i + (1 if act == "tnext" else -1)) % len(names)]
        elif act == "tpick":
            items: List[PickItem] = [(x["name"], x) for x in self.models.downloaded()]
            ui.picker = Picker("AUTO-TUNE WHICH MODEL?", items + [(TUNE_ALL_LABEL, TUNE_ALL_LABEL)], "picktune")
        elif act == "tquick":                           # quick -> normal -> long -> quick
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
                ui.toast(f"{RED}CARL cannot save config.json: {e}{R}", 10)
                return
            ui.pending = None
            ui.toast(f"{ui.tune_model}: CARL uses the recommended settings again. Your changes for this model in "
                     f"config.json are removed.", 8)

    def not_ready(self, p: Pending) -> str:
        """Why Apply cannot start this setup ('' when it can)."""
        f = self.svc.fit_cached(p)
        if not f.known:
            return f"{f.name} is not a known model. Choose another model."
        if not f.downloaded:
            return f"{f.name} is not downloaded. To download it: the Models panel (]), then d."
        if f.error:
            return f"CARL cannot check the memory: {f.error}"
        if not f.fits:
            return "This setup does not fit this Mac (see Memory). Use a smaller context or fewer slots."
        return ""

    def server_action(self, act: str) -> None:
        """The Server panel: a row (setrow:N), a value (setinc:N / setdec:N), the model drop-down
        (setpick), Undo, the recommended settings, Auto fit, Apply and its question."""
        ui, d, svc = self.ui, self.snapshot(), self.svc
        p = ui.pending
        if p is None:
            return
        rws = svc.rows(p)
        if act == "setpick":
            ui.set_row = 0
            self.open_model_picker()
        elif act.startswith("setrow:"):
            ui.set_row = min(int(act[7:]), len(rws) - 1)
        elif act.startswith(("setinc:", "setdec:")):
            i = min(int(act[7:]), len(rws) - 1)
            ui.set_row = i
            row = rws[i]
            step = 1 if act.startswith("setinc") else -1
            if row.key == "spec" and row.choices:
                p["spec"], p["specn"] = str(step_choice(row.choices, spec_value(p), step)).split("|")
            elif row.choices:
                p[row.key] = step_choice(row.choices, p[row.key], step)
            if row.key == "model":
                svc.load_profile(p, str(p["model"]))
        elif act == "setrevert":
            ui.pending = None
        elif act == "setdefaults":
            ui.pending = svc.defaults_for(p)
        elif act == "setautofit":
            ui.sp = SP_FIT
        elif act == "setnofit":
            ui.toast(self.not_ready(p) or "This setup cannot start.", 8, error=True)
        elif act == "setapply" and not ui.restart:
            why = self.not_ready(p)
            if why:
                ui.toast(why, 8)
                return
            if d.cmd and "llama-server" not in d.cmd:
                ui.toast("Another server (not llama.cpp) uses this port. Stop it first.", 8)
                return
            ui.confirm = True
        elif act == "setno":
            ui.confirm = False
        elif act == "setyes":
            ui.confirm = False
            self.jobs.restart(p, d)

    # ------------------------------------------------------------ the Agents panel (Phase 23.4.4)
    def agents_model(self) -> str:
        """The model the Agents panel shows: the one chosen there, else the running model, else the Server panel's
        ("" when no model is known)."""
        ui, svc = self.ui, self.svc
        if ui.agents_model and svc.models.by_name(ui.agents_model):
            return ui.agents_model
        run = svc.running(self.snapshot()).get("model")
        if isinstance(run, str) and svc.models.by_name(run):
            return run
        name = svc.resolved_model(ui.pending or {"model": "auto"})
        return name if svc.models.by_name(name) else ""

    def agents_info(self) -> Optional[AgentsInfo]:
        """The shown model's thinking per role (None: no model is known)."""
        name = self.agents_model()
        return self.svc.agents(name) if name else None

    def agents_action(self, act: str) -> None:
        """The Agents panel: row:N selects a row, inc:N / dec:N change it (the model: the next one; a thinking row:
        saved at once), pick opens the model drop-down, undo puts back the values from before this session's changes,
        rec uses the recommended values."""
        ui = self.ui
        verb, _, n = act.partition(":")
        if verb == "row":
            ui.agents_row = max(0, min(int(n), len(AGENT_ROWS) - 1))
        elif verb == "pick":
            ui.agents_row = 0
            ui.picker = self.view.agents_picker(self.agents_model(), ui.msort, ui.mfilter)
        elif verb in ("inc", "dec"):
            ui.agents_row = max(0, min(int(n), len(AGENT_ROWS) - 1))
            step = 1 if verb == "inc" else -1
            key = AGENT_ROWS[ui.agents_row]
            if key == "model":
                names = [m["name"] for m in self.visible()]
                cur = self.agents_model()
                if names:
                    ui.agents_model = names[(names.index(cur) + step) % len(names)] if cur in names else names[0]
                return
            info = self.agents_info()
            if info is not None:
                self.agents_save(info, {key: str(step_choice(list(info.choices[key]), info.mine[key], step))})
        elif verb in ("undo", "rec"):
            info = self.agents_info()
            if info is None:
                return
            if verb == "undo":
                before = ui.agents_undo.get(info.name)
                if not before:
                    ui.toast(f"There is nothing to undo for {info.name}.", 5)
                    return
                self.agents_save(info, before, undo=True)
            else:
                self.agents_save(info, {k: None for k in THINKING_ROWS}, what="the recommended settings")

    def agents_save(self, info: AgentsInfo, want: Dict[str, Optional[str]], undo: bool = False,
                    what: str = "") -> None:
        """Save Main thinking and Coder thinking of the shown model (None: the recommended value) and say so; the
        values before the first change of this session are kept for r (undo)."""
        ui = self.ui
        try:
            saved = self.svc.thinking_saved(info.name)
            for key, value in want.items():
                self.svc.save_thinking(info.name, key, value)
        except Exception as e:      # config.json unreadable or not writable, a value the model does not take: say so
            ui.toast(f"{RED}CARL cannot save config.json: {e}{R}", 10)
            return
        if undo:
            ui.agents_undo.pop(info.name, None)
        else:
            ui.agents_undo.setdefault(info.name, saved)
        now = self.svc.agents(info.name)
        if what or undo:
            said = f"{info.name} uses {'its earlier settings' if undo else what} again"
        else:
            key = next(iter(want))
            said = f"{THINKING_LABELS[key]} of {info.name}: {shown_value(key, now.mine[key])}"
        if coder_off(now.mine):
            # the message line has two lines at most: the note alone after "Saved.", so its full-spec sentence is
            # never cut (the panel shows the model and the values; its text above says when OpenCode and Pi use it)
            ui.toast(f"Saved. {CODER_OFF_NOTE}", 12)
            return
        ui.toast(f"Saved. {said}. OpenCode and Pi use it after the next update (the Connect tab, u; other "
                 f"computers: P).", 12)

    def router_action(self, act: str) -> None:
        """The Router panel: rmode:MODE asks to switch between single model and router mode, rmodeyes
        saves it (llama.mode) and restarts a running server in it; rload:ID / runload:ID load or unload a
        router's model (in the background: a load takes 30 s to 2 min)."""
        ui, d = self.ui, self.snapshot()
        if act.startswith("rmode:"):
            mode = act[6:]
            if mode not in ("single", "router"):
                return
            running = "router" if d.router is not None else "single" if d.up else None
            now = ("The server restarts in this mode now. Requests in progress stop, and the model loads again."
                   if running and running != mode else "The next server start uses it.")
            here = bool(fsio.installed_here(self.endpoint.base, self.home))     # as rmodeyes decides
            configs = ("CARL updates the OpenCode and Pi configs on this Mac." if here else
                       "Update the OpenCode and Pi configs yourself (./carl.sh install --config-only): CARL did not "
                       "set them up on this Mac.")
            if mode == "router":
                lines = ["OpenCode and Pi can then switch the model. Each switch loads the model again: 30 s to 2 "
                         "minutes.", "",
                         "After a switch, the new model has none of the sessions in memory. A saved session belongs to "
                         "the model it ran on: when you switch back to that model, OpenCode and Pi restore it from the "
                         "disk cache (with Saved sessions on, and for a Gemma model the full cache). A session that continues on the new model is read again: minutes for a long "
                         "session.", "", configs, now]
                reg = getattr(self.jobs, "registry", None)
                if reg is not None and reg.list():                   # computers that sync their configs (#33)
                    lines.append("The client configs on other computers must list every model (the Connect tab, P).")
                title = "SWITCH TO ROUTER MODE?"
            else:
                lines = ["One model runs. You choose it in Settings > Server.", "", configs, now]
                title = "SWITCH TO A SINGLE MODEL?"
            if ui.full:
                lines += ["", row("config.json", "llama.mode = router" if mode == "router" else
                                  "llama.mode is removed (single is the default)")]
            ui.confirm2 = Confirm(title, lines, "rmodeyes", mode, yes_label="Switch")
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
                ui.toast(f"{RED}CARL cannot save config.json: {e}{R}", 10)
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
                name = "router mode" if mode == "router" else "a single model"
                ui.toast(f"Saved: {name}." + ("" if running == mode else " The next start uses it."), 8)
                if update:
                    self.jobs.start_install(config_only=True)
            return
        if act.startswith(("rload:", "runload:")):
            self.jobs.router_load(act.split(":", 1)[1], d, unload=act.startswith("runload:"))

    def cache_action(self, act: str) -> None:
        """The Caching panel: KEY (the next choice), KEY:VALUE (that one) or KEY:prev (the one before) for
        disk, prefix, sessions, share, save, auto and swa; saved to config.json "cache" at once. clear asks,
        clearyes removes every saved file (diskcache.py)."""
        ui, jobs = self.ui, self.jobs
        folder = jobs.paths.slots
        if act == "clear":
            files = diskcache.listing(folder)
            if not files:
                ui.toast("The disk cache is empty.", 5)
                return
            n = sum(1 for f in files if f.kind == diskcache.PROMPT)
            ui.confirm2 = Confirm("CLEAR THE DISK CACHE?", [
                row("saved prompts", str(n)), row("saved sessions", str(len(files) - n)),
                row("size", diskcache.gb(diskcache.used(files))), row("folder", home_short(folder, self.home)), "",
                "The server keeps what it holds now. After the next start, it reads each prompt again when a client "
                "uses it first."], "cache:clearyes", yes_label="Clear")
            return
        if act == "clearyes":
            ui.confirm2 = None
            diskcache.remove(folder, [f.name for f in diskcache.listing(folder)])
            ui.toast("The disk cache is empty now.", 6)
            return
        conf = jobs.cache_conf(fresh=True)
        key, _, value = act.partition(":")
        step = -1 if value == "prev" else 1
        new: object
        if key == "disk":
            gbs = sorted({*DISK_CHOICES, conf.disk_gb})
            new = int(value) if value.isdigit() else gbs[(gbs.index(conf.disk_gb) + step) % len(gbs)]
            text = f"{new} GB"
        elif key in ("prefix", "sessions", "share"):
            cur = {"prefix": conf.prefix, "sessions": conf.sessions, "share": conf.share}[key]
            new = value == "on" if value in ("on", "off") else not cur
            text = "on" if new else "off"
        elif key == "auto":
            autos = sorted({*AUTO_CHOICES, conf.auto_s})
            new = int(value) if value.isdigit() else autos[(autos.index(conf.auto_s) + step) % len(autos)]
            text = f"after {new} s of reading"
        elif key in ("save", "swa", "move"):
            opts = {"save": diskcache.SAVES, "swa": diskcache.SWAS, "move": diskcache.MOVES}[key]
            mode = {"save": conf.save, "swa": conf.swa, "move": conf.move}[key]
            new = value if value in opts else opts[(opts.index(mode) + step) % len(opts)]
            text = (str(new) + (". It applies at the next start" if key == "swa" else "") if key != "move" else
                    ("move to your message" if new == "auto" else "leave in place") +
                    ". OpenCode and Pi use it from their next request")
        else:
            return
        try:
            cfg = self.store.load_config()
            sec = cfg.setdefault("cache", {})
            sec[{"disk": "disk_gb", "auto": "auto_s"}.get(key, key)] = new
            self.store.save_config(cfg)
        except Exception as e:      # config.json unreadable or not writable: say so
            ui.toast(f"{RED}CARL cannot save config.json: {e}{R}", 10)
            return
        jobs.cache_conf(fresh=True)
        if key == "disk":
            before = len(diskcache.listing(folder))
            jobs.trim_cache()
            gone = before - len(diskcache.listing(folder))
            text += f". CARL removed the {plural(gone, 'oldest file')} to stay below it" if gone else ""
        ui.toast(f"Saved. {CACHE_NAMES[key].capitalize()}: {text}.", 8)

    def auto_choice(self, act: str) -> None:
        """The Auto fit panel's goal / candidates: fgoal / fscope switch to the other one, fgoal:X /
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
            ui.toast(f"{RED}Auto fit: {e}{R}", 10)
            return
        fit = self.svc.auto_fit(p)
        now = (f"Auto fit now suggests {fit.pick.name} with {plan_words(fit.plan.slots, fit.plan.ctx)}."
               if fit and fit.pick and fit.plan else "No model fits with this choice.")
        ui.toast(f"Saved. {now}", 8)

    def auto_fit(self, p: Pending) -> None:
        """Auto fit (Enter in its panel): its choice for this Mac with its context, slots and context
        memory, in one step; a choice that isn't downloaded is offered for download."""
        ui = self.ui
        fit = self.svc.apply_auto_fit(p)
        if fit is None:
            ui.toast(f"{RED}Auto fit cannot work now: {self.models.fit_error or 'no model list'}{R}", 10)
        elif fit.pick is None or fit.plan is None:
            ui.toast(f"{RED}Auto fit: no model fits this Mac.{R}", 12)
        elif not fit.pick.downloaded:
            m = self.models.by_name(fit.pick.name)
            ui.confirm2 = Confirm("DOWNLOAD AUTO FIT'S CHOICE?", [
                row("choice", fit.pick.name), row("with", plan_words(fit.plan.slots, fit.plan.ctx)),
                row("context memory", str(getattr(fit.plan, "kv", "") or "q4").split("_")[0]),
                row("needs", f"{memory(fit.plan.need)} of {memory(fit.budget.allowed)} (the GPU limit)"),
                row("download", file_size(m['bytes']) if m else "size not known"), "",
                "The Models panel shows the progress. After the download, press a in the Server panel to start it. "
                "If you cancel, the Server panel keeps this choice; it can start only after the download."], "mautodl",
                fit.pick.name, yes_label="Download")
        else:
            ui.toast(f"Auto fit's choice is set: {fit.pick.name} with {plan_words(fit.plan.slots, fit.plan.ctx)}. "
                     f"Press a to start it.", 8)

    # ------------------------------------------------------------ the model lists
    def set_arrangement(self, kind: str, index: int) -> None:
        """Set the model lists' sort or filter (index wraps), keeping the selected model when it is
        still listed; an open model drop-down is rebuilt in the new order."""
        ui = self.ui
        name = self.visible()[ui.mrow]["name"] if self.visible() and ui.mrow < len(self.visible()) else None
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
            ui.toast(f"{m['name']} is {status_name(m['status'])}. To download it: the Models panel (]), then d.", 8)

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
        elif pk.on_pick == "pickagents":
            ui.agents_model, ui.agents_row = str(val), 0
        elif pk.on_pick == "picktune":
            ui.tune_model = TUNE_ALL if val == TUNE_ALL_LABEL else val
        elif pk.on_pick == "pickcard" and ui.card:
            ui.card.add_pick(str(val))
        elif pk.on_pick in ("picksort", "pickfilter"):
            self.set_arrangement("sort" if pk.on_pick == "picksort" else "filter", int(str(val)))
            if pk.reopen and ui.sp == SP_AGENTS:       # back to the drop-down it came from
                ui.picker = self.view.agents_picker(pk.reopen, ui.msort, ui.mfilter)
            elif pk.reopen:
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

    # ------------------------------------------------------------ keys
    def keys(self, rest: str) -> bool:
        """A whole read while a text is typed (a Hugging Face repo, a card field): a paste stays one
        text. True when the input was used here."""
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
        if ui.card and ui.sp == SP_MODELS:
            return self.card.keys(rest)
        return False

    def key(self, k: str) -> bool:
        """One key in the Settings tab. True when it was used here (else the keys of every screen get it)."""
        ui = self.ui
        if ui.text or (ui.card is not None and ui.card.typing is not None and ui.sp == SP_MODELS):
            return self.keys(k)
        if ui.picker:
            return self.picker_key(k)
        if ui.confirm2:
            if k in ("y", "Y") or k in ENTER:
                self.run(ui.confirm2.yes)
            elif k in ("n", "N", ESC):
                ui.confirm2 = None
            return True
        if ui.card and ui.sp == SP_MODELS:
            return self.card.keys(k)
        if ui.edit is not None and ui.sp == SP_SERVER:
            return self.edit_key(k)
        if k in ("[", "]"):
            ui.sp = (ui.sp + (1 if k == "]" else -1)) % len(SUBPANELS)
            return True
        handlers: Dict[int, Callable[[str], bool]] = {
            SP_SERVER: self.server_key, SP_MODELS: self.models_key, SP_AGENTS: self.agents_key, SP_FIT: self.fit_key,
            SP_TUNE: self.tune_key, SP_ROUTER: self.router_key, SP_CACHE: self.cache_key}
        return handlers[ui.sp](k)

    def picker_key(self, k: str) -> bool:
        ui = self.ui
        pk = ui.picker
        if pk is None:
            return False
        if k == UP:
            pk.sel = max(pk.sel - 1, 0)
        elif k == DOWN:
            pk.sel = min(pk.sel + 1, len(pk.items) - 1)
        elif k == PGUP:
            pk.sel = max(pk.sel - 10, 0)
        elif k == PGDN:
            pk.sel = min(pk.sel + 10, len(pk.items) - 1)
        elif k in ARRANGE_KEYS and pk.on_pick in ("pickmodel", "pickagents"):
            cur = str(pk.items[pk.sel][0])
            self.action(ARRANGE_KEYS[k])
            ui.picker = (self.view.model_picker(cur, ui.msort, ui.mfilter, ui.pending) if pk.on_pick == "pickmodel"
                         else self.view.agents_picker(cur, ui.msort, ui.mfilter))
        elif k in ENTER:
            self.picker_choose()
        elif k == ESC:
            ui.picker = None
        return True

    def edit_key(self, k: str) -> bool:
        """A number being typed into the selected Server row: digits . k, Backspace, Enter keeps it, Esc
        drops it."""
        ui = self.ui
        if ui.edit is None:
            return False
        if k in "0123456789.kK" and len(k) == 1:
            ui.edit += k.lower()
        elif k in BACKSPACE and len(k) == 1:
            ui.edit = ui.edit[:-1]
        elif k in ENTER:
            self.commit_edit(self.selected_key())
        elif k == ESC:
            ui.edit = None
        return True

    def server_key(self, k: str) -> bool:
        """The Server panel: ↑↓ a row, ← → its value, Enter (the model list, or type a number), a apply,
        r undo, x the recommended settings, A Auto fit, m the model list beside it, s f its sort and filter,
        PgUp PgDn scroll."""
        ui = self.ui
        p = ui.pending
        if p is None:
            return False
        items = ["auto"] + [m["name"] for m in self.visible()]
        if ui.slist:                                # the model list beside the settings has the keys
            if k == UP:
                ui.srow = max(ui.srow - 1, 0)
            elif k == DOWN:
                ui.srow = min(ui.srow + 1, len(items) - 1)
            elif k in ENTER and items:
                self.choose_model(items[min(ui.srow, len(items) - 1)])
                ui.slist = False
            elif k in ("m", ESC):
                ui.slist = False
            elif k in ARRANGE_KEYS:
                self.action(ARRANGE_KEYS[k])
            else:
                return False
            return True
        n = len(self.svc.rows(p))
        if k in (UP, DOWN):
            ui.set_row = (ui.set_row + (1 if k == DOWN else -1)) % n
            ui.set_scroll = 0                       # the rows are at the top: keep the selected one in view
        elif k in (RIGHT, LEFTKEY):
            self.action(f"{'setinc' if k == RIGHT else 'setdec'}:{ui.set_row}")
        elif k in (PGUP, PGDN):
            ui.set_scroll = max(0, ui.set_scroll + (-10 if k == PGUP else 10))
        elif k in ENTER:
            key = self.selected_key()
            if key == "model":
                self.open_model_picker()
            elif key in NUMERIC:
                ui.edit = ""
        elif k in SERVER_KEYS:
            self.action(SERVER_KEYS[k])
        elif k == "m":
            ui.slist = True
        elif k in ARRANGE_KEYS:
            self.action(ARRANGE_KEYS[k])
        else:
            return False
        return True

    def models_key(self, k: str) -> bool:
        """The Models panel: ↑↓ PgUp PgDn a model, Enter use it, d v x h c u e, s f (S F back)."""
        ui = self.ui
        if k in (UP, DOWN, PGUP, PGDN):
            step = {UP: -1, DOWN: 1, PGUP: -10, PGDN: 10}[k]
            ui.mrow = max(min(ui.mrow + step, len(self.visible()) - 1), 0)
        elif k in MODEL_KEYS:
            self.action(MODEL_KEYS[k])
        else:
            return False
        return True

    def fit_key(self, k: str) -> bool:
        """The Auto fit panel: ↑↓ PgUp PgDn scroll, Enter use, d download, g goal, f candidates."""
        if k in (UP, DOWN, PGUP, PGDN):
            self.scroll({UP: 3, DOWN: -3, PGUP: 10, PGDN: -10}[k])
        elif k in FIT_KEYS:
            self.action(FIT_KEYS[k])
        else:
            return False
        return True

    def tune_key(self, k: str) -> bool:
        """The Auto-tune panel: ← → the model, space the length, Enter run, c cancel, x recommended; ↑↓ PgUp
        PgDn scroll the page."""
        if k in (UP, DOWN, PGUP, PGDN):
            self.ui.page_scroll = max(0, self.ui.page_scroll + {UP: -3, DOWN: 3, PGUP: -10, PGDN: 10}[k])
            return True
        if k in TUNE_KEYS:
            self.action(TUNE_KEYS[k])
            return True
        return False

    def router_key(self, k: str) -> bool:
        """The Router panel: s single model, r router mode (both ask first), ↑↓ a router model, Enter loads
        or unloads it, u updates the OpenCode and Pi configs."""
        ui, d = self.ui, self.snapshot()
        models = d.router.models if d.router is not None else []
        if k in ("s", "r"):
            self.action("rmode:single" if k == "s" else "rmode:router")
        elif k in (UP, DOWN) and models:
            ui.router_row = max(min(ui.router_row + (1 if k == DOWN else -1), len(models) - 1), 0)
        elif k in ENTER and models:
            m = models[min(ui.router_row, len(models) - 1)]
            self.action(f"{'runload' if m.active else 'rload'}:{m.id}")
        elif k == "u":
            self.run("insconfig")
        elif k in (PGUP, PGDN):
            ui.page_scroll = max(0, ui.page_scroll + (-10 if k == PGUP else 10))
        else:
            return False
        return True

    def agents_key(self, k: str) -> bool:
        """The Agents panel: ↑↓ a row, ← → its value (saved at once; the model row: the next model), Enter the model
        drop-down, r undo, x recommended, PgUp PgDn scroll the page."""
        ui = self.ui
        if k in (UP, DOWN):
            ui.agents_row = (ui.agents_row + (1 if k == DOWN else -1)) % len(AGENT_ROWS)
            ui.page_scroll = 0
        elif k in (RIGHT, LEFTKEY):
            self.action(f"agents:{'inc' if k == RIGHT else 'dec'}:{ui.agents_row}")
        elif k in ENTER and ui.agents_row == 0:
            self.action("agents:pick")
        elif k in AGENTS_KEYS:
            self.action(AGENTS_KEYS[k])
        elif k in (PGUP, PGDN):
            ui.page_scroll = max(0, ui.page_scroll + (-10 if k == PGUP else 10))
        else:
            return False
        return True

    def cache_key(self, k: str) -> bool:
        """The Caching panel: ↑↓ a row, ← → its value (saved at once), c clear (asks first), PgUp PgDn scroll
        the page."""
        ui = self.ui
        if k in (UP, DOWN):
            ui.cache_row = (ui.cache_row + (1 if k == DOWN else -1)) % len(CACHE_ROWS)
            ui.page_scroll = 0                       # the rows are at the top: keep the selected one in view
        elif k in (PGUP, PGDN):
            ui.page_scroll = max(0, ui.page_scroll + (-10 if k == PGUP else 10))
        elif k in (RIGHT, LEFTKEY):
            key = CACHE_ROWS[min(ui.cache_row, len(CACHE_ROWS) - 1)]
            self.action(f"cache:{key}" + ("" if k == RIGHT else ":prev"))
        elif k == "c":
            self.action("cache:clear")
        else:
            return False
        return True
