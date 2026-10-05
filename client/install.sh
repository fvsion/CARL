#!/usr/bin/env bash
# Wires OpenCode and Pi to the model server (llama.cpp, port 8080).
# Runs in the VMware VM (Linux) or directly on a Mac. Existing configs are
# merged without overwriting what you own (client/configure.py): our providers,
# defaults and agents are tracked in carl.json next to each config; a
# backup is written before any change.
#
#   ./install.sh                     the server in remote.json next to this script (the server
#                                    writes it at every start), else auto: macOS = --local, Linux = --vm
#   ./install.sh --vm [HOST]         server on the VM host (default 192.168.42.1)
#   ./install.sh --local             server on this Mac: uses the address the
#                                    running server listens on, else 127.0.0.1
#   ./install.sh --host ADDR         any address (e.g. the Mac's LAN address, or a
#                                    Parallels / other VM network address)
#   --key-file FILE                  read the API key from FILE (copy it from the
#                                    dashboard: Connect tab, k shows the key)
#   --key KEY                        the API key itself (it stays in your shell history
#                                    and shows in the process list: use --key-file or the prompt)
#   --port N                         the llama.cpp server's port (default 8080)
#   ./install.sh HOST                same as --host HOST
#   (the pre-1.2.0 form HOST MTPLX_PORT LLAMA_PORT still works; the second value
#   is ignored: MTPLX support was removed)
#
# The context limit of the model the server runs now follows its --ctx (read
# from /props); LLAMA_CTX=128k overrides it (that model only, and only while the
# server runs). The other models get the context of their own settings.
# Re-run after restarting the server with a different --ctx.
#
# API key source (first hit wins): --key / --key-file, $CARL_API_KEY, ./api-key next to
# this script, the server's own key file on a Mac (~/.config/carl/api-key), the
# key stored by a previous run, an interactive prompt. Stored at
# ~/.config/carl/api-key (0600; on the server Mac that is the server's own file).
# Earlier key files are still read while the new one doesn't exist yet. Re-running
# this script also brings an earlier install up to date (old names, MTPLX pieces).
set -euo pipefail

# The width of the help page and the messages: COLUMNS, else the terminal's, else 80.
out_width() {
  local w="${COLUMNS:-}"
  if ! [[ "$w" =~ ^[0-9]+$ ]]; then
    w=""
    [[ -t 1 ]] && w="$(tput cols 2>/dev/null || true)"
    [[ "$w" =~ ^[0-9]+$ ]] || w=80
  fi
  (( w >= 50 )) || w=50
  printf '%s' "$w"
}
W="$(out_width)"

# The help text from stdin, wrapped to the width. A line "TERM\tTEXT" (a backslash and a t) is a term
# with its text in a column; the other lines wrap with their own indent; an empty line stays.
wrap_help() {
  awk -v W="$W" -v C=24 '
    function emit(text, first, rest,    n, words, i, line, started) {
      n = split(text, words, / +/); line = first; started = 0
      for (i = 1; i <= n; i++) {
        if (words[i] == "") continue
        if (started && length(line) + 1 + length(words[i]) > W) { print line; line = rest words[i] }
        else if (started) line = line " " words[i]
        else { line = line words[i]; started = 1 }
      }
      print line
    }
    $0 == "" { print ""; next }
    (k = index($0, "\\t")) {
      t = substr($0, 1, k - 1); d = substr($0, k + 2); pad = sprintf("%" C "s", "")
      if (length(t) + 2 > C) { print t; emit(d, pad, pad) } else emit(d, sprintf("%-" C "s", t), pad)
      next
    }
    { match($0, /^ */); ind = substr($0, 1, RLENGTH); emit(substr($0, RLENGTH + 1), ind, ind) }'
}

# One message, wrapped to the width; its next lines are indented by 2.
say() { printf '%s\n' "$*" | fold -s -w "$(( W - 2 ))" | sed -e 's/ *$//' -e '2,$s/^/  /'; }

usage() {
  wrap_help <<'HELP'
CARL client installer: connect OpenCode and Pi to the CARL server.

Run ./setup instead. It asks for your choices, installs OpenCode and Pi, and runs this script. This script is an internal part of the setup. The sync service runs it too.

Usage: ./install.sh [OPTION...]

Where the server is:
  (no option)\tThe server in remote.json next to this script. The server writes this file at every start. Without remote.json: --local on a Mac, --vm on Linux.
  --local\tThe server runs on this Mac. The script uses the address that the server listens on, else 127.0.0.1.
  --vm [HOST]\tThe server runs on the host of this VM. The default HOST is 192.168.42.1.
  --host ADDRESS\tThe server is at ADDRESS, for example the LAN address of the Mac.
  ADDRESS\tThe same as --host ADDRESS.
  --port N\tThe port of the server. The default is 8080.
  -h, --help\tShow this help.

The API key:
  --key-file FILE\tRead the API key from FILE. The dashboard on the server Mac shows the key on its Connect tab.
  --key KEY\tGive the API key on the command line. Then your shell history and the process list show the key. Use --key-file or the prompt if you can.
  The script looks for the key in this order: --key or --key-file, CARL_API_KEY, the file api-key next to this script, ~/.config/carl/api-key, a key from an earlier install. Then it asks you. It keeps the key in ~/.config/carl/api-key, with mode 0600.

Switches (environment variables, for example NO_CACHE=1 ./install.sh):
  CLIENTS=WHICH	The configs to write: both (the default), opencode or pi.
  NO_CACHE=1\tDo not install the disk cache.
  NO_MODEL_CHECK=1\tDo not install the model check of OpenCode.
  NO_SWITCHER=1\tDo not install the session switcher of OpenCode.
  NO_SIDEBAR=1\tDo not install the subagents sidebar of OpenCode.
  CODER=1, NO_CODER=1\tAlways install the coder subagent, or never. Without them, the script installs the coder when the server runs 2 or more slots.
  NO_BACKGROUND_SUBAGENTS=1\tThe main session waits for the coder.
  NO_BROWSER=1\tDo not install the browser tools.
  BROWSER_HEADED=1\tShow the browser on the screen.
  WEB_SEARCH=PROVIDER\texa (the default), parallel or off.
  NO_LSP=1\tDo not turn on the LSP tool of OpenCode.
  LLAMA_CTX=N\tThe context for the clients, in tokens or as Nk, for example 96k. The default is the context of the running server.
  NO_PROFILE=1\tDo not change ~/.zshrc or ~/.bashrc.
  NO_SYNC_SERVICE=1\tDo not install the sync service.

Run the script again after you change the context of the server, or after you download or delete a model. The script keeps your own settings. It makes a backup of each file before it changes it.
HELP
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"
tilde() { local t='~'; printf '%s' "${1/#"$HOME"/$t}"; }   # a path as ~/... (bash 3.2 keeps a quoted \~)
MODE=""; HOST=""; pos=(); ARG_KEY=""; KEY_IN_ARGS=0; LLAMA_PORT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) MODE=local ;;
    --vm) MODE=vm; if [[ "${2:-}" =~ ^[0-9.]+$ ]]; then HOST="$2"; shift; fi ;;
    --host) MODE=host; HOST="${2:?--host needs an address}"; shift ;;
    --key) ARG_KEY="${2:?--key needs the key}"; KEY_IN_ARGS=1; shift ;;
    --port) LLAMA_PORT="${2:?--port needs a port}"; shift ;;
    --key-file) f="${2:?--key-file needs a file}"
                [[ -r "$f" ]] || { echo "error: cannot read the key file $f (check the path and that you can read it)" >&2; exit 1; }
                ARG_KEY="$(tr -d '[:space:]' < "$f")"; KEY_IN_ARGS=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) pos+=("$1") ;;
  esac
  shift
done
(( ${#pos[@]} <= 3 )) || { echo "error: too many arguments: ${pos[*]}. Help: ./install.sh --help" >&2; exit 2; }
[[ ${#pos[@]} -ge 1 ]] && { HOST="${pos[0]}"; MODE="${MODE:-host}"; }
if (( ${#pos[@]} >= 2 )); then
  # The pre-1.2.0 form HOST MTPLX_PORT [LLAMA_PORT]: the MTPLX port has no use any more.
  say "Note: the form ./install.sh HOST MTPLX_PORT LLAMA_PORT is old. MTPLX support was removed, so the script ignores '${pos[1]}'." >&2
  say "Use this form: ./install.sh --host HOST --port PORT" >&2
  [[ ${#pos[@]} -ge 3 && -z "$LLAMA_PORT" ]] && LLAMA_PORT="${pos[2]}"
fi
# remote.json (written next to this script at every server start: host/serve-llama.sh): the
# server's address and port, for a copy of this folder on another computer given no address.
if [[ -z "$MODE" && -s "$HERE/remote.json" ]]; then
  if remote=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["host"], int(d["port"]))' \
                "$HERE/remote.json" 2>/dev/null); then
    HOST="${remote% *}"; LLAMA_PORT="${LLAMA_PORT:-${remote#* }}"
    # the server's own computer (its address is one of ours): local, no sync service
    if [[ "$HOST" =~ ^(127\.0\.0\.1|localhost|::1)$ ]] || { command -v ifconfig >/dev/null && ifconfig 2>/dev/null | grep -qF "inet $HOST "; } \
       || { command -v ip >/dev/null && ip -o addr 2>/dev/null | grep -qF "inet $HOST/"; }; then
      MODE=local
    else
      MODE=host
    fi
    echo "Server address from remote.json: $HOST:$LLAMA_PORT"
  fi
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
echo "Server: $HOST, port $LLAMA_PORT ($MODE mode)"

python3 -c 'import json' 2>/dev/null || {
  echo "error: the script needs python3. On macOS, run xcode-select --install or brew install python." >&2; exit 1; }

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
    echo "Moved $(tilde "$OLD_KEY_DIR") to $(tilde "$KEY_DIR"), CARL's new name. The old path links to it."
  fi
fi
# Before 1.2.0: the server's key lived in MTPLX's folder, the clients' copy in ~/.config/mtplx.
OLD_SERVER_KEY="$HOME/.mtplx/api-key"
OLD_KEY_FILE="$HOME/.config/mtplx/api-key"

# --- API key -----------------------------------------------------------------
if [[ -n "$ARG_KEY" ]]; then
  key="$ARG_KEY"
  if (( KEY_IN_ARGS )); then
    say "Note: --key puts the key in your shell history and in the process list. Next time, use --key-file FILE or the prompt." >&2
  fi
elif [[ -n "${CARL_API_KEY:-}" ]]; then
  key="$CARL_API_KEY"
elif [[ -f "$HERE/api-key" ]]; then
  chmod 600 "$HERE/api-key" 2>/dev/null || true      # a copied client folder can lose the mode
  key="$(tr -d '[:space:]' < "$HERE/api-key")"
elif [[ -s "$KEY_FILE" ]]; then
  key="$(tr -d '[:space:]' < "$KEY_FILE")"
  echo "Using the API key in $(tilde "$KEY_FILE")"
elif [[ -s "$OLD_KEY_DIR/api-key" ]]; then
  key="$(tr -d '[:space:]' < "$OLD_KEY_DIR/api-key")"
  echo "Using the API key from $(tilde "$OLD_KEY_DIR/api-key"). It is now in $(tilde "$KEY_FILE")."
elif [[ "$MODE" == local && -s "$OLD_SERVER_KEY" ]]; then
  key="$(tr -d '[:space:]' < "$OLD_SERVER_KEY")"
  echo "Using the API key of the local server from before 1.2.0: $(tilde "$OLD_SERVER_KEY")"
elif [[ -s "$OLD_KEY_FILE" ]]; then
  key="$(tr -d '[:space:]' < "$OLD_KEY_FILE")"
  echo "Using the API key from before 1.2.0 in $(tilde "$OLD_KEY_FILE"). It is now in $(tilde "$KEY_FILE")."
else
  read -rsp "API key (the dashboard on the server Mac shows it on its Connect tab): " key; echo
fi
key="${key#"${key%%[![:space:]]*}"}"; key="${key%"${key##*[![:space:]]}"}"   # trim (a pasted newline)
[[ -n "$key" ]] || { say "error: the API key is empty. Copy it from the Connect tab of the dashboard on the server Mac. Give it with --key-file FILE or at the prompt." >&2; exit 1; }
# It goes into an HTTP header: printable characters, no spaces (never echoed back).
[[ "$key" =~ ^[[:graph:]]+$ ]] || { say "error: the API key contains spaces or control characters. Copy it again from the Connect tab of the dashboard on the server Mac." >&2; exit 1; }
mkdir -p "$KEY_DIR"; chmod 700 "$KEY_DIR"
# Rewritten only when it changes. On the server Mac this is the server's own key
# file: a different key replaces it for the next server start, so the old one is kept.
old_key=""
[[ -f "$KEY_FILE" ]] && old_key="$(tr -d '[:space:]' < "$KEY_FILE")"
if [[ "$old_key" != "$key" ]]; then
  if [[ -n "$old_key" ]]; then
    bak="$KEY_FILE.bak.$(date +%Y%m%d-%H%M%S)"
    ( umask 077; cp "$KEY_FILE" "$bak" )
    say "Note: the script replaced the API key in $(tilde "$KEY_FILE"). The old key is in $(tilde "$bak"). If this Mac runs the CARL server, the server uses the new key after its next start."
  fi
  ( umask 077; printf '%s' "$key" > "$KEY_FILE" )
fi
chmod 600 "$KEY_FILE"

# api_get URL: GET with the key as Bearer header. The header is read from a file
# descriptor, so the key never appears in curl's command line (ps).
api_get() { curl -fsS -m 5 -H @<(printf 'Authorization: Bearer %s\n' "$key") "$1"; }

# --- The context for the clients -------------------------------------------------
# The clients' context limit (OpenCode limit.context, Pi contextWindow) is
# client-side only: it decides when they compact. It must not exceed the
# server's -c, or requests past it fail with HTTP 400 exceed_context_size_error.
# Source: $LLAMA_CTX (N or Nk) > the running server's /props n_ctx > 98304 (96K); for the running model
# only (client/carl_models.py _ctx_for): the others use their own settings.
# Re-run this script after restarting the server with a different --ctx.
if [[ -n "${LLAMA_CTX:-}" ]]; then
  v="$(printf '%s' "$LLAMA_CTX" | tr '[:upper:]' '[:lower:]')"
  if [[ "$v" =~ ^([0-9]{1,6})k$ ]]; then ctx=$(( 10#${BASH_REMATCH[1]} * 1024 ))
  elif [[ "$v" =~ ^[0-9]{1,9}$ ]]; then ctx=$(( 10#$v ))
  else echo "error: LLAMA_CTX takes tokens or Nk (e.g. 96k), got '$LLAMA_CTX'" >&2; exit 1; fi
  ctx_src=", from LLAMA_CTX."
elif ctx=$(api_get "http://$HOST:$LLAMA_PORT/props" 2>/dev/null \
           | python3 -c 'import json,sys; n=int(json.load(sys.stdin)["default_generation_settings"]["n_ctx"]); assert n >= 1024; print(n)' 2>/dev/null); then
  ctx_src=", from the running server."
else
  ctx=98304; ctx_src=". No server answers, so this is the default."
fi
ktok() { if (( $1 % 1024 == 0 )); then printf '%sK' "$(( $1 / 1024 ))"; else awk -v n="$1" 'BEGIN { printf "%.1fK", n / 1024 }'; fi; }   # K = 1024 tokens
say "Context for the clients: $(ktok "$ctx") tokens per slot$ctx_src"

# --- Coder subagent: only with a 2-slot server ---------------------------------
# A subagent is a second conversation. With one slot (e.g. a 24 GB Mac) every
# delegation evicts the main session, which is then re-read from scratch, so
# the coder and its delegation rule are installed only when the running server
# has 2+ slots (/props total_slots). CODER=1 forces it on, NO_CODER=1 off. If
# the server isn't reachable, a previous choice is kept (default: on).
slots=$(api_get "http://$HOST:$LLAMA_PORT/props" 2>/dev/null \
        | python3 -c 'import json,sys; print(int(json.load(sys.stdin).get("total_slots", 1)))' 2>/dev/null || true)
if [[ "${NO_CODER:-0}" == 1 ]]; then CODER=0; coder_src="NO_CODER=1 turns it off."
elif [[ "${CODER:-}" == 1 ]]; then CODER=1; coder_src="CODER=1 turns it on."
elif [[ -n "$slots" ]]; then
  if (( slots >= 2 )); then CODER=1; coder_src="The server runs $slots slots."
  else CODER=0; coder_src="The server runs 1 slot. A subagent would take the slot of the main session."; fi
else
  # the state file configure.py keeps (llm-deploy.json before the rename; it moves it)
  oc_state="$HOME/.config/opencode/carl.json"
  [[ -f "$oc_state" ]] || oc_state="$HOME/.config/opencode/llm-deploy.json"
  if grep -qs '"coder_agent"' "$oc_state" || [[ ! -f "$oc_state" ]]; then CODER=1
  else CODER=0; fi
  if [[ -f "$oc_state" ]]; then coder_src="No server answers, so the script keeps the earlier choice."
  else coder_src="No server answers, so the script uses the default."; fi
fi
say "Coder subagent: $([[ $CODER == 1 ]] && echo on || echo off). $coder_src"

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
    rm -f "$MODELS_FILE.tmp"; say "Warning: the script could not list the models of this Mac with tools/carl.py client-models." >&2
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
json.dump({"schema": 1, "default": ml.default, "models": [m.__dict__ for m in ml.models]}, sys.stdout)' "$HERE" "$ctx" \
       > "$models_arg" 2>/dev/null; then
    echo '{"schema": 1, "default": null, "models": []}' > "$models_arg"
  fi
  say "Note: there is no installed-models.json here, so the model list comes from the server (/v1/models). For every installed model, make a new client package on the server Mac (./carl.sh package), unzip it over this folder and run ./setup."
fi
say "Models for the clients: $(python3 -c 'import json,sys; print(", ".join(m["id"] for m in json.load(open(sys.argv[1]))["models"]) or "none yet")' "$models_arg")."

# --- Tools: web search on by default (WEB_SEARCH=exa|parallel|off) -------------------
web_search="${WEB_SEARCH:-exa}"
case "$web_search" in exa|parallel|off) ;; *) echo "error: WEB_SEARCH takes exa, parallel or off, got '$web_search'" >&2; exit 2 ;; esac
clients="${CLIENTS:-both}"
case "$clients" in both|opencode|pi) ;; *) echo "error: CLIENTS takes both, opencode or pi, got '$clients'" >&2; exit 2 ;; esac
if [[ "$web_search" != off ]]; then
  say "Web search: on ($web_search). OpenCode and Pi send the search queries to $web_search, outside this computer. Everything else stays on this computer. To turn it off, use WEB_SEARCH=off."
fi
# OpenCode reads its tool switches only from the environment: say plainly which shell
# profile files get the 3-line pointer to ~/.config/carl/opencode.env, before writing.
profiles=(); have_block=0
for f in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [[ -e "$f" ]] || continue
  if grep -qF "# >>> CARL: OpenCode tool switches >>>" "$f" 2>/dev/null; then have_block=1; continue; fi
  if [[ -L "$f" ]]; then profiles+=("$(tilde "$f") (a link to $(tilde "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$f")"))")
  else profiles+=("$(tilde "$f")"); fi
done
if [[ "${NO_PROFILE:-0}" == 1 ]]; then
  say "Shell profile: not changed (NO_PROFILE=1). For the tool switches of OpenCode, add this line to it yourself:"
  # shellcheck disable=SC2016  # the line is printed for the user to paste, not expanded here
  echo '  [ -f "$HOME/.config/carl/opencode.env" ] && . "$HOME/.config/carl/opencode.env"'
elif (( ${#profiles[@]} == 0 && have_block )); then
  echo "Shell profile: it loads ~/.config/carl/opencode.env already."
elif (( ${#profiles[@]} )); then
  prof_text="${profiles[0]}"; (( ${#profiles[@]} > 1 )) && prof_text="${profiles[0]} and ${profiles[1]}"
  say "Shell profile: the script adds 3 marked lines to $prof_text. They load ~/.config/carl/opencode.env, because OpenCode reads its tool switches only from the environment. The script makes a backup and changes nothing else in the file. To skip this, use NO_PROFILE=1."
fi
if [[ "${NO_BROWSER:-0}" != 1 ]]; then
  if [[ "$OS" == Darwin && -d "/Applications/Google Chrome.app" ]]; then
    say "Browser: OpenCode (its browser agent) and Pi use Google Chrome, with a temporary profile. To turn it off, use NO_BROWSER=1."
  else
    say "Browser: OpenCode (its browser agent) and Pi use the Chromium of Playwright. Install it one time with npx playwright install chromium. It is about 150 MB. To turn it off, use NO_BROWSER=1."
  fi
fi

# --- OpenCode + Pi configs (client/configure.py) --------------------------------
# Merges our providers, defaults, coder agent, sidebar and extensions into the
# existing configs without overwriting anything the user owns: a provider of
# your own named "llamacpp" stays, and ours is added as "carl"; your
# default model, agents and extensions are kept. Our MTPLX pieces from before
# 1.2.0 are removed, and the pieces named llm-deploy get CARL's names. Backups: *.bak.<time>.
python3 "$HERE/configure.py" --bundle "$HERE" --home "$HOME" --host "$HOST" --clients "$clients" \
  --llama-port "$LLAMA_PORT" --ctx "$ctx" --models "$models_arg" ${running:+--running "$running"} --coder "$CODER" --sidebar "$([[ "${NO_SIDEBAR:-0}" == 1 ]] && echo 0 || echo 1)" \
  --switcher "$([[ "${NO_SWITCHER:-0}" == 1 ]] && echo 0 || echo 1)" \
  --model-check "$([[ "${NO_MODEL_CHECK:-0}" == 1 ]] && echo 0 || echo 1)" \
  --web-search "$web_search" --lsp "$([[ "${NO_LSP:-0}" == 1 || "${LSP:-1}" == 0 ]] && echo 0 || echo 1)" \
  --background "$([[ "${NO_BACKGROUND_SUBAGENTS:-0}" == 1 ]] && echo 0 || echo 1)" \
  --profile "$([[ "${NO_PROFILE:-0}" == 1 ]] && echo 0 || echo 1)" \
  --cache "$([[ "${NO_CACHE:-0}" == 1 || "${NO_PREFIX_CACHE:-0}" == 1 ]] && echo 0 || echo 1)" \
  --browser "$([[ "${NO_BROWSER:-0}" == 1 ]] && echo 0 || echo 1)" --browser-headed "$([[ "${BROWSER_HEADED:-0}" == 1 ]] && echo 1 || echo 0)"

# --- Client config sync (carl-sync.py) -------------------------------------------
# This install's switches, so a sync (a config that the dashboard sends) applies them
# again; and on a computer whose server is elsewhere (remote.json), the sync service: one outgoing
# connection to the dashboard API (no port opens here) that applies each new config with this
# installer. launchd (macOS) or systemd --user (Linux) keeps it running. Off: NO_SYNC_SERVICE=1.
SYNC_LABEL=dev.carl.sync
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }   # text in the launchd plist
sync_service() {   # sync_service on|off: install / remove the service; "on" fails without a service manager
  local py plist unit
  py="$(command -v python3)"
  plist="$HOME/Library/LaunchAgents/$SYNC_LABEL.plist"
  unit="$HOME/.config/systemd/user/carl-sync.service"
  if [[ "$1" == off ]]; then
    if [[ -f "$plist" ]]; then launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true; rm -f "$plist"; echo "  removed   the client sync service"; fi
    if [[ -f "$unit" ]]; then systemctl --user disable --now carl-sync.service 2>/dev/null || true; rm -f "$unit"; echo "  removed   the client sync service"; fi
    return 0
  fi
  if [[ "$OS" == Darwin ]]; then
    mkdir -p "$(dirname "$plist")"
    cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$SYNC_LABEL</string>
  <key>ProgramArguments</key><array><string>$(xml "$py")</string><string>$(xml "$HERE/carl-sync.py")</string><string>watch</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$(xml "$HOME/.config/carl/client-sync.err")</string>
</dict></plist>
PLIST
    launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
  elif command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    mkdir -p "$(dirname "$unit")"
    cat > "$unit" <<UNIT
[Unit]
Description=CARL client config sync (applies the client config that the CARL dashboard sends)
After=network-online.target

[Service]
ExecStart="${py//%/%%}" "${HERE//%/%%}/carl-sync.py" watch
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
UNIT
    # restart, not enable --now: a running service would keep the carl-sync.py it started with
    systemctl --user daemon-reload && systemctl --user enable carl-sync.service >/dev/null \
      && systemctl --user restart carl-sync.service
  else
    return 1
  fi
}
if [[ "${CARL_SYNC:-0}" != 1 ]]; then
  ( umask 077; mkdir -p "$HOME/.config/carl"
    for k in CLIENTS CODER NO_CODER WEB_SEARCH NO_LSP LSP NO_BROWSER BROWSER_HEADED NO_SIDEBAR NO_SWITCHER \
             NO_MODEL_CHECK NO_BACKGROUND_SUBAGENTS NO_CACHE LLAMA_CTX; do
      [[ -n "${!k:-}" ]] && printf '%s=%s\n' "$k" "${!k}"
    done > "$HOME/.config/carl/client-install.env" ) || true
  if [[ "$MODE" == local || ! -s "$HERE/remote.json" || "${NO_SYNC_SERVICE:-0}" == 1 ]]; then
    sync_service off
    python3 "$HERE/carl-sync.py" register off 2>/dev/null || true
    [[ "$MODE" != local && "${NO_SYNC_SERVICE:-0}" == 1 ]] && say "Client sync: no sync service (NO_SYNC_SERVICE=1). OpenCode and Pi check for a new config when they start."
  elif sync_service on; then
    python3 "$HERE/carl-sync.py" register on 2>/dev/null || true
    say "Client sync: a background service ($([[ "$OS" == Darwin ]] && echo "launchd $SYNC_LABEL" || echo "systemd --user carl-sync")) keeps one connection to the dashboard on the server. It applies each new config that the dashboard sends. It opens no port on this computer. /carl in OpenCode or Pi shows its state. To remove it, use NO_SYNC_SERVICE=1."
    if [[ "$OS" != Darwin ]] && command -v loginctl >/dev/null 2>&1 \
         && [[ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" == no ]]; then
      say "Note: the service runs only while you are logged in. To keep it running, for example in a VM that you reach with SSH, run this command:"
      echo "  loginctl enable-linger $(id -un)"
    fi
  else
    python3 "$HERE/carl-sync.py" register off 2>/dev/null || true
    say "Client sync: no background service. This computer has no service manager, or the service did not start. OpenCode and Pi check for a new config when they start."
  fi
fi

# --- Smoke test ----------------------------------------------------------------
echo
if out=$(api_get "http://$HOST:$LLAMA_PORT/v1/models" 2>/dev/null); then
  first_id="$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null || true)"
  say "OK: the server answers at http://$HOST:$LLAMA_PORT/v1${first_id:+. It has $first_id}."
else
  say "Note: no server runs at http://$HOST:$LLAMA_PORT. Start it on the server Mac with ./carl.sh."
fi
