# shellcheck shell=bash
# Shared by host/serve.sh and host/serve-llama.sh. Sourced, not run.
#
# Network mode -- where the server listens:
#   --vm     VM_HOST (default 192.168.42.1, VMware Fusion's vmnet8 address on
#            this Mac). Fails if that interface doesn't exist (Fusion not running).
#   --local  127.0.0.1: clients on this Mac only (OpenCode/Pi installed locally).
#   (none)   local: the default since 1.3.0. The VM network is used only when asked
#            for (--vm, NET=vm, or llama.net = vm in config.json).
#   --host ADDR / HOST=ADDR: one address of this Mac (its LAN address, or another
#            VM network such as Parallels 10.211.55.2); wins over the modes.
#            NET=vm|local is the env form of the modes; VM_HOST changes the vm address.
#            NET=auto (before 1.3.0: the VM address whenever it existed) now means local.
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
  local mode="${1:-${NET:-local}}" retired=""
  NET_NOTE=""
  if [[ "$mode" == auto ]]; then
    mode=local; retired=" NET=auto was removed in 1.3.0: use --vm for a VM."
  fi
  if [[ -n "${HOST:-}" ]]; then
    case "$HOST" in
      127.0.0.1|localhost|::1) NET_NOTE="Only this Mac can use the server." ;;
      "$VM_HOST") NET_NOTE="The VM and this Mac can use the server." ;;
      *) NET_NOTE="CAUTION: every computer that can reach this address can use the server. Only the API key protects it." ;;
    esac
  else
    case "$mode" in
      vm)
        has_addr "$VM_HOST" || {
          echo "error: --vm: no network interface has the address $VM_HOST. Start VMware Fusion first, or use --local to serve only this Mac." >&2
          exit 1; }
        HOST="$VM_HOST"; NET_NOTE="The VM network: the VM and this Mac can use the server." ;;
      local)
        HOST=127.0.0.1; NET_NOTE="Only this Mac can use the server.$retired"
        if [[ -z "$retired" ]] && has_addr "$VM_HOST"; then
          NET_NOTE+=" The VMware network is up: use --vm to serve a VM too."
        fi ;;
      *) echo "error: unknown network mode '$mode'. Use vm or local." >&2; exit 2 ;;
    esac
  fi
  case "$HOST" in
    0.0.0.0|0|::|"[::]"|"*") echo "error: CARL is refusing to listen on $HOST: every computer on the LAN could use the server. Use --local, --vm or --host with one address of this Mac." >&2; exit 1 ;;
    127.0.0.1|localhost|::1) ;;
    *) has_addr "$HOST" || { echo "error: no network interface of this Mac has the address $HOST. ifconfig shows the addresses. Or use --local." >&2; exit 1; } ;;
  esac
}

# CARL's settings folder (config.json, models.json, slots/, router-presets.ini): CARL_CONF_DIR
# when set, like tools/carl.py (domain/confdir.py), so a run with a test folder never writes
# the real one. The server's key stays in the default folder (HOME_CONF), as carl.py reads it.
HOME_CONF="$HOME/.config/carl"
CARL_CONF="${CARL_CONF_DIR:-$HOME_CONF}"
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

# The server's Bearer key. Clients copy it: client/setup (./carl.sh install, the package), or the dashboard's
# CONNECT section. Its earlier places, newest first (LEGACY_KEY_FILES): the old
# settings folder (when both folders exist), and MTPLX's folder (before 1.2.0).
# The first start copies the newest one here once, so configured clients keep working.
CARL_KEY_FILE="$HOME_CONF/api-key"
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
  echo "CARL made the API key $(tilde "$f"). The clients need it: ./carl.sh install and the client package (./carl.sh package) copy it, and the dashboard shows it (Connect, then k)." >&2
}

# carl_version: the CARL version: the newest "## X.Y.Z" heading of CHANGELOG.md, else
# git describe --tags, else "unknown" (tools/carl_core/domain/package.py reads it the same way).
carl_version() {
  local root v
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  v="$(sed -nE 's/^##[[:space:]]+v?([0-9]+[.][0-9]+[.][0-9]+)([^0-9].*)?$/\1/p' "$root/CHANGELOG.md" 2>/dev/null | head -n1)"
  [[ -n "$v" ]] || v="$(git -C "$root" describe --tags 2>/dev/null | sed -E 's/^v//')" || v=""
  [[ "$v" =~ ^[0-9A-Za-z.+-]+$ ]] || v=unknown
  printf '%s' "$v"
}

# write_client_package HOST PORT KEY_FILE: the client folder's connection file, so a copy of
# client/ (./carl.sh package zips it for another computer) needs nothing else: client/remote.json
# (the server's address and port, the dashboard API at port + 1, the CARL version) and
# client/api-key (the key), both readable by the owner only and git-ignored. Written at every
# server start (the address can change between --local and --vm). client/setup and
# client/install.sh use them when they are given no address.
write_client_package() {
  local host="$1" port="$2" key="$3" dir="$CARL_CLIENT_DIR"
  [[ -d "$dir" && -s "$key" ]] || return 0
  ( umask 077
    printf '{\n  "host": "%s",\n  "port": %s,\n  "cache_api": "http://%s:%s",\n  "version": "%s",\n  "written": "%s"\n}\n' \
      "$host" "$port" "$host" "$(( port + 1 ))" "$(carl_version)" "$(date +%Y-%m-%dT%H:%M:%S)" > "$dir/remote.json.tmp" \
      && mv "$dir/remote.json.tmp" "$dir/remote.json"
    cp "$key" "$dir/api-key.tmp" && mv "$dir/api-key.tmp" "$dir/api-key" ) || return 0
  chmod 600 "$dir/remote.json" "$dir/api-key" 2>/dev/null || true
}

# ensure_deps: the Homebrew tools CARL needs (llama-server from llama.cpp,
# aria2c, ansifilter, zstd: the disk cache's conversations stored as patches). Missing ones are installed with Homebrew after asking (in
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
  command -v zstd >/dev/null || missing+=(zstd)
  (( ${#missing[@]} )) || return 0
  echo "CARL needs these programs, and they are not installed: ${missing[*]}."
  if ! command -v brew >/dev/null; then
    echo "Install Homebrew first (https://brew.sh), then run this command again:" >&2
    # shellcheck disable=SC2016  # the command is printed for the user, not run
    echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' >&2
    exit 1
  fi
  if [[ -t 0 && -t 1 ]]; then
    read -r -p "Install them now with Homebrew (brew install ${missing[*]})? [Y/n] " a
    if [[ ! "$a" =~ ^[Nn] ]]; then
      brew install "${missing[@]}" || { echo "error: brew install failed (its output is above). Correct the cause, or run: brew install ${missing[*]}" >&2; exit 1; }
      hash -r
      command -v llama-server >/dev/null || { echo "error: CARL cannot find llama-server after brew install. Open a new terminal, then run this command again." >&2; exit 1; }
      return 0
    fi
  fi
  if [[ " ${missing[*]} " == *" llama.cpp "* ]]; then
    echo "error: CARL cannot find llama-server. Run: brew install ${missing[*]}" >&2; exit 1
  fi
  echo "note: some optional programs are not installed. Downloads and logs work without them. To install them: brew install ${missing[*]}" >&2
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
      printf "  pid %s, %.1f GiB: %s\n", $1, $2 / 1048576, $3 }')"
  [[ -z "$found" ]] && return 0
  echo "error: another large process (possibly a model) is in memory. Two models do not fit, and the second one can stop the Mac." >&2
  echo "$found" >&2
  echo "       Stop that process first. If it is a CARL server, open its dashboard (./carl.sh), press q, then s." >&2
  echo "       If it is not a model, set ALLOW_SECOND_MODEL=1." >&2
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
# CARL_LOG_FILTER=1 (a single model at -lv 4, Phase 23.4.4 item 12): the server's output goes through
# tools/llama-log-filter.py, which writes LOG_FILE (the lines CARL keeps) and passes them on to the console or the
# terminal; the server then has no --log-file.
# Either way caffeinate keeps the Mac awake while the server lives (KEEP_AWAKE=0 = off).
run_server() {
  local port="$1" log="$2"; shift 2
  local here; here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ "${MONITOR:-1}" != 0 && -t 0 && -t 1 ]]; then
    local console="$HOME/models/logs/.console-$port.out"
    mkdir -p "$(dirname "$console")"
    set -m                                   # job control: background jobs get their own process group
    if [[ "${CARL_LOG_FILTER:-0}" == 1 && "$log" != none ]]; then
      nohup "$@" > >(python3 "$here/../tools/llama-log-filter.py" --port "$port" "$log" >"$console" 2>&1) 2>&1 &
    else
      nohup "$@" >"$console" 2>&1 &
    fi
    local spid=$!
    [[ "${KEEP_AWAKE:-1}" != 0 ]] && { nohup caffeinate -i -w "$spid" >/dev/null 2>&1 & }
    set +m
    local margs=(--host "$HOST" --port "$port" --server-pid "$spid" --owner --console "$console")
    [[ "$log" != none ]] && margs+=(--log "$log")
    exec python3 "$here/../tools/llama-monitor.py" "${margs[@]}"
  fi
  [[ "${KEEP_AWAKE:-1}" != 0 ]] && { caffeinate -i -w $$ & }
  if [[ "${CARL_LOG_FILTER:-0}" == 1 && "$log" != none ]]; then
    exec "$@" > >(python3 "$here/../tools/llama-log-filter.py" --port "$port" "$log") 2>&1
  fi
  exec "$@"
}
