"""Client configs for the running server: OpenCode and Pi provider blocks and a curl test.
Pure: the templates (client/opencode/opencode.json, client/pi/models.json) come in as text.

Snippets for pasting by hand are additive: one provider block under the id "llm-deploy"
(or "llm-deploy-mtplx"), which can't collide with a provider the user already has, and
nothing else (no default model, $schema or agents)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .model import JSONDict, ServerData, jdict, jlist

SNIP_ID = {"llamacpp": "llm-deploy", "mtplx": "llm-deploy-mtplx"}
LABELS = {"opencode": "OpenCode config", "pi": "Pi config", "curl": "curl test"}


@dataclass
class Served:
    """What a client needs to know about the running server."""
    provider: str       # llamacpp | mtplx
    alias: str          # the model id clients ask for
    ctx: int
    llama: bool


def fill_template(text: Optional[str], host: str, port: int, home: str) -> JSONDict:
    """A client config template with this server's address filled in ({} if there is none)."""
    if text is None:
        return {}
    for a, b in (("__MTPLX_HOST__", host), ("__MTPLX_PORT__", str(port)), ("__LLAMA_PORT__", str(port)),
                 ("__HOME__", home)):
        text = text.replace(a, b)
    return jdict(json.loads(text))


def served(d: ServerData, alias_lookup: Callable[[], Optional[str]]) -> Served:
    """The running server as clients see it. alias_lookup asks the server (/v1/models)
    when /props has no alias."""
    alias = d.props.get("model_alias")
    llama = d.slots
    if not alias:
        alias = alias_lookup() or "model"
    return Served("llamacpp" if llama else "mtplx", str(alias), d.n_ctx or 49152, llama)


def opencode_config(s: Served, template: JSONDict, base: str, key: str) -> JSONDict:
    """An OpenCode provider block for this server."""
    p = jdict(jdict(template.get("provider")).get(s.provider)) or {
        "npm": "@ai-sdk/openai-compatible", "name": s.provider, "options": {}, "models": {}}
    p.setdefault("options", {})
    p["options"]["baseURL"] = f"{base}/v1"
    p["options"]["apiKey"] = key
    m = jdict(jdict(p.get("models")).get(s.alias)) or {
        "name": s.alias, "reasoning": True, "tool_call": True, "temperature": True,
        "limit": {"context": s.ctx, "output": 32000},
        "variants": {"none": {"reasoningEffort": "none"}}}
    m.setdefault("limit", {})
    m["limit"]["context"] = s.ctx
    m["limit"]["output"] = min(m["limit"].get("output", 32000), s.ctx // 2)
    p["models"] = {s.alias: m}
    p["name"] = p.get("name", s.provider) + " [llm-deploy]"
    return {"provider": {SNIP_ID[s.provider]: p}}


def pi_config(s: Served, template: JSONDict, base: str, key: str) -> JSONDict:
    """A Pi provider block for this server."""
    p = jdict(jdict(template.get("providers")).get(s.provider)) or {
        "baseUrl": "", "api": "openai-completions", "models": []}
    p["baseUrl"] = f"{base}/v1"
    p["apiKey"] = key
    ms = [m for m in jlist(p.get("models")) if isinstance(m, dict) and m.get("id") == s.alias] or [
        {"id": s.alias, "name": s.alias, "reasoning": True, "input": ["text"], "contextWindow": s.ctx, "maxTokens": 32768}]
    for m in ms:
        m["contextWindow"] = s.ctx
        m["maxTokens"] = min(m.get("maxTokens", 32768), s.ctx // 2)
    p["models"] = ms
    return {"providers": {SNIP_ID[s.provider]: p}}


def curl_test(s: Served, base: str, key: str) -> str:
    """A curl command that asks the server for a short answer."""
    off = '"reasoning_effort":"none"' if s.llama else '"enable_thinking":false'
    return (f"curl -s {base}/v1/chat/completions \\\n  -H 'Authorization: Bearer {key}' \\\n"
            f"  -H 'Content-Type: application/json' \\\n"
            f"  -d '{{\"model\":\"{s.alias}\",{off},\"messages\":[{{\"role\":\"user\",\"content\":\"Say hello\"}}]}}'")


def masked(text: str, key: str) -> str:
    """text with the key hidden (on-screen previews; the copy has the real key)."""
    return text.replace(key, "•" * 16 + key[-4:] + "  (k shows it; the copy has the real key)") if key else text


def config_text(kind: str, s: Served, templates: Dict[str, JSONDict], base: str, key: str) -> str:
    """The text of one config: kind = opencode | pi | curl."""
    if kind == "opencode":
        return json.dumps(opencode_config(s, templates.get("opencode", {}), base, key), indent=2, ensure_ascii=False)
    if kind == "pi":
        return json.dumps(pi_config(s, templates.get("pi", {}), base, key), indent=2, ensure_ascii=False)
    return curl_test(s, base, key)
