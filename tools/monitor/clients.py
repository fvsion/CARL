"""Client configs for the running server: OpenCode and Pi provider blocks and a curl test.
Pure: the templates (client/opencode/opencode.json, client/pi/models.json) come in as text.

Snippets for pasting by hand are additive: one provider block under the id "carl",
which can't collide with a provider the user already has, and nothing else (no default
model, $schema or agents). It lists every installed model, built as client/install.sh
builds them (client/carl_models.py), so pasting and installing give the same entries.
drift() compares what an installed client config lists with what is installed."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .model import JSONDict, ServerData, jdict

_CLIENT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "client")
if _CLIENT not in sys.path:
    sys.path.insert(0, _CLIENT)
import carl_models  # noqa: E402  (client/carl_models.py: the installer's entry builder)
from carl_models import ModelList  # noqa: E402

PROVIDER = "llamacpp"        # the provider id in the templates
SNIP_ID = "carl"             # the id of a pasted provider block
LABELS = {"opencode": "OpenCode config", "pi": "Pi config", "curl": "curl test"}


@dataclass
class Served:
    """What a client needs to know about the running server."""
    alias: str          # the model id clients ask for
    ctx: int


def fill_template(text: Optional[str], host: str, port: int, home: str) -> JSONDict:
    """A client config template with this server's address filled in ({} if there is none)."""
    if text is None:
        return {}
    for a, b in (("__HOST__", host), ("__LLAMA_PORT__", str(port)), ("__HOME__", home)):
        text = text.replace(a, b)
    return jdict(json.loads(text))


def served(d: ServerData, alias_lookup: Callable[[], Optional[str]]) -> Served:
    """The running server as clients see it. alias_lookup asks the server (/v1/models)
    when /props has no alias."""
    alias = d.props.get("model_alias") or alias_lookup() or "model"
    return Served(str(alias), d.n_ctx or 98304)


def model_list(doc: Optional[JSONDict], s: Served) -> ModelList:
    """The installed models (tools/carl.py client-models); without them, the served model alone."""
    try:
        ml = carl_models.parse_list(doc) if doc is not None else None
    except ValueError:
        ml = None
    return ml if ml and ml.models else carl_models.from_server_ids([s.alias], s.ctx)


def opencode_config(s: Served, template: JSONDict, base: str, key: str, ml: ModelList) -> JSONDict:
    """An OpenCode provider block for this server: one entry per installed model."""
    p = jdict(jdict(template.get("provider")).get(PROVIDER)) or {
        "npm": "@ai-sdk/openai-compatible", "name": PROVIDER, "options": {}, "models": {}}
    p.setdefault("options", {})
    p["options"]["baseURL"] = f"{base}/v1"
    p["options"]["apiKey"] = key
    p["models"] = carl_models.opencode_models(ml, s.alias, s.ctx)
    p["name"] = p.get("name", PROVIDER) + f" [{SNIP_ID}]"
    return {"provider": {SNIP_ID: p}}


def pi_config(s: Served, template: JSONDict, base: str, key: str, ml: ModelList) -> JSONDict:
    """A Pi provider block for this server: one entry per installed model."""
    p = jdict(jdict(template.get("providers")).get(PROVIDER)) or {
        "baseUrl": "", "api": "openai-completions", "models": []}
    p["baseUrl"] = f"{base}/v1"
    p["apiKey"] = key
    p["models"] = carl_models.pi_models(ml, s.alias, s.ctx)
    return {"providers": {SNIP_ID: p}}


def curl_test(s: Served, base: str, key: str) -> str:
    """A curl command that asks the server for a short answer."""
    off = '"reasoning_effort":"none"'
    return (f"curl -s {base}/v1/chat/completions \\\n  -H 'Authorization: Bearer {key}' \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  -d '{{\"model\":\"{s.alias}\",{off},\"messages\":[{{\"role\":\"user\",\"content\":\"Say hello\"}}]}}'")


def masked(text: str, key: str) -> str:
    """text with the key hidden (on-screen previews; the copy has the real key)."""
    return text.replace(key, "•" * 16 + key[-4:] + "  (Press k to show it. The copy has the real key.)") if key else text


def config_text(kind: str, s: Served, templates: Dict[str, JSONDict], base: str, key: str, ml: ModelList) -> str:
    """The text of one config: kind = opencode | pi | curl."""
    if kind == "opencode":
        return json.dumps(opencode_config(s, templates.get("opencode", {}), base, key, ml), indent=2, ensure_ascii=False)
    if kind == "pi":
        return json.dumps(pi_config(s, templates.get("pi", {}), base, key, ml), indent=2, ensure_ascii=False)
    return curl_test(s, base, key)


@dataclass(frozen=True)
class Drift:
    """A client config that is out of date: its model list differs from the installed models,
    or the running model's window differs from the server's (the context or slots changed)."""
    client: str
    listed: int
    added: List[str]            # installed, not in the config
    removed: List[str]          # in the config, not installed (OpenCode would get HTTP 400 in router mode)
    window: Optional[str] = None    # "MODEL: 96K in the config, 128K on the server"

    def line(self, installed: int) -> str:
        """The drift in sentences: what an update changes, and the key."""
        parts: List[str] = []
        if self.added or self.removed:
            change = " and ".join(x for x in (f"adds {', '.join(self.added)}" if self.added else "",
                                              f"removes {', '.join(self.removed)}" if self.removed else "") if x)
            parts.append(f"{self.client} lists {self.listed} {'model' if self.listed == 1 else 'models'}, {installed} "
                         f"{'is' if installed == 1 else 'are'} installed. An update {change}.")
        if self.window:
            parts.append(f"{self.client}: the context of {self.window}.")
        return " ".join(parts) + " Press u."


def drift(listed: Dict[str, Optional[Dict[str, int]]], installed: Sequence[str],
          running: Optional[Tuple[str, int]] = None) -> List[Drift]:
    """The clients (configured for this server) whose config is out of date; a config that
    can't be read is left out. running: the served model and its window per slot."""
    out = []
    for client, ids in listed.items():
        if ids is None:
            continue
        added = [m for m in installed if m not in ids]
        removed = [m for m in ids if m not in installed]
        window = None
        if running and running[1] and ids.get(running[0]) not in (None, 0, running[1]):
            window = f"{running[0]} is {ids[running[0]] // 1024}K in the config, {running[1] // 1024}K on the server"
        if added or removed or window:
            out.append(Drift(client, len(ids), added, removed, window))
    return out
