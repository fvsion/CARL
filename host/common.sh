# Shared by host/serve.sh (MTPLX presets) and host/serve-llama.sh. Sourced, not run.
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

has_addr() { ifconfig 2>/dev/null | grep -q "inet ${1//./\\.} "; }

# resolve_host MODE -> sets HOST (and NET_NOTE for the start-up banner)
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
    0.0.0.0|::|"[::]") echo "error: refusing to bind $HOST (would expose the server to the LAN)" >&2; exit 1 ;;
    127.0.0.1|localhost|::1) ;;
    *) has_addr "$HOST" || { echo "error: no interface has $HOST" >&2; exit 1; } ;;
  esac
}

# ensure_api_key FILE: create a random Bearer key on first use (friend's Mac,
# no MTPLX installed). Clients copy it: client/install.sh, or the monitor's CONNECT section.
ensure_api_key() {
  local f="$1"
  [[ -s "$f" ]] && return 0
  mkdir -p "$(dirname "$f")"; chmod 700 "$(dirname "$f")"
  ( umask 077; LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 40 > "$f" )
  echo "created API key: $f (clients need it: ./carl.sh monitor shows it, or client/install.sh)" >&2
}

# ensure_deps [mtplx]: the Homebrew tools CARL needs (llama-server from llama.cpp,
# aria2c, ansifilter). Missing ones are installed with Homebrew after asking (in
# a terminal), or listed with the command to run. Homebrew itself is not
# installed automatically (it needs the user's password). SKIP_DEPS=1 skips this.
ensure_deps() {
  [[ "${SKIP_DEPS:-0}" == 1 ]] && return 0
  local missing=() need_llama=1 a
  [[ "${1:-}" == mtplx ]] && need_llama=0
  [[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null
  (( need_llama )) && ! command -v llama-server >/dev/null && missing+=(llama.cpp)
  command -v aria2c >/dev/null || missing+=(aria2)
  command -v ansifilter >/dev/null || missing+=(ansifilter)
  if [[ "${1:-}" == mtplx ]] && ! command -v mtplx >/dev/null; then
    echo "error: mtplx is not installed (grant / pocket need it; see USERGUIDE.md). llama.cpp: ${CMD:-./carl.sh} llama" >&2
    exit 1
  fi
  (( ${#missing[@]} )) || return 0
  echo "CARL needs: ${missing[*]} (not installed)"
  if ! command -v brew >/dev/null; then
    echo "Install Homebrew first (https://brew.sh), then run this again:" >&2
    echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' >&2
    exit 1
  fi
  if [[ -t 0 && -t 1 ]]; then
    read -r -p "Install them now with Homebrew (brew install ${missing[*]})? [Y/n] " a
    if [[ ! "$a" =~ ^[Nn] ]]; then
      brew install "${missing[@]}" || { echo "error: brew install failed" >&2; exit 1; }
      hash -r
      (( need_llama )) && ! command -v llama-server >/dev/null && { echo "error: llama-server still not found" >&2; exit 1; }
      return 0
    fi
  fi
  if (( need_llama )) && [[ " ${missing[*]} " == *" llama.cpp "* ]]; then
    echo "error: llama-server not found. Run: brew install ${missing[*]}" >&2; exit 1
  fi
  echo "note: optional tools missing (downloads and logs work without them): brew install ${missing[*]}" >&2
}

# guard_other_models: refuse to load a second model. Two models do not fit in
# GPU memory on these Macs: the second one can crash the Mac (it rebooted on
# 2026-10-02) or break the first server. Name checks miss servers started some
# other way (the MTPLX app, a renamed binary), so this checks memory: any
# process with more than BIG_GB (default 8) GB resident, plus every known server
# name. ALLOW_SECOND_MODEL=1 skips the check.
guard_other_models() {
  [[ "${ALLOW_SECOND_MODEL:-0}" == 1 ]] && return 0
  local big=$(( ${BIG_GB:-8} * 1024 * 1024 )) found
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
