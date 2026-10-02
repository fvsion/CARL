#!/usr/bin/env python3
"""Merge the CARL client configs into OpenCode and Pi without clobbering
anything the user owns. Called by install.sh (it resolves the key, server
address, context window and whether the coder is on).

What "ours" means: recorded in a state file next to each config
(~/.config/opencode/llm-deploy.json, ~/.pi/agent/llm-deploy.json). Installs
from before the state file existed are recognised by a signature (our
provider names / API-key file path, our prompt text).

Rules
- Providers: ours replace only our own earlier version. If the user has their
  own provider called "llamacpp" / "mtplx", ours is installed alongside as
  "llm-deploy" / "llm-deploy-mtplx" and every reference uses that id.
- Default model (OpenCode model/small_model, Pi defaultProvider/defaultModel/
  defaultThinkingLevel): set only when unset or still the value we set before.
- Agents, prompts, extensions: never overwrite a user's file or agent with the
  same name; ours gets an "llm-deploy-" name or is skipped with a note.
- Lists (plugin, instructions): ours are appended / removed, others kept.
- Every changed file is backed up first (<file>.bak.<timestamp>).
"""
import argparse, json, os, re, shutil, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("--bundle", required=True)          # the client/ directory
ap.add_argument("--home", default=os.path.expanduser("~"))
ap.add_argument("--host", required=True)
ap.add_argument("--port", required=True)            # MTPLX
ap.add_argument("--llama-port", required=True)
ap.add_argument("--ctx", type=int, required=True)
ap.add_argument("--coder", type=int, default=1)
ap.add_argument("--sidebar", type=int, default=1)
ap.add_argument("--switcher", type=int, default=1)
args = ap.parse_args()

B, HOME = args.bundle, args.home
OC, PI = os.path.join(HOME, ".config/opencode"), os.path.join(HOME, ".pi/agent")
STAMP = time.strftime("%Y%m%d-%H%M%S")
report = {"added": [], "updated": [], "kept": [], "removed": [], "backed up": []}
ORIGINAL = ".before-carl"      # one copy of each config as it was before CARL first changed it
ALT = {"llamacpp": "llm-deploy", "mtplx": "llm-deploy-mtplx"}
OUR_NAMES = {"llama.cpp (Mac host)", "MTPLX (Mac host, ≤48K)", "MTPLX (Mac host)"}
OUR_KEY = "/.config/mtplx/api-key"
# defaults earlier versions set (treated as ours when no state file exists yet)
OLD_DEFAULTS = {"llamacpp/qwen3.8-27b-abliterated-llama", "llamacpp/qwen3.8-27b", "llamacpp/qwen3.6-35b-a3b",
                "mtplx/qwen3.8-27b-abliterated-grant", "mtplx/qwen3.8-27b-abliterated"}
DEFAULT_MODEL = "qwen3.6-35b-a3b"


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        sys.exit(f"error: {path} is not plain JSON (comments?). Fix or move it, then re-run; nothing was changed.")


def save(path, data, mode=0o600, backup=True):
    save_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", mode, backup)


def save_text(path, text, mode=0o600, backup=True):
    """Write only on a change. Before a change to a file that exists: keep the
    original once (FILE.before-carl, never overwritten) and a copy of the
    current version (FILE.bak.<time>)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        if open(path).read() == text:
            return
        if backup:
            if not os.path.exists(path + ORIGINAL):
                shutil.copy2(path, path + ORIGINAL)
                report["backed up"].append(f"{path.replace(HOME, '~')} -> {os.path.basename(path)}{ORIGINAL} (your original)")
            shutil.copy2(path, f"{path}.bak.{STAMP}")
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


def render(text):
    for a, b in (("__MTPLX_HOST__", args.host), ("__MTPLX_PORT__", args.port),
                 ("__LLAMA_PORT__", args.llama_port), ("__HOME__", HOME)):
        text = text.replace(a, b)
    return text


def bundle_json(rel):
    return json.loads(render(open(os.path.join(B, rel)).read()))


def coder_parts():
    text = open(os.path.join(B, "agents/coder.md")).read()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    front, body = m.group(1), m.group(2).lstrip()
    desc = re.search(r'^description:\s*"(.*)"\s*$', front, re.M).group(1)
    return text, front, body, desc


def name_agent(text, name):
    """Point the coder / delegation texts at the installed agent name."""
    if name == "coder":
        return text
    text = re.sub(r"^name: coder$", f"name: {name}", text, flags=re.M)
    return (text.replace("`coder`", f"`{name}`").replace('"coder"', f'"{name}"')
                .replace("If you ARE the coder subagent", f"If you ARE the {name} subagent")
                .replace("You are **coder**", f"You are **{name}**"))


def set_ctx_oc(provider):
    for m in provider.get("models", {}).values():
        m["limit"]["context"] = args.ctx
        m["limit"]["output"] = min(m["limit"]["output"], args.ctx // 2)
        m["name"] = m["name"].replace("128K", f"{args.ctx // 1024}K")


def set_ctx_pi(provider):
    for m in provider.get("models", []):
        m["contextWindow"] = args.ctx
        m["maxTokens"] = min(m["maxTokens"], args.ctx // 2)
        m["name"] = m["name"].replace("128K", f"{args.ctx // 1024}K")


def pick_ids(existing, state, is_ours):
    ids = {}
    for pid in ("llamacpp", "mtplx"):
        chosen = state.get("providers", {}).get(pid)
        if not chosen:
            cur = existing.get(pid)
            chosen = pid if cur is None or is_ours(cur) else ALT[pid]
        ids[pid] = chosen
    return ids


# =============================================================== OpenCode
def opencode():
    path = os.path.join(OC, "opencode.json")
    cfg = load(path, {})
    st_path = os.path.join(OC, "llm-deploy.json")
    st = load(st_path, {})
    first_install = not st
    bundle = bundle_json("opencode/opencode.json")

    def ours_oc(p):
        return p.get("name") in OUR_NAMES or OUR_KEY in str(p.get("options", {}).get("apiKey", ""))

    providers = cfg.setdefault("provider", {})
    had_ours_before = any(isinstance(v, dict) and ours_oc(v) for v in providers.values())
    ids = pick_ids(providers, st, ours_oc)
    for pid, new_id in ids.items():
        prov = bundle["provider"][pid]
        if pid == "llamacpp":
            set_ctx_oc(prov)
        if new_id != pid:
            prov["name"] = prov["name"] + " [llm-deploy]"
            report["kept"].append(f"OpenCode provider '{pid}' (yours); ours installed as '{new_id}'")
        if providers.get(new_id) != prov:
            (report["updated"] if new_id in providers else report["added"]).append(f"OpenCode provider '{new_id}'")
        providers[new_id] = prov

    # default model: only if unset or still what we set before
    ours_default = f"{ids['llamacpp']}/{DEFAULT_MODEL}"
    prev = {st.get("model"), st.get("small_model")} | (OLD_DEFAULTS if first_install and had_ours_before else set())
    for key in ("model", "small_model"):
        cur = cfg.get(key)
        # small_model follows model: if your default model is your own, your
        # small tasks (titles, summaries) aren't moved to our server either.
        if key == "small_model" and "model" not in st and cur is None:
            continue
        if cur is None or cur in prev:
            if cur != ours_default:
                cfg[key] = ours_default
                report["updated" if cur else "added"].append(f"OpenCode {key} = {ours_default}")
            st[key] = ours_default
        else:
            report["kept"].append(f"OpenCode {key} = {cur} (yours)")
            st.pop(key, None)

    if "model" not in st and cfg.get("small_model") in prev and cfg.get("small_model"):
        report["removed"].append(f"OpenCode small_model = {cfg.pop('small_model')} (follows your own default model)")
        st.pop("small_model", None)

    # title agent: we no longer disable it; undo our earlier setting
    agent = cfg.setdefault("agent", {})
    if (st.get("title_disabled") or (first_install and had_ours_before)) and agent.get("title") == {"disable": True}:
        agent.pop("title")
        report["removed"].append("OpenCode agent.title.disable (titles are back on)")
    st.pop("title_disabled", None)

    # plugins (server side): the MTPLX session headers
    plug = os.path.join(OC, "plugins/mtplx-session-headers")
    shutil.rmtree(plug, ignore_errors=True)
    shutil.copytree(os.path.join(B, "opencode/plugins/mtplx-session-headers"), plug)
    plist = cfg.setdefault("plugin", [])
    if plug not in plist:
        plist.append(plug)

    # coder agent + delegation rule
    ddir = os.path.join(OC, "llm-deploy")
    old_prompts = [os.path.join(OC, "prompts", n) for n in ("coder.md", "delegation.md")]
    for p in old_prompts:                      # earlier versions kept them in prompts/
        if os.path.exists(p) and open(p).read().lstrip().startswith(("You are **coder**", "## Delegating to the coder")):
            os.remove(p)
            cfg["instructions"] = [x for x in cfg.get("instructions", []) if x != p]
    def ours_agent(a):
        return isinstance(a, dict) and ("llm-deploy" in str(a.get("prompt", "")) or "prompts/coder.md" in str(a.get("prompt", "")))
    name = st.get("coder_agent") or ("coder" if "coder" not in agent or ours_agent(agent["coder"]) else "llm-deploy-coder")
    if name != "coder" and "coder" in agent and not st.get("coder_agent"):
        report["kept"].append("OpenCode agent 'coder' (yours); ours is 'llm-deploy-coder'")
    rule_path = os.path.join(ddir, "delegation.md")
    if args.coder:
        _, front, body, desc = coder_parts()
        prompt_path = os.path.join(ddir, "coder.md")
        os.makedirs(ddir, exist_ok=True)
        new_prompt = name_agent(body, name)
        new_rule = name_agent(open(os.path.join(B, "agents/delegation.md")).read(), name)
        for p, t, what in ((prompt_path, new_prompt, "coder prompt"), (rule_path, new_rule, "delegation rule")):
            old = open(p).read() if os.path.exists(p) else None
            if old is not None and old != t:
                report["updated"].append(f"OpenCode {what} ({p.replace(HOME, '~')})")
            open(p, "w").write(t)
        new_agent = {
            "description": desc,
            "mode": "subagent",
            "prompt": "{file:" + prompt_path + "}",
            "options": {"reasoningEffort": "medium"},
            "permission": {"task": "deny"},
            "steps": 80,
            "color": "secondary",
        }
        if agent.get(name) != new_agent:
            (report["updated"] if name in agent else report["added"]).append(f"OpenCode agent '{name}' + delegation rule")
        agent[name] = new_agent
        ins = cfg.setdefault("instructions", [])
        if rule_path not in ins:
            ins.append(rule_path)
        st["coder_agent"] = name
    else:
        if name in agent and (st.get("coder_agent") == name or ours_agent(agent[name])):
            agent.pop(name)
            report["removed"].append(f"OpenCode agent '{name}' (server has 1 slot, or NO_CODER=1)")
        cfg["instructions"] = [x for x in cfg.get("instructions", []) if x != rule_path]
        shutil.rmtree(ddir, ignore_errors=True)
        st.pop("coder_agent", None)
    for k in ("instructions", "agent", "plugin"):
        if not cfg.get(k):
            cfg.pop(k, None)
    cfg.setdefault("$schema", "https://opencode.ai/config.json")

    # TUI plugins: subagents sidebar, session switcher (in the prompt box)
    tui_path = os.path.join(OC, "tui.json")
    tui = load(tui_path, {})
    tplug = tui.setdefault("plugin", [])
    for name, label, want in (("subagents-sidebar", "OpenCode subagents sidebar", args.sidebar),
                              ("session-switcher", "OpenCode session switcher", args.switcher)):
        dest = os.path.join(OC, "plugins", name)
        entry = "file:" + dest
        if want:
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(os.path.join(B, "opencode/plugins", name), dest)
            if entry not in tplug:
                tplug.append(entry)
                report["added"].append(f"{label} (tui.json)")
        elif entry in tplug:
            tplug.remove(entry)
            shutil.rmtree(dest, ignore_errors=True)
            report["removed"].append(label)
    if not tplug:
        tui.pop("plugin")
    if tui or os.path.exists(tui_path):
        save(tui_path, tui)

    st.update({"providers": ids, "base_url": f"http://{args.host}:{args.llama_port}/v1", "updated": STAMP})
    save(path, cfg)
    save(st_path, st, backup=False)
    return ids


# =============================================================== Pi
def pi():
    path = os.path.join(PI, "models.json")
    cfg = load(path, {})
    st_path = os.path.join(PI, "llm-deploy.json")
    st = load(st_path, {})
    first_install = not st
    bundle = bundle_json("pi/models.json")

    def ours_pi(p):
        return OUR_KEY in str(p.get("apiKey", ""))

    providers = cfg.setdefault("providers", {})
    had_ours_before = any(isinstance(v, dict) and ours_pi(v) for v in providers.values())
    ids = pick_ids(providers, st, ours_pi)
    for pid, new_id in ids.items():
        prov = bundle["providers"][pid]
        if pid == "llamacpp":
            set_ctx_pi(prov)
        if new_id != pid:
            report["kept"].append(f"Pi provider '{pid}' (yours); ours installed as '{new_id}'")
        if providers.get(new_id) != prov:
            (report["updated"] if new_id in providers else report["added"]).append(f"Pi provider '{new_id}'")
        providers[new_id] = prov
    save(path, cfg)

    # settings: defaults only when unset or still ours
    sp = os.path.join(PI, "settings.json")
    sett = load(sp, {})
    want = {"defaultProvider": ids["llamacpp"], "defaultModel": DEFAULT_MODEL, "defaultThinkingLevel": "low"}
    prev = st.get("settings", {})
    legacy = first_install and had_ours_before and sett.get("defaultProvider") == "llamacpp"
    if all(sett.get(k) is None or sett.get(k) == prev.get(k) or legacy for k in want):
        changed = {k: v for k, v in want.items() if sett.get(k) != v}
        sett.update(want)
        if changed:
            report["updated"].append("Pi defaults " + ", ".join(f"{k}={v}" for k, v in changed.items()))
        st["settings"] = want
        save(sp, sett)
    else:
        report["kept"].append(f"Pi defaults (yours: {sett.get('defaultProvider')}/{sett.get('defaultModel')})")
        st.pop("settings", None)

    # extensions
    ext = os.path.join(PI, "extensions")
    os.makedirs(ext, exist_ok=True)
    pol = os.path.join(ext, "mtplx-request-policy.ts")
    src = open(os.path.join(B, "pi/extensions/mtplx-request-policy.ts")).read()
    if not os.path.exists(pol) or "Pi <-> MTPLX request bridge" in open(pol).read():
        open(pol, "w").write(src)
    else:
        report["kept"].append("Pi extensions/mtplx-request-policy.ts (yours)")

    sub = os.path.join(ext, "subagent")
    sub_ours = os.path.isdir(sub) and "LLM-Deploy" in open(os.path.join(sub, "index.ts")).read() \
        if os.path.exists(os.path.join(sub, "index.ts")) else not os.path.exists(sub)
    agents_dir = os.path.join(PI, "agents")
    name = st.get("coder_agent") or "coder"
    cpath = os.path.join(agents_dir, f"{name}.md")
    if name == "coder" and os.path.exists(cpath) and "You are **coder**" not in open(cpath).read():
        name, cpath = "llm-deploy-coder", os.path.join(agents_dir, "llm-deploy-coder.md")
        report["kept"].append("Pi agents/coder.md (yours); ours is 'llm-deploy-coder'")
    asp = os.path.join(PI, "APPEND_SYSTEM.md")
    cur = open(asp).read() if os.path.exists(asp) else ""
    begin, end = "<!-- llm-deploy:delegation begin -->", "<!-- llm-deploy:delegation end -->"
    cur = re.sub(r"\n*" + re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "\n", cur, flags=re.S).strip()
    if args.coder:
        if sub_ours:
            shutil.rmtree(sub, ignore_errors=True)
            shutil.copytree(os.path.join(B, "pi/extensions/subagent"), sub)
            st["subagent_ext"] = True
        else:
            report["kept"].append("Pi extensions/subagent (yours; it provides the subagent tool ours would)")
        os.makedirs(agents_dir, exist_ok=True)
        text, *_ = coder_parts()
        new_text = name_agent(text, name)
        if not os.path.exists(cpath) or open(cpath).read() != new_text:
            report["updated" if os.path.exists(cpath) else "added"].append(f"Pi agent '{name}' + delegation rule")
        open(cpath, "w").write(new_text)
        rule = name_agent(open(os.path.join(B, "agents/delegation.md")).read().strip(), name)
        cur = (cur + "\n\n" if cur else "") + f"{begin}\n{rule}\n{end}"
        st["coder_agent"] = name
    else:
        if sub_ours and os.path.isdir(sub):
            shutil.rmtree(sub)
        if os.path.exists(cpath) and "specialist software engineer" in open(cpath).read():
            os.remove(cpath)
            report["removed"].append(f"Pi agent '{name}' (server has 1 slot, or NO_CODER=1)")
        st.pop("coder_agent", None); st.pop("subagent_ext", None)
    if cur or os.path.exists(asp):
        save_text(asp, cur + "\n" if cur else "", mode=0o644)

    st.update({"providers": ids, "base_url": f"http://{args.host}:{args.llama_port}/v1", "updated": STAMP})
    save(st_path, st, backup=False)
    return ids


oc_ids = opencode()
pi_ids = pi()
for k in ("backed up", "added", "updated", "kept", "removed"):
    for line in report[k]:
        print(f"  {k:9s} {line}")
print(f"OpenCode: {OC}/opencode.json (providers: {', '.join(oc_ids.values())})")
print(f"Pi:       {PI}/models.json (providers: {', '.join(pi_ids.values())})")
