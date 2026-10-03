#!/usr/bin/env bash
# Wires OpenCode and Pi to the model server (llama.cpp :8080, MTPLX :8000).
# Runs in the VMware VM (Linux) or directly on a Mac. Existing configs are
# merged without overwriting what you own (client/configure.py): our providers,
# defaults and agents are tracked in llm-deploy.json next to each config; a
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
#   ./install.sh [HOST] [MTPLX_PORT] [LLAMA_PORT]   (old positional form)
#
# The llama.cpp context limit follows the running server's --ctx (read from
# /props); override with LLAMA_CTX=128k, default 96K if the server is down.
# Re-run after restarting the server with a different --ctx.
#
# API key source (first hit wins): --key / --key-file, $MTPLX_API_KEY, ./api-key next to this
# script, the server's own key file on a Mac (~/.mtplx/api-key), the key
# stored by a previous run, an interactive prompt. Stored at
# ~/.config/mtplx/api-key (0600).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"
MODE=""; HOST=""; pos=(); ARG_KEY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) MODE=local ;;
    --vm) MODE=vm; if [[ "${2:-}" =~ ^[0-9.]+$ ]]; then HOST="$2"; shift; fi ;;
    --host) MODE=host; HOST="${2:?--host needs an address}"; shift ;;
    --key) ARG_KEY="${2:?--key needs the key}"; shift ;;
    --key-file) f="${2:?--key-file needs a file}"; [[ -r "$f" ]] || { echo "error: cannot read $f" >&2; exit 1; }
                ARG_KEY="$(tr -d '[:space:]' < "$f")"; shift ;;
    -h|--help) sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) pos+=("$1") ;;
  esac
  shift
done
[[ ${#pos[@]} -ge 1 ]] && { HOST="${pos[0]}"; MODE="${MODE:-host}"; }
PORT="${pos[1]:-8000}"
LLAMA_PORT="${pos[2]:-8080}"
if [[ -z "$MODE" ]]; then
  if [[ "$OS" == Darwin ]]; then MODE=local; else MODE=vm; fi
fi
valid_port() { [[ "$1" =~ ^[0-9]{1,5}$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 )); }
for p in "$PORT" "$LLAMA_PORT"; do
  valid_port "$p" || { echo "error: not a TCP port: '$p'" >&2; exit 2; }
done

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
  local) HOST="$(listen_addr "$LLAMA_PORT")"; HOST="${HOST:-$(listen_addr "$PORT")}"; HOST="${HOST:-127.0.0.1}" ;;
  vm) HOST="${HOST:-192.168.42.1}" ;;
esac
# The address goes into URLs and the client configs: a host name or an IP address only.
[[ "$HOST" =~ ^[A-Za-z0-9][A-Za-z0-9.:-]*$ ]] || { echo "error: not a host name or address: '$HOST'" >&2; exit 2; }
echo "Target: $MODE mode, server at $HOST (llama.cpp :$LLAMA_PORT, MTPLX :$PORT)"

python3 -c 'import json' 2>/dev/null || {
  echo "error: python3 is required (macOS: xcode-select --install, or brew install python)" >&2; exit 1; }

KEY_DIR="$HOME/.config/mtplx"
KEY_FILE="$KEY_DIR/api-key"
SERVER_KEY="$HOME/.mtplx/api-key"     # the server's key, when the server runs on this machine

# --- API key -----------------------------------------------------------------
if [[ -n "$ARG_KEY" ]]; then
  key="$ARG_KEY"
elif [[ -n "${MTPLX_API_KEY:-}" ]]; then
  key="$MTPLX_API_KEY"
elif [[ -f "$HERE/api-key" ]]; then
  key="$(tr -d '[:space:]' < "$HERE/api-key")"
elif [[ "$MODE" == local && -s "$SERVER_KEY" ]]; then
  key="$(tr -d '[:space:]' < "$SERVER_KEY")"
  echo "Using the local server's API key ($SERVER_KEY)"
elif [[ -s "$KEY_FILE" ]]; then
  key="$(tr -d '[:space:]' < "$KEY_FILE")"
  echo "Reusing API key from $KEY_FILE"
else
  read -rsp "API key (on the server Mac: cat ~/.mtplx/api-key, or the monitor's CONNECT section): " key; echo
fi
key="${key#"${key%%[![:space:]]*}"}"; key="${key%"${key##*[![:space:]]}"}"   # trim (a pasted newline)
[[ -n "$key" ]] || { echo "error: empty API key" >&2; exit 1; }
# It goes into an HTTP header: printable characters, no spaces (never echoed back).
[[ "$key" =~ ^[[:graph:]]+$ ]] || { echo "error: the API key contains spaces or control characters" >&2; exit 1; }
mkdir -p "$KEY_DIR"; chmod 700 "$KEY_DIR"
( umask 077; printf '%s' "$key" > "$KEY_FILE" )
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
           | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["default_generation_settings"]["n_ctx"]))' 2>/dev/null); then
  ctx_src="running llama.cpp server"
else
  ctx=98304; ctx_src="default (llama.cpp server not reachable)"
fi
echo "llama.cpp context for clients: $ctx tokens ($ctx_src)"

# --- Coder subagent: only with a 2-slot server ---------------------------------
# A subagent is a second conversation. With one slot (e.g. a 24 GB Mac) every
# delegation evicts the main session, which is then re-read from scratch, so
# the coder and its delegation rule are installed only when the running server
# has 2+ slots (/props total_slots), or is MTPLX (it runs one request at a time
# and keeps each session in its session bank, so the main session comes back
# without a full re-read: measured 3.8 s for 24K tokens, 2026-10-02).
# CODER=1 forces it on, NO_CODER=1 off. If
# the server isn't reachable, a previous choice is kept (default: on).
slots=$(api_get "http://$HOST:$LLAMA_PORT/props" 2>/dev/null \
        | python3 -c 'import json,sys; print(int(json.load(sys.stdin).get("total_slots", 1)))' 2>/dev/null || true)
if [[ "${NO_CODER:-0}" == 1 ]]; then CODER=0; coder_src="NO_CODER=1"
elif [[ "${CODER:-}" == 1 ]]; then CODER=1; coder_src="CODER=1"
elif [[ -n "$slots" ]]; then
  if (( slots >= 2 )); then CODER=1; coder_src="server has $slots slots"; else CODER=0; coder_src="server has 1 slot: delegating would evict the main session"; fi
elif api_get "http://$HOST:$PORT/v1/mtplx/snapshot" >/dev/null 2>&1; then
  CODER=1; coder_src="MTPLX: one request at a time, each session comes back from its session bank"
elif grep -qs '"coder_agent"' "$HOME/.config/opencode/llm-deploy.json" || [[ ! -f "$HOME/.config/opencode/llm-deploy.json" ]]; then CODER=1; coder_src="server not reachable; keeping it on"
else CODER=0; coder_src="server not reachable; keeping it off"; fi
echo "coder subagent: $([[ $CODER == 1 ]] && echo on || echo off) ($coder_src)"

# --- OpenCode + Pi configs (client/configure.py) --------------------------------
# Merges our providers, defaults, coder agent, sidebar and extensions into the
# existing configs without overwriting anything the user owns: a provider of
# your own named "llamacpp"/"mtplx" stays, and ours is added as "llm-deploy";
# your default model, agents and extensions are kept. Backups: *.bak.<time>.
python3 "$HERE/configure.py" --bundle "$HERE" --home "$HOME" --host "$HOST" --port "$PORT" \
  --llama-port "$LLAMA_PORT" --ctx "$ctx" --coder "$CODER" --sidebar "$([[ "${NO_SIDEBAR:-0}" == 1 ]] && echo 0 || echo 1)" \
  --switcher "$([[ "${NO_SWITCHER:-0}" == 1 ]] && echo 0 || echo 1)"

# --- Smoke test (only one server usually runs at a time) -------------------
echo
for p in "$LLAMA_PORT:llama.cpp" "$PORT:MTPLX"; do
  if out=$(api_get "http://$HOST:${p%%:*}/v1/models" 2>/dev/null); then
    echo "OK: ${p#*:} reachable at http://$HOST:${p%%:*}/v1 -> $(echo "$out" | grep -o '"id":"[^"]*"' | head -1)"
  else
    echo "--: ${p#*:} not running on http://$HOST:${p%%:*} (start it on the Mac with ./carl.sh ...)"
  fi
done
