"""The dashboard's actions: what a click on a button or a key asks for.

An Action is a text (the clickable parts of the screen carry it, and the tests send it) that is a Verb and, after a
colon, what the verb acts on. The vocabulary is closed: every verb is a member of Verb, and OWNERS says which part
of the dashboard runs it. The renderers make actions with act(), so a verb that is not listed fails where it is
drawn; the controller reads one with parse() and sends it to its owner by the verb, never by the start of its text.
Nothing here depends on another module of the dashboard, so the renderers and the actions can both import it.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, Optional


class Verb(str, Enum):
    """Every verb of the dashboard, with its text (the part of an action before the colon)."""
    # the dashboard itself: the tabs, the levels, the dialogs, the Log tab, the cards of a plugin
    TAB = "tab"                         # tab:N
    LEVEL = "level"                     # level:SECTION
    DETAIL = "detail"
    LIVESTART = "livestart"
    QUIT = "quit"
    CANCEL = "cancel"
    KEY = "key"
    WRAP = "wrap"
    ERRORS = "errors"
    FOLLOW = "follow"
    DETACH = "detach"
    STOP = "stop"
    DRAIN = "drain"                     # drain:now | wait | cancel
    CSP = "csp"                         # csp:N, a panel of the Connect tab
    PLUGACT = "plugact"                 # plugact:PLUGIN:CARD:ACTION
    # the Connect tab
    OPENCODE = "opencode"
    PI = "pi"
    CURL = "curl"
    INSALL = "insall"
    INSCONFIG = "insconfig"
    INSYES = "insyes"
    INSNO = "insno"
    INSSHOW = "insshow"
    INSCLOSE = "insclose"
    INSCANCEL = "inscancel"
    INSPUSH = "inspush"
    PKGMAKE = "pkgmake"
    PKGSHOW = "pkgshow"
    PKGCLOSE = "pkgclose"
    CLFORGET = "clforget"
    # the Settings tab: the panel bar, the drop-downs, the questions and the card edit form
    SP = "sp"                           # sp:N
    PICK = "pick"                       # pick:N
    PICKOK = "pickok"
    PICKNO = "pickno"
    C2NO = "c2no"
    CARDROW = "cardrow"                 # cardrow:N
    CARDSAVE = "cardsave"
    CARDCANCEL = "cardcancel"
    CARDENTER = "cardenter"
    CARDCLEAR = "cardclear"
    # the Models panel and the model lists
    MROW = "mrow"                       # mrow:N
    SMODEL = "smodel"                   # smodel:NAME
    MSORT = "msort"
    MSORT_BACK = "msort-"
    MFILTER = "mfilter"
    MFILTER_BACK = "mfilter-"
    MSORTSET = "msortset"               # msortset:N
    MFILTERSET = "mfilterset"           # mfilterset:N
    MSORTPICK = "msortpick"
    MFILTERPICK = "mfilterpick"
    MUSEIT = "museit"
    MSPEEDS = "mspeeds"
    MQUANT = "mquant"                   # mquant:1 | -1, the next or the previous quantization of the model
    MDL = "mdl"
    MVERIFY = "mverify"
    MDELETE = "mdelete"
    MDELYES = "mdelyes"
    MHF = "mhf"
    HFTOKEN = "hftoken"                 # type the Hugging Face token (Settings > Models)
    HFTOKENCLEAR = "hftokenclear"       # remove the token CARL keeps
    MCANCEL = "mcancel"
    MTUNE = "mtune"
    MAUTODL = "mautodl"
    MEDIT = "medit"
    MMEASURE = "mmeasure"               # measure the model of the row with llama-server's own fit (A72)
    # the Auto fit panel
    FGOAL = "fgoal"                     # fgoal | fgoal:GOAL
    FSCOPE = "fscope"                   # fscope | fscope:SCOPE
    FUSE = "fuse"
    FDL = "fdl"
    FMEASURE = "fmeasure"               # measure the model of the Server panel (Auto fit's choice for auto) the same way
    # the Auto-tune panel
    TPREV = "tprev"
    TNEXT = "tnext"
    TPICK = "tpick"
    TQUICK = "tquick"
    TDEPTH = "tdepth"                   # tdepth:DEPTH
    TRUN = "trun"
    TYES = "tyes"
    TCANCEL = "tcancel"
    TCLEAR = "tclear"
    # the Server panel
    SETPICK = "setpick"
    SETROW = "setrow"                   # setrow:N
    SETINC = "setinc"                   # setinc:N
    SETDEC = "setdec"                   # setdec:N
    SETREVERT = "setrevert"
    SETDEFAULTS = "setdefaults"
    SETAUTOFIT = "setautofit"
    SETNOFIT = "setnofit"
    SETAPPLY = "setapply"
    SETNO = "setno"
    SETYES = "setyes"
    SETUBTUNED = "setubtuned"           # the -ub question's answers (A51): Use tuned, Keep for this model
    SETUBKEEP = "setubkeep"
    # the Agents, Router, Caching panels and the plugins' panels
    AGENTS = "agents"                   # agents:row:N | inc:N | dec:N | pick | undo | rec
    RMODE = "rmode"                     # rmode:single | router
    RMODEYES = "rmodeyes"
    RLOAD = "rload"                     # rload:MODEL
    RUNLOAD = "runload"                 # runload:MODEL
    CACHE = "cache"                     # cache:KEY | KEY:VALUE | KEY:prev | clear | clearyes
    PLUGROW = "plugrow"                 # plugrow:N
    PLUGSET = "plugset"                 # plugset:PLUGIN:KEY:VALUE


class AgentsOp(str, Enum):
    """What an agents:... action does (the Agents panel)."""
    ROW = "row"                         # agents:row:N, select a row
    INC = "inc"                         # agents:inc:N, the next value of a row
    DEC = "dec"
    PICK = "pick"                       # the model drop-down
    UNDO = "undo"
    REC = "rec"                         # the recommended values


class DrainOp(str, Enum):
    """What a drain:... action answers (the question "an agent is working")."""
    NOW = "now"
    WAIT = "wait"
    CANCEL = "cancel"


class CacheOp(str, Enum):
    """The cache:... actions that are not a row's value (the Caching panel)."""
    CLEAR = "clear"                     # asks first
    CLEARYES = "clearyes"


class Owner(str, Enum):
    """The part of the dashboard that runs a verb."""
    APP = "app"                         # the controller itself
    CONNECT = "connect"                 # actions/connect.py
    DIALOG = "dialog"                   # actions/settings/dialogs.py
    MODELS = "models"                   # actions/settings/models.py
    FIT = "fit"                         # actions/settings/autofit.py
    TUNE = "tune"                       # actions/settings/tune.py
    SERVER = "server"                   # actions/settings/server.py
    AGENTS = "agents"                   # actions/settings/agents.py
    ROUTER = "router"                   # actions/settings/router.py
    CACHE = "cache"                     # actions/settings/caching.py
    PLUGIN = "plugin"                   # actions/settings/plugin.py


def _owned(owner: Owner, *verbs: Verb) -> Dict[Verb, Owner]:
    return {v: owner for v in verbs}


V = Verb
OWNERS: Dict[Verb, Owner] = {
    **_owned(Owner.APP, V.TAB, V.LEVEL, V.DETAIL, V.LIVESTART, V.QUIT, V.CANCEL, V.KEY, V.WRAP, V.ERRORS, V.FOLLOW,
             V.DETACH, V.STOP, V.DRAIN, V.CSP, V.PLUGACT),
    **_owned(Owner.CONNECT, V.OPENCODE, V.PI, V.CURL, V.INSALL, V.INSCONFIG, V.INSYES, V.INSNO, V.INSSHOW, V.INSCLOSE,
             V.INSCANCEL, V.INSPUSH, V.PKGMAKE, V.PKGSHOW, V.PKGCLOSE, V.CLFORGET),
    **_owned(Owner.DIALOG, V.SP, V.PICK, V.PICKOK, V.PICKNO, V.C2NO, V.CARDROW, V.CARDSAVE, V.CARDCANCEL, V.CARDENTER,
             V.CARDCLEAR),
    **_owned(Owner.MODELS, V.MROW, V.SMODEL, V.MSORT, V.MSORT_BACK, V.MFILTER, V.MFILTER_BACK, V.MSORTSET, V.MFILTERSET,
             V.MSORTPICK, V.MFILTERPICK, V.MUSEIT, V.MSPEEDS, V.MDL, V.MVERIFY, V.MDELETE, V.MDELYES, V.MHF, V.MCANCEL,
             V.HFTOKEN, V.HFTOKENCLEAR, V.MTUNE, V.MAUTODL, V.MEDIT, V.MQUANT, V.MMEASURE),
    **_owned(Owner.FIT, V.FGOAL, V.FSCOPE, V.FUSE, V.FDL, V.FMEASURE),
    **_owned(Owner.TUNE, V.TPREV, V.TNEXT, V.TPICK, V.TQUICK, V.TDEPTH, V.TRUN, V.TYES, V.TCANCEL, V.TCLEAR),
    **_owned(Owner.SERVER, V.SETPICK, V.SETROW, V.SETINC, V.SETDEC, V.SETREVERT, V.SETDEFAULTS, V.SETAUTOFIT,
             V.SETNOFIT, V.SETAPPLY, V.SETNO, V.SETYES, V.SETUBTUNED, V.SETUBKEEP),
    **_owned(Owner.AGENTS, V.AGENTS),
    **_owned(Owner.ROUTER, V.RMODE, V.RMODEYES, V.RLOAD, V.RUNLOAD),
    **_owned(Owner.CACHE, V.CACHE),
    **_owned(Owner.PLUGIN, V.PLUGROW, V.PLUGSET),
}


class Action(str):
    """An action's text: VERB, or VERB:WHAT (what the verb acts on; it may hold more colons). A str, so the clickable
    parts of a screen, the regions and the tests carry it as the text it always was."""
    __slots__ = ()

    @property
    def verb(self) -> Verb:
        return Verb(self.partition(":")[0])

    @property
    def arg(self) -> str:
        return self.partition(":")[2]

    @property
    def owner(self) -> Owner:
        return OWNERS[self.verb]


def act(verb: Verb, arg: object = "") -> Action:
    """The action for a verb and what it acts on (nothing: the verb alone)."""
    text = arg.value if isinstance(arg, Enum) else str(arg)
    return Action(f"{verb.value}:{text}" if text else verb.value)


def agents(op: AgentsOp, row: object = "") -> Action:
    """An action of the Agents panel: agents:OP or agents:OP:ROW."""
    return act(Verb.AGENTS, f"{op.value}:{row}" if row != "" else op.value)


def cache(key: object, value: object = "") -> Action:
    """An action of the Caching panel: cache:KEY, cache:KEY:VALUE (a choice of the row; prev is the one before) or
    cache:clear / cache:clearyes."""
    k = key.value if isinstance(key, Enum) else str(key)
    return act(Verb.CACHE, f"{k}:{value}" if value != "" else k)


def parse(text: str) -> Optional[Action]:
    """text as an action; None when its verb is not in the vocabulary (a click or a key then does nothing)."""
    try:
        return Action(text) if Action(text).owner else None
    except ValueError:
        return None


def check() -> None:
    """Every verb has an owner (the tests call it)."""
    missing = [v.name for v in Verb if v not in OWNERS]
    if missing:
        raise ValueError(f"verbs without an owner: {', '.join(missing)}")
