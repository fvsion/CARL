#!/usr/bin/env bash
# Wires OpenCode and Pi to the model server (llama.cpp, port 8080).
# Runs in the VMware VM (Linux) or directly on a Mac. Existing configs are
# merged without overwriting what you own (client/configure.py): our providers,
# defaults and agents are tracked in carl.json next to each config; a
# backup is written before any change.
#
#   ./install.sh                     auto: on macOS = --local, on Linux = --vm
#   ./install.sh --vm [HOST]         server on the VM host (default 192.168.42.1)
#   ./install.sh --local             server on this Mac: uses the address the
#                                    running server listens on, else 127.0.0.1
#   ./install.sh --host ADDR         any address (e.g. the Mac's LAN address, or a
#                                    Parallels / other VM network address)
#   --key-file FILE                  read the API key from FILE (copy it from the
#                                    dashboard: Connect tab, k shows the key)
#   --key KEY                        the API key itself (stays in your shell
#                                    history: prefer --key-file or the prompt)
#   --port N                         the llama.cpp server's port (default 8080)
#   ./install.sh HOST                same as --host HOST
#   (the pre-1.2.0 form HOST MTPLX_PORT LLAMA_PORT still works; the second value
#   is ignored: MTPLX support was removed)
#
# The llama.cpp context limit follows the running server's --ctx (read from
# /props); override with LLAMA_CTX=128k, default 96K if the server is down.
# Re-run after restarting the server with a different --ctx.
#
# API key source (first hit wins): --key / --key-file, $CARL_API_KEY, ./api-key next to
# this script, the server's own key file on a Mac (~/.config/carl/api-key), the
# key stored by a previous run, an interactive prompt. Stored at
# ~/.config/carl/api-key (0600; on the server Mac that is the server's own file).
# Earlier key files are still read while the new one doesn't exist yet. Re-running
# this script also brings an earlier install up to date (old names, MTPLX pieces).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"
tilde() { local t='~'; printf '%s' "${1/#"$HOME"/$t}"; }   # a path as ~/... (bash 3.2 keeps a quoted \~)
MODE=""; HOST=""; pos=(); ARG_KEY=""; LLAMA_PORT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) MODE=local ;;
    --vm) MODE=vm; if [[ "${2:-}" =~ ^[0-9.]+$ ]]; then HOST="$2"; shift; fi ;;
    --host) MODE=host; HOST="${2:?--host needs an address}"; shift ;;
    --key) ARG_KEY="${2:?--key needs the key}"; shift ;;
    --port) LLAMA_PORT="${2:?--port needs a port}"; shift ;;
    --key-file) f="${2:?--key-file needs a file}"; [[ -r "$f" ]] || { echo "error: cannot read $f" >&2; exit 1; }
                ARG_KEY="$(tr -d '[:space:]' < "$f")"; shift ;;
    -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) pos+=("$1") ;;
  esac
  shift
done
(( ${#pos[@]} <= 3 )) || { echo "error: too many arguments: ${pos[*]} (./install.sh -h)" >&2; exit 2; }
[[ ${#pos[@]} -ge 1 ]] && { HOST="${pos[0]}"; MODE="${MODE:-host}"; }
if (( ${#pos[@]} >= 2 )); then
  # The pre-1.2.0 form HOST MTPLX_PORT [LLAMA_PORT]: the MTPLX port has no use any more.
  echo "note: './install.sh HOST MTPLX_PORT LLAMA_PORT' is deprecated: MTPLX support was removed, '${pos[1]}' is ignored." >&2
  echo "      Use: ./install.sh --host HOST [--port LLAMA_PORT]" >&2
  [[ ${#pos[@]} -ge 3 && -z "$LLAMA_PORT" ]] && LLAMA_PORT="${pos[2]}"
fi
LLAMA_PORT="${LLAMA_PORT:-8080}"
if [[ -z "$MODE" ]]; then
  if [[ "$OS" == Darwin ]]; then MODE=local; else MODE=vm; fi
fi
valid_port() { [[ "$1" =~ ^[0-9]{1,5}$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 )); }
valid_port "$LLAMA_PORT" || { echo "error: not a TCP port: '$LLAMA_PORT'" >&2; exit 2; }

listen_addr() {  # address a local server listens on for port $1 (macOS/Linux)
  # netstat, not lsof: lsof hangs on a stale network share
  if [[ "$OS" == Darwin ]]; then
    /usr/sbin/netstat -anv -p tcp 2>/dev/null | awk -v p="$1" '$6=="LISTEN" && $4 ~ ("[.]" p "$") {a=$4; sub("[.]" p "$","",a); print (a=="*" ? "127.0.0.1" : a); exit}'
  elif command -v ss >/dev/null 2>&1; then
    ss -Hltn "sport = :$1" 2>/dev/null | awk '{split($4,a,":"); print a[1]; exit}'
  fi
  return 0
}
case "$MODE" in
  local) HOST="$(listen_addr "$LLAMA_PORT")"; HOST="${HOST:-127.0.0.1}" ;;
  vm) HOST="${HOST:-192.168.42.1}" ;;
esac
# The address goes into URLs and the client configs: a host name or an IP address only.
[[ "$HOST" =~ ^[A-Za-z0-9][A-Za-z0-9.:-]*$ ]] || { echo "error: not a host name or address: '$HOST'" >&2; exit 2; }
echo "Target: $MODE mode, server at $HOST (llama.cpp :$LLAMA_PORT)"

python3 -c 'import json' 2>/dev/null || {
  echo "error: python3 is required (macOS: xcode-select --install, or brew install python)" >&2; exit 1; }

# The clients' copy of the key. On the server Mac it is the server's own key file
# (host/common.sh uses the same path), so --local needs no copy.
KEY_DIR="$HOME/.config/carl"
KEY_FILE="$KEY_DIR/api-key"
# Before the rename in 1.2.0 the folder was ~/.config/llm-deploy (CARL was called
# LLM-Deploy). Moved once, as host/common.sh's migrate_conf_dir does on the server
# (on a Mac that is both, whichever runs first moves it); a symlink stays at the old
# path, so configs that still read the key there work until configure.py rewrites them.
OLD_KEY_DIR="$HOME/.config/llm-deploy"
if [[ -d "$OLD_KEY_DIR" && ! -L "$OLD_KEY_DIR" && ! -e "$KEY_DIR" && ! -L "$KEY_DIR" ]]; then
  if mv "$OLD_KEY_DIR" "$KEY_DIR"; then
    chmod 700 "$KEY_DIR"
    find "$KEY_DIR" -maxdepth 1 -type f -exec chmod go-rwx {} +
    ln -s carl "$OLD_KEY_DIR" 2>/dev/null || true
    echo "Moved $(tilde "$OLD_KEY_DIR") to $(tilde "$KEY_DIR") (CARL's new name; the old path links to it)"
  fi
fi
# Before 1.2.0: the server's key lived in MTPLX's folder, the clients' copy in ~/.config/mtplx.
OLD_SERVER_KEY="$HOME/.mtplx/api-key"
OLD_KEY_FILE="$HOME/.config/mtplx/api-key"

# --- API key -----------------------------------------------------------------
if [[ -n "$ARG_KEY" ]]; then
  key="$ARG_KEY"
elif [[ -n "${CARL_API_KEY:-}" ]]; then
  key="$CARL_API_KEY"
elif [[ -f "$HERE/api-key" ]]; then
  key="$(tr -d '[:space:]' < "$HERE/api-key")"
elif [[ -s "$KEY_FILE" ]]; then
  key="$(tr -d '[:space:]' < "$KEY_FILE")"
  echo "Using the API key in $(tilde "$KEY_FILE")"
elif [[ -s "$OLD_KEY_DIR/api-key" ]]; then
  key="$(tr -d '[:space:]' < "$OLD_KEY_DIR/api-key")"
  echo "Reusing the API key from $(tilde "$OLD_KEY_DIR/api-key"); it now lives in $(tilde "$KEY_FILE")"
elif [[ "$MODE" == local && -s "$OLD_SERVER_KEY" ]]; then
  key="$(tr -d '[:space:]' < "$OLD_SERVER_KEY")"
  echo "Using the local server's API key from before 1.2.0 ($(tilde "$OLD_SERVER_KEY"))"
elif [[ -s "$OLD_KEY_FILE" ]]; then
  key="$(tr -d '[:space:]' < "$OLD_KEY_FILE")"
  echo "Reusing the API key from $(tilde "$OLD_KEY_FILE") (before 1.2.0); it now lives in $(tilde "$KEY_FILE")"
else
  read -rsp "API key (on the server Mac: cat ~/.config/carl/api-key, or the monitor's CONNECT section): " key; echo
fi
key="${key#"${key%%[![:space:]]*}"}"; key="${key%"${key##*[![:space:]]}"}"   # trim (a pasted newline)
[[ -n "$key" ]] || { echo "error: empty API key" >&2; exit 1; }
# It goes into an HTTP header: printable characters, no spaces (never echoed back).
[[ "$key" =~ ^[[:graph:]]+$ ]] || { echo "error: the API key contains spaces or control characters" >&2; exit 1; }
mkdir -p "$KEY_DIR"; chmod 700 "$KEY_DIR"
# Rewritten only when it changes. On the server Mac this is the server's own key
# file: a different key replaces it for the next server start, so the old one is kept.
old_key=""
[[ -f "$KEY_FILE" ]] && old_key="$(tr -d '[:space:]' < "$KEY_FILE")"
if [[ "$old_key" != "$key" ]]; then
  if [[ -n "$old_key" ]]; then
    bak="$KEY_FILE.bak.$(date +%Y%m%d-%H%M%S)"
    ( umask 077; cp "$KEY_FILE" "$bak" )
    echo "note: replaced the API key in $(tilde "$KEY_FILE") (the old one: $(tilde "$bak")). If this Mac runs the"
    echo "      CARL server, it uses the new key from its next start."
  fi
  ( umask 077; printf '%s' "$key" > "$KEY_FILE" )
fi
chmod 600 "$KEY_FILE"

# api_get URL: GET with the key as Bearer header. The header is read from a file
# descriptor, so the key never appears in curl's command line (ps).
api_get() { curl -fsS -m 5 -H @<(printf 'Authorization: Bearer %s\n' "$key") "$1"; }

# --- llama.cpp context window --------------------------------------------------
# The clients' context limit (OpenCode limit.context, Pi contextWindow) is
# client-side only: it decides when they compact. It must not exceed the
# server's -c, or requests past it fail with HTTP 400 exceed_context_size_error.
# Source: $LLAMA_CTX (N or Nk) > the running server's /props n_ctx > 98304 (96K).
# Re-run this script after restarting the server with a different --ctx.
if [[ -n "${LLAMA_CTX:-}" ]]; then
  v="$(printf '%s' "$LLAMA_CTX" | tr '[:upper:]' '[:lower:]')"
  if [[ "$v" =~ ^([0-9]{1,6})k$ ]]; then ctx=$(( 10#${BASH_REMATCH[1]} * 1024 ))
  elif [[ "$v" =~ ^[0-9]{1,9}$ ]]; then ctx=$(( 10#$v ))
  else echo "error: LLAMA_CTX takes tokens or Nk (e.g. 96k), got '$LLAMA_CTX'" >&2; exit 1; fi
  ctx_src="LLAMA_CTX"
elif ctx=$(api_get "http://$HOST:$LLAMA_PORT/props" 2>/dev/null \
           | python3 -c 'import json,sys; n=int(json.load(sys.stdin)["default_generation_settings"]["n_ctx"]); assert n >= 1024; print(n)' 2>/dev/null); then
  ctx_src="running llama.cpp server"
else
  ctx=98304; ctx_src="default (llama.cpp server not reachable)"
fi
echo "llama.cpp context for clients: $ctx tokens ($ctx_src)"

# --- Coder subagent: only with a 2-slot server ---------------------------------
# A subagent is a second conversation. With one slot (e.g. a 24 GB Mac) every
# delegation evicts the main session, which is then re-read from scratch, so
# the coder and its delegation rule are installed only when the running server
# has 2+ slots (/props total_slots). CODER=1 forces it on, NO_CODER=1 off. If
# the server isn't reachable, a previous choice is kept (default: on).
slots=$(api_get "http://$HOST:$LLAMA_PORT/props" 2>/dev/null \
        | python3 -c 'import json,sys; print(int(json.load(sys.stdin).get("total_slots", 1)))' 2>/dev/null || true)
if [[ "${NO_CODER:-0}" == 1 ]]; then CODER=0; coder_src="NO_CODER=1"
elif [[ "${CODER:-}" == 1 ]]; then CODER=1; coder_src="CODER=1"
elif [[ -n "$slots" ]]; then
  if (( slots >= 2 )); then CODER=1; coder_src="server has $slots slots"; else CODER=0; coder_src="server has 1 slot: delegating would evict the main session"; fi
else
  # the state file configure.py keeps (llm-deploy.json before the rename; it moves it)
  oc_state="$HOME/.config/opencode/carl.json"
  [[ -f "$oc_state" ]] || oc_state="$HOME/.config/opencode/llm-deploy.json"
  if grep -qs '"coder_agent"' "$oc_state" || [[ ! -f "$oc_state" ]]; then CODER=1; coder_src="server not reachable; keeping it on"
  else CODER=0; coder_src="server not reachable; keeping it off"; fi
fi
echo "coder subagent: $([[ $CODER == 1 ]] && echo on || echo off) ($coder_src)"

# --- The installed models: one OpenCode / Pi entry each (client/carl_models.py) ---
# On the server Mac CARL lists them (tools/carl.py client-models) into
# installed-models.json next to this script, so a copy of this folder carries the
# list into a VM. Without the list (a bundle copied before 1.3.0): the ids the server
# reports on /v1/models, as generic entries. Re-run after a download or a delete
# (the dashboard's Connect tab says when the list is out of date).
MODELS_FILE="$HERE/installed-models.json"
if [[ -f "$HERE/../tools/carl.py" ]]; then
  if python3 "$HERE/../tools/carl.py" client-models > "$MODELS_FILE.tmp"; then
    mv "$MODELS_FILE.tmp" "$MODELS_FILE"
  else
    rm -f "$MODELS_FILE.tmp"; echo "warning: could not list this Mac's models (tools/carl.py client-models)" >&2
  fi
fi
running=$(api_get "http://$HOST:$LLAMA_PORT/props" 2>/dev/null \
          | python3 -c 'import json,sys; p=json.load(sys.stdin); print("" if p.get("role") == "router" else p.get("model_alias") or "")' 2>/dev/null || true)
models_arg="$MODELS_FILE"
if [[ ! -s "$MODELS_FILE" ]]; then
  models_arg="$(mktemp)"; trap 'rm -f "$models_arg"' EXIT
  if ! api_get "http://$HOST:$LLAMA_PORT/v1/models" 2>/dev/null | python3 -c '
import json, sys
sys.path.insert(0, sys.argv[1])
import carl_models
ids = [m.get("id") for m in json.load(sys.stdin).get("data", [])]
ml = carl_models.from_server_ids(ids, int(sys.argv[2]))
json.dump({"schema": 1, "default": ml.default, "models": [m.__dict__ for m in ml.models]}, sys.stdout)' "$HERE" "$ctx" > "$models_arg"; then
    echo '{"schema": 1, "default": null, "models": []}' > "$models_arg"
  fi
  echo "note: no installed-models.json here: the model entries come from the server (/v1/models). Copy the"
  echo "      client folder from the server Mac again (after ./carl.sh install there) for every installed model."
fi
echo "models for the clients: $(python3 -c 'import json,sys; print(", ".join(m["id"] for m in json.load(open(sys.argv[1]))["models"]) or "none yet")' "$models_arg")"

# --- Tools: web search on by default (WEB_SEARCH=exa|parallel|off) -------------------
web_search="${WEB_SEARCH:-exa}"
case "$web_search" in exa|parallel|off) ;; *) echo "error: WEB_SEARCH takes exa, parallel or off, got '$web_search'" >&2; exit 2 ;; esac
if [[ "$web_search" != off ]]; then
  echo "web search: on ($web_search). OpenCode and Pi send their search queries to $web_search, outside this"
  echo "            computer (the rest stays local). Off: WEB_SEARCH=off ./install.sh"
fi
# OpenCode reads its tool switches only from the environment: say plainly which shell
# profile files get the 3-line pointer to ~/.config/carl/opencode.env, before writing.
profiles=(); have_block=0
for f in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [[ -e "$f" ]] || continue
  if grep -qF "# >>> CARL: OpenCode tool switches >>>" "$f" 2>/dev/null; then have_block=1; continue; fi
  if [[ -L "$f" ]]; then profiles+=("$(tilde "$f") (a link: the change goes to $(tilde "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$f")"))")
  else profiles+=("$(tilde "$f")"); fi
done
if [[ "${NO_PROFILE:-0}" == 1 ]]; then
  echo "shell profile: not changed (NO_PROFILE=1). For OpenCode's tool switches, add this line to it yourself:"
  # shellcheck disable=SC2016  # the line is printed for the user to paste, not expanded here
  echo '               [ -f "$HOME/.config/carl/opencode.env" ] && . "$HOME/.config/carl/opencode.env"'
elif (( ${#profiles[@]} == 0 && have_block )); then
  echo "shell profile: already loads ~/.config/carl/opencode.env (nothing to write)"
elif (( ${#profiles[@]} )); then
  echo "shell profile: WRITING to ${profiles[*]}: appends 3 marked lines (# >>> CARL ... # <<< CARL <<<)"
  echo "               that load ~/.config/carl/opencode.env (OpenCode reads its tool switches only from the"
  echo "               environment). A backup is kept; nothing else in the file changes. Skip: NO_PROFILE=1"
fi
if [[ "${NO_BROWSER:-0}" != 1 ]]; then
  if [[ "$OS" == Darwin && -d "/Applications/Google Chrome.app" ]]; then
    echo "browser: OpenCode's browser agent and Pi drive Google Chrome (a temporary profile). Off: NO_BROWSER=1"
  else
    echo "browser: OpenCode's browser agent and Pi use Playwright's Chromium: once, run npx playwright install chromium"
    echo "         (about 150 MB). Off: NO_BROWSER=1"
  fi
fi

# --- OpenCode + Pi configs (client/configure.py) --------------------------------
# Merges our providers, defaults, coder agent, sidebar and extensions into the
# existing configs without overwriting anything the user owns: a provider of
# your own named "llamacpp" stays, and ours is added as "carl"; your
# default model, agents and extensions are kept. Our MTPLX pieces from before
# 1.2.0 are removed, and the pieces named llm-deploy get CARL's names. Backups: *.bak.<time>.
python3 "$HERE/configure.py" --bundle "$HERE" --home "$HOME" --host "$HOST" \
  --llama-port "$LLAMA_PORT" --ctx "$ctx" --models "$models_arg" ${running:+--running "$running"} --coder "$CODER" --sidebar "$([[ "${NO_SIDEBAR:-0}" == 1 ]] && echo 0 || echo 1)" \
  --switcher "$([[ "${NO_SWITCHER:-0}" == 1 ]] && echo 0 || echo 1)" \
  --model-check "$([[ "${NO_MODEL_CHECK:-0}" == 1 ]] && echo 0 || echo 1)" \
  --web-search "$web_search" --lsp "$([[ "${NO_LSP:-0}" == 1 || "${LSP:-1}" == 0 ]] && echo 0 || echo 1)" \
  --background "$([[ "${NO_BACKGROUND_SUBAGENTS:-0}" == 1 ]] && echo 0 || echo 1)" \
  --profile "$([[ "${NO_PROFILE:-0}" == 1 ]] && echo 0 || echo 1)" \
  --cache "$([[ "${NO_CACHE:-0}" == 1 || "${NO_PREFIX_CACHE:-0}" == 1 ]] && echo 0 || echo 1)" \
  --browser "$([[ "${NO_BROWSER:-0}" == 1 ]] && echo 0 || echo 1)" --browser-headed "$([[ "${BROWSER_HEADED:-0}" == 1 ]] && echo 1 || echo 0)"

# --- Smoke test ----------------------------------------------------------------
echo
if out=$(api_get "http://$HOST:$LLAMA_PORT/v1/models" 2>/dev/null); then
  echo "OK: llama.cpp reachable at http://$HOST:$LLAMA_PORT/v1 -> $(echo "$out" | grep -o '"id":"[^"]*"' | head -1)"
else
  echo "--: llama.cpp not running on http://$HOST:$LLAMA_PORT (start it on the Mac with ./carl.sh)"
fi
