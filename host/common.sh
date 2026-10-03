# shellcheck shell=bash
# Shared by host/serve.sh and host/serve-llama.sh. Sourced, not run.
#
# Network mode -- where the server listens:
#   --vm     VM_HOST (default 192.168.42.1, VMware Fusion's vmnet8 address on
#            this Mac). Fails if that interface doesn't exist (Fusion not running).
#   --local  127.0.0.1: clients on this Mac only (OpenCode/Pi installed locally).
#   (none)   auto: VM_HOST if the interface exists, otherwise 127.0.0.1.
#   --host ADDR / HOST=ADDR: one address of this Mac (its LAN address, or another
#            VM network such as Parallels 10.211.55.2); wins over the modes.
#            NET=vm|local|auto is the env form of the modes; VM_HOST changes the vm address.
# Never 0.0.0.0: these scripts assume the macOS firewall may be off, so a
# wildcard bind would publish the model and its key-protected API to the LAN.

VM_HOST="${VM_HOST:-192.168.42.1}"

# port_pid PORT: pid of the process listening on TCP PORT (empty if none).
# port_host PORT: the address it listens on. netstat, not lsof: lsof stats every
# mounted filesystem and hangs, unkillable, on a stale network share (a dead
# Time Machine SMB volume froze ./carl.sh and the monitor).
# A PORT that isn't a number gives no answer (it is used inside an awk regex).
is_port() { [[ "$1" =~ ^[0-9]{1,5}$ ]] && (( 10#$1 >= 1 && 10#$1 <= 65535 )); }
_listen() { is_port "$1" || return 0; /usr/sbin/netstat -anv -p tcp 2>/dev/null | awk -v p="$1" '
  $6 == "LISTEN" && $4 ~ ("[.]" p "$") { a = $4; sub("[.]" p "$", "", a)
    pid = ""; if (match($0, /:[0-9]+ +[0-9][0-9][0-9][0-9][0-9] /)) { pid = substr($0, RSTART + 1, RLENGTH); sub(/ .*/, "", pid) }
    print a, pid; exit }'; }
port_pid() { _listen "$1" | awk '{print $2}'; }
port_host() { _listen "$1" | awk '{print ($1 == "*" ? "127.0.0.1" : $1)}'; }

has_addr() { ifconfig 2>/dev/null | grep -qF "inet $1 "; }

# require_int NAME VALUE [MIN [MAX]]: fail unless VALUE is a whole number in range.
# Settings reach bash arithmetic, which would evaluate anything else as an expression.
require_int() {
  local name="$1" v="$2" min="${3:-}" max="${4:-}"
  if [[ ! "$v" =~ ^(0|[1-9][0-9]{0,8})$ ]] || { [[ -n "$min" ]] && (( 10#$v < min )); } || { [[ -n "$max" ]] && (( 10#$v > max )); }; then
    echo "error: $name must be a whole number${min:+ from $min}${max:+ to $max}, got '$v'" >&2
    exit 2
  fi
}

# apply_settings ALLOWED FORCED: read KEY=value lines (tools/carl.py launch-env)
# from stdin and set each KEY matching the ALLOWED regex, unless the
# environment already sets it (KEYs matching FORCED are always set). Values are
# assigned with printf -v, never evaluated. SETTINGS_USED lists what was set.
SETTINGS_USED=()
apply_settings() {
  local allowed="^($1)\$" forced="^($2)\$" k v
  SETTINGS_USED=()
  while IFS='=' read -r k v; do
    [[ -n "$k" ]] || continue
    if [[ ! "$k" =~ $allowed ]]; then
      echo "warning: ignoring unknown setting '$k' from tools/carl.py" >&2; continue
    fi
    [[ "$k" =~ $forced || -z "${!k:-}" ]] || continue
    printf -v "$k" '%s' "$v"
    SETTINGS_USED+=("$k=$v")
  done
}

# resolve_host MODE -> sets HOST (and NET_NOTE for the start-up banner)
# shellcheck disable=SC2034  # NET_NOTE is read by the scripts that source this file
resolve_host() {
  local mode="${1:-${NET:-auto}}"
  NET_NOTE=""
  if [[ -n "${HOST:-}" ]]; then
    case "$HOST" in
      127.0.0.1|localhost|::1) NET_NOTE="$HOST (explicit): this Mac only" ;;
      "$VM_HOST") NET_NOTE="$HOST (explicit): the VM network and this Mac" ;;
      *) NET_NOTE="$HOST (explicit): CAUTION: every computer that reaches this address can use the server; only the API key protects it" ;;
    esac
  else
    case "$mode" in
      vm)
        has_addr "$VM_HOST" || {
          echo "error: --vm: no interface has $VM_HOST. Start VMware Fusion (vmnet8) first," >&2
          echo "       or use --local to serve this Mac only." >&2
          exit 1; }
        HOST="$VM_HOST"; NET_NOTE="VM network ($VM_HOST): reachable from the Fusion VM and this Mac" ;;
      local)
        HOST=127.0.0.1; NET_NOTE="local (127.0.0.1): this Mac only" ;;
      auto)
        if has_addr "$VM_HOST"; then
          HOST="$VM_HOST"; NET_NOTE="VM network ($VM_HOST): reachable from the Fusion VM and this Mac"
        else
          HOST=127.0.0.1; NET_NOTE="local (127.0.0.1): no VMware network found, this Mac only (--vm to require it)"
        fi ;;
      *) echo "error: unknown network mode '$mode' (vm | local | auto)" >&2; exit 2 ;;
    esac
  fi
  case "$HOST" in
    0.0.0.0|0|::|"[::]"|"*") echo "error: refusing to bind $HOST (would expose the server to the LAN)" >&2; exit 1 ;;
    127.0.0.1|localhost|::1) ;;
    *) has_addr "$HOST" || { echo "error: no interface has $HOST" >&2; exit 1; } ;;
  esac
}

# CARL's settings folder (config.json, models.json, api-key). tools/carl.py reads
# CARL_CONF_DIR when set; the server's key stays in the default folder.
CARL_CONF="$HOME/.config/carl"
OLD_CONF="$HOME/.config/llm-deploy"        # its name before 1.2.0 (CARL was called LLM-Deploy)

# tilde PATH: PATH as ~/... for messages (bash 3.2 would print a quoted \~ as is).
tilde() { local t='~'; printf '%s' "${1/#"$HOME"/$t}"; }

# migrate_conf_dir: move the settings folder from its old name to CARL_CONF, once.
# A symlink is left at the old path: client configs written before the rename read
# the key there (client/install.sh rewrites them). Nothing happens when CARL_CONF_DIR
# picks another folder, or when CARL_CONF exists already (the old folder is then
# left alone; tools/carl.py reads CARL_CONF). Files end up 0600, the folder 0700.
migrate_conf_dir() {
  [[ -z "${CARL_CONF_DIR:-}" ]] || return 0
  [[ -d "$OLD_CONF" && ! -L "$OLD_CONF" ]] || return 0
  if [[ -e "$CARL_CONF" || -L "$CARL_CONF" ]]; then
    echo "note: both $(tilde "$CARL_CONF") and $(tilde "$OLD_CONF") exist: using $(tilde "$CARL_CONF"); the old folder is not used" >&2
    return 0
  fi
  mv "$OLD_CONF" "$CARL_CONF" || { echo "warning: could not move $(tilde "$OLD_CONF") to $(tilde "$CARL_CONF"); still using the old folder" >&2; return 0; }
  chmod 700 "$CARL_CONF"
  find "$CARL_CONF" -maxdepth 1 -type f -exec chmod go-rwx {} +
  ln -s carl "$OLD_CONF" 2>/dev/null || true
  echo "moved $(tilde "$OLD_CONF") to $(tilde "$CARL_CONF") (CARL's new name; the old path links to it)" >&2
}

# The server's Bearer key. Clients copy it: client/install.sh, or the monitor's
# CONNECT section. Its earlier places, newest first (LEGACY_KEY_FILES): the old
# settings folder (when both folders exist), and MTPLX's folder (before 1.2.0).
# The first start copies the newest one here once, so configured clients keep working.
CARL_KEY_FILE="$CARL_CONF/api-key"
LEGACY_KEY_FILES=("$OLD_CONF/api-key" "$HOME/.mtplx/api-key")

# ensure_api_key FILE: make sure FILE holds the key, readable by its owner only.
# For the default FILE, a key from an earlier place is copied over once (same
# value; the old file stays); otherwise a random key is created on first use.
ensure_api_key() {
  local f="$1" dir old
  if [[ -s "$f" ]]; then chmod go-rwx "$f" 2>/dev/null || true; return 0; fi
  dir="$(dirname "$f")"
  mkdir -p "$dir"; chmod 700 "$dir"
  if [[ "$f" == "$CARL_KEY_FILE" ]]; then
    for old in "${LEGACY_KEY_FILES[@]}"; do
      [[ -s "$old" ]] || continue
      ( umask 077; cp "$old" "$f" ) && chmod 600 "$f"
      echo "moved the API key to $f (copied from $(tilde "$old"); clients keep working)" >&2
      return 0
    done
  fi
  # pipefail off here: head closing the pipe ends tr with SIGPIPE, which would
  # fail the subshell (and, under set -e, silently end the server start).
  ( set +o pipefail; umask 077; LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 40 > "$f" )
  echo "created API key: $f (clients need it: ./carl.sh monitor shows it, or client/install.sh)" >&2
}

# ensure_deps: the Homebrew tools CARL needs (llama-server from llama.cpp,
# aria2c, ansifilter). Missing ones are installed with Homebrew after asking (in
# a terminal), or listed with the command to run. Homebrew itself is not
# installed automatically (it needs the user's password). SKIP_DEPS=1 skips this.
ensure_deps() {
  [[ "${SKIP_DEPS:-0}" == 1 ]] && return 0
  local missing=() a
  # Homebrew's bin on PATH (what `brew shellenv` adds), for shells that lack it
  [[ -x /opt/homebrew/bin/brew && ":$PATH:" != *":/opt/homebrew/bin:"* ]] && PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
  command -v llama-server >/dev/null || missing+=(llama.cpp)
  command -v aria2c >/dev/null || missing+=(aria2)
  command -v ansifilter >/dev/null || missing+=(ansifilter)
  (( ${#missing[@]} )) || return 0
  echo "CARL needs: ${missing[*]} (not installed)"
  if ! command -v brew >/dev/null; then
    echo "Install Homebrew first (https://brew.sh), then run this again:" >&2
    # shellcheck disable=SC2016  # the command is printed for the user, not run
    echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' >&2
    exit 1
  fi
  if [[ -t 0 && -t 1 ]]; then
    read -r -p "Install them now with Homebrew (brew install ${missing[*]})? [Y/n] " a
    if [[ ! "$a" =~ ^[Nn] ]]; then
      brew install "${missing[@]}" || { echo "error: brew install failed" >&2; exit 1; }
      hash -r
      command -v llama-server >/dev/null || { echo "error: llama-server still not found" >&2; exit 1; }
      return 0
    fi
  fi
  if [[ " ${missing[*]} " == *" llama.cpp "* ]]; then
    echo "error: llama-server not found. Run: brew install ${missing[*]}" >&2; exit 1
  fi
  echo "note: optional tools missing (downloads and logs work without them): brew install ${missing[*]}" >&2
}

# guard_other_models: refuse to load a second model. Two models do not fit in
# GPU memory on these Macs: the second one can crash the Mac (it rebooted on
# 2026-10-02) or break the first server. Name checks miss servers started some
# other way (an app, a renamed binary), so this checks memory: any process with
# more than BIG_GB (default 8) GB resident, plus every known server name. The
# list keeps mtplx although CARL no longer starts it: an MTPLX server left over
# from an older CARL (or started by hand) still holds a model in GPU memory.
# ALLOW_SECOND_MODEL=1 skips the check.
guard_other_models() {
  [[ "${ALLOW_SECOND_MODEL:-0}" == 1 ]] && return 0
  require_int BIG_GB "${BIG_GB:-8}" 1
  local big=$(( 10#${BIG_GB:-8} * 1024 * 1024 )) found
  found="$(ps -axo pid=,rss=,comm= 2>/dev/null | awk -v big="$big" -v me="$$" '
    $1 != me && ($2 > big || $3 ~ /(^|\/)(llama-server|mtplx|ollama|LM Studio)/) {
      printf "  pid %s, %.1f GB: %s\n", $1, $2 / 1048576, $3 }')"
  [[ -z "$found" ]] && return 0
  echo "error: another large process (probably a model) is in memory:" >&2
  echo "$found" >&2
  echo "       Two models do not fit: the second one can crash the Mac. Stop it first" >&2
  echo "       (its monitor: q then s), or set ALLOW_SECOND_MODEL=1 if it is not a model." >&2
  exit 1
}

# run_server PORT LOG_FILE CMD...: start the server and show the monitor in this
# terminal. Interactive (a terminal on stdin/stdout, MONITOR != 0):
#   - the server runs in the background in its own process group (so Ctrl-C in
#     the monitor can't reach it) under nohup (it survives a closed tab if you
#     detach); its console output goes to ~/models/logs/.console-PORT.out
#   - the monitor takes over this tab; quitting it asks: stop the server, or
#     detach and leave it running (./host/serve.sh monitor re-attaches)
# Otherwise (scripts, nohup, MONITOR=0): exec the server in the foreground.
# Either way caffeinate keeps the Mac awake while the server lives (KEEP_AWAKE=0 = off).
run_server() {
  local port="$1" log="$2"; shift 2
  local here; here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ "${MONITOR:-1}" != 0 && -t 0 && -t 1 ]]; then
    local console="$HOME/models/logs/.console-$port.out"
    mkdir -p "$(dirname "$console")"
    set -m                                   # job control: background jobs get their own process group
    nohup "$@" >"$console" 2>&1 &
    local spid=$!
    [[ "${KEEP_AWAKE:-1}" != 0 ]] && { nohup caffeinate -i -w "$spid" >/dev/null 2>&1 & }
    set +m
    local margs=(--host "$HOST" --port "$port" --server-pid "$spid" --owner --console "$console")
    [[ "$log" != none ]] && margs+=(--log "$log")
    exec python3 "$here/../tools/llama-monitor.py" "${margs[@]}"
  fi
  [[ "${KEEP_AWAKE:-1}" != 0 ]] && { caffeinate -i -w $$ & }
  exec "$@"
}
