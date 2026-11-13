"""Client configs for the running server: OpenCode and Pi provider blocks and a curl test.
Pure: the templates (client/opencode/opencode.json, client/pi/models.json) come in as text.

Snippets for pasting by hand are additive: one provider block under the id "carl",
which can't collide with a provider the user already has, and nothing else (no default
model, $schema or agents). It lists the models the client configs get, built as the client setup
builds them (carl_client.domain.models, the shared kernel, with the rules of the client package's data that the
composition reads through adapters/client_bundle.py), so pasting and installing give the same entries: in
single-model mode only the model the server runs (Phase 23.4.4 item 13), in router mode every
installed model. drift() compares what an installed client config lists with that list."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from carl_client.domain import models as client_models
from carl_client.domain.models import ModelList, ModelRules

from ..domain.memory.fit import DEFAULT_CTX
from ..domain.server.snapshot import JSONDict, ServerData, jdict

PROVIDER = "llamacpp"        # the provider id in the templates
SNIP_ID = "carl"             # the id of a pasted provider block
LABELS = {"opencode": "OpenCode config", "pi": "Pi config", "curl": "curl test"}


@dataclass
class Served:
    """What a client needs to know about the running server."""
    alias: str          # the model id clients ask for
    ctx: int


def fill_template(text: Optional[str], host: str, port: int, home: str, label: str = "host") -> JSONDict:
    """A client config template with this server's address filled in ({} if there is none; label: this computer in the
    provider's name, the platform's host_label). The JSON is read first and the values go into its strings, so a
    Windows home's backslashes stay text (as the client setup fills them)."""
    if text is None:
        return {}
    values = (("__HOST__", host), ("__LLAMA_PORT__", str(port)), ("__HOME__", home), ("__HOST_LABEL__", label))

    def fill(x: object) -> object:
        if isinstance(x, str):
            for a, b in values:
                x = x.replace(a, b)
            return x
        if isinstance(x, dict):
            return {fill(k): fill(v) for k, v in x.items()}
        if isinstance(x, list):
            return [fill(v) for v in x]
        return x
    return jdict(fill(json.loads(text)))


def served(d: ServerData, alias_lookup: Callable[[], Optional[str]]) -> Served:
    """The running server as clients see it. alias_lookup asks the server (/v1/models)
    when /props has no alias."""
    alias = d.props.get("model_alias") or alias_lookup() or "model"
    return Served(str(alias), d.n_ctx or DEFAULT_CTX)


def serving(d: ServerData) -> Tuple[Optional[str], Optional[bool]]:
    """(running, router) for the client list (ModelList.client_list): a single-model server's model (its alias), or
    router True; (None, None) when no server runs (the list follows config.json: the model a start loads)."""
    if not d.up:
        return None, None
    if d.router is not None:
        return None, True
    return d.alias or None, False


def model_list(doc: Optional[JSONDict], s: Served, rules: ModelRules) -> ModelList:
    """The installed models (carl client-models) under the client package's rules; without them, the served model
    alone."""
    try:
        ml = client_models.parse_list(doc, rules) if doc is not None else None
    except ValueError:
        ml = None
    return ml if ml and ml.models else client_models.from_server_ids([s.alias], s.ctx, rules)


def opencode_config(s: Served, template: JSONDict, base: str, key: str, ml: ModelList) -> JSONDict:
    """An OpenCode provider block for this server: one entry per installed model."""
    p = jdict(jdict(template.get("provider")).get(PROVIDER)) or {
        "npm": "@ai-sdk/openai-compatible", "name": PROVIDER, "options": {}, "models": {}}
    p.setdefault("options", {})
    p["options"]["baseURL"] = f"{base}/v1"
    p["options"]["apiKey"] = key
    p["models"] = client_models.opencode_models(ml, s.alias, s.ctx)
    p["name"] = p.get("name", PROVIDER) + f" [{SNIP_ID}]"
    return {"provider": {SNIP_ID: p}}


def pi_config(s: Served, template: JSONDict, base: str, key: str, ml: ModelList) -> JSONDict:
    """A Pi provider block for this server: one entry per installed model."""
    p = jdict(jdict(template.get("providers")).get(PROVIDER)) or {
        "baseUrl": "", "api": "openai-completions", "models": []}
    p["baseUrl"] = f"{base}/v1"
    p["apiKey"] = key
    p["models"] = client_models.pi_models(ml, s.alias, s.ctx)
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
    """A client config that is out of date: its model list differs from the list the configs get (single-model
    mode: the model the server runs; router mode: the installed models), or the running model's window differs from
    the server's (the context or slots changed)."""
    client: str
    listed: int
    added: List[str]            # in the list, not in the config
    removed: List[str]          # in the config, not in the list (OpenCode would get HTTP 400 in router mode)
    window: Optional[str] = None    # "MODEL: 96K in the config, 128K on the server"
    server: str = ""            # single-model mode: the model the server runs (the configs list only it)
    moved: Optional[Tuple[int, int]] = None   # the config's port, the server's (Phase 23.5: the server moved)

    def line(self, installed: int) -> str:
        """The drift in sentences: what an update changes, and the key."""
        parts: List[str] = []
        if self.moved:
            parts.append(f"{self.client} uses port {self.moved[0]}. The server runs on port {self.moved[1]}.")
        if self.added or self.removed:
            change = " and ".join(x for x in (f"adds {', '.join(self.added)}" if self.added else "",
                                              f"removes {', '.join(self.removed)}" if self.removed else "") if x)
            lists = f"{self.client} lists {self.listed} {'model' if self.listed == 1 else 'models'}"
            parts.append(f"{lists}. {single_mode(self.server)} An update {change}." if self.server else
                         f"{lists}, {installed} {'is' if installed == 1 else 'are'} installed. An update {change}.")
        if self.window:
            parts.append(f"{self.client}: the context of {self.window}.")
        return " ".join(parts) + " Press u."


def single_mode(server: str) -> str:
    """Why the configs list one model (single-model mode), as a sentence."""
    return f"In single-model mode, OpenCode and Pi list only the model of the server: {server}."


def drift(listed: Dict[str, Optional[Dict[str, int]]], installed: Sequence[str],
          running: Optional[Tuple[str, int]] = None, server: str = "",
          moved: Optional[Dict[str, Tuple[int, int]]] = None) -> List[Drift]:
    """The clients (configured for this server) whose config is out of date; a config that
    can't be read is left out. installed: the models the configs get (single-model mode: the model the server runs,
    named in server). running: the served model and its window per slot. moved: client -> (its port, the server's)."""
    out = []
    for client, ids in listed.items():
        if ids is None:
            continue
        added = [m for m in installed if m not in ids]
        removed = [m for m in ids if m not in installed]
        window = None
        if running and running[1] and ids.get(running[0]) not in (None, 0, running[1]):
            window = f"{running[0]} is {ids[running[0]] // 1024}K in the config, {running[1] // 1024}K on the server"
        mv = (moved or {}).get(client)
        if added or removed or window or mv:
            out.append(Drift(client, len(ids), added, removed, window, server, mv))
    return out
