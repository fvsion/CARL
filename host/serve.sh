#!/usr/bin/env bash
# Entry point for the local llama.cpp server that OpenCode / Pi use, on this Mac
# (the default, 127.0.0.1) or in a VMware Fusion guest over the NAT network
# (vmnet8, with --vm). One server at a time: two models don't fit together.
#
#   ./host/serve.sh                       the dashboard: attaches to a running server on :8080, else
#                                         starts llama.cpp with its saved settings (config.json)
#   ./host/serve.sh -h | help [TOPIC]     the help of every command (tools/carl_help.py; also CMD --help)
#   ./host/serve.sh llama [opts]          llama.cpp on :8080 (host/serve-llama.sh)
#   ./host/serve.sh monitor               live dashboard (tools/llama-monitor.py); a server start in a
#                                         terminal shows it automatically (MONITOR=0 = off)
#   --local | --vm                        network mode for any server command (host/common.sh)
#   ./host/serve.sh models|download|verify   models (host/models.sh -> tools/carl.py)
#   ./host/serve.sh tune NAME             auto-tune a model for this Mac (tools/carl-tune.py)
#   ./host/serve.sh config ...            the settings file (tools/carl.py config)
#   ./host/serve.sh card NAME [set|unset]  a model's card (tools/carl.py card)
#   ./host/serve.sh cache [show|trim|clear]   the disk cache of prompt states (tools/carl.py cache)
#   ./host/serve.sh push                  publish the client config for the clients' sync service
#
# Binds to 127.0.0.1 or the vmnet8 host address (192.168.42.1), never 0.0.0.0:
# with the macOS firewall off, 0.0.0.0 would expose the model on the LAN.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CMD="${CARL_CMD:-./host/serve.sh}"   # how the user started us (./carl.sh sets it), for the help text
# The help of every command (tools/carl_help.py: ASD-STE100 text that wraps to COLUMNS, else the
# terminal's width, else 80 columns). An unknown topic: an error and exit 2.
show_help() {
  local t="${1:-main}"
  [[ "$t" == --no-start ]] && t=dashboard
  CARL_CMD="$CMD" exec python3 "$HERE/../tools/carl_help.py" "$t"
}

# install [both|opencode|pi] [--local|--vm [HOST]|--host ADDR] [--port N] [--clients-only|--config-only]:
# the clients (client/install-clients.sh) and their configs (client/install.sh)
# in one step. Network options and env switches (CODER, NO_SWITCHER, LLAMA_CTX…)
# go to install.sh unchanged.
client_install() {
  local what=both steps=both net=() a
  while [[ $# -gt 0 ]]; do
    a="$1"
    case "$a" in
      both|opencode|pi) what="$a" ;;
      --clients-only) steps=clients ;;
      --config-only) steps=config ;;
      --vm) net+=(--vm); if [[ "${2:-}" =~ ^[0-9.]+$ ]]; then net+=("$2"); shift; fi ;;
      --host) net+=(--host "${2:?--host needs an address}"); shift ;;
      --local) net+=(--local) ;;
      --port) net+=(--port "${2:?--port needs a port}"); shift ;;
      --key-file) net+=(--key-file "${2:?--key-file needs a file}"); shift ;;
      --key) net+=(--key "${2:?--key needs the key}"); shift ;;
      *) echo "error: install does not know '$a'. Run $CMD install --help to see the options." >&2; return 2 ;;
    esac
    shift
  done
  local c="$HERE/../client"
  if [[ "$steps" != config ]]; then
    echo "CARL installs the clients ($what)."
    "$c/install-clients.sh" "$what" || return $?
  fi
  if [[ "$steps" != clients ]]; then
    echo; echo "CARL writes the client configs."
    "$c/install.sh" ${net[@]+"${net[@]}"} || return $?
  fi
  echo; echo "Done. Open a new terminal, then run opencode or pi."
}

# ---- dispatch -------------------------------------------------------------
# shellcheck source=SCRIPTDIR/common.sh
source "$HERE/common.sh"
LLAMA_PORT=8080
# The settings folder's old name, moved once before anything reads it (not for
# help, nor for a command that doesn't exist).
case "${1:-}" in
  help|-h|--help|--help-adv|grant|pocket) ;;
  ""|dashboard|--no-start|install|monitor|fit|models|config|cache|push|card|tune|download|verify|delete|llama|-*) migrate_conf_dir ;;
esac
if [[ $# -eq 0 ]]; then
  # No arguments: the dashboard. Attach to a server that runs already (one model
  # at a time), else start llama.cpp with its saved settings.
  if [[ -n "$(port_pid "$LLAMA_PORT")" ]]; then
    exec python3 "$HERE/../tools/llama-monitor.py" --port "$LLAMA_PORT"
  fi
  ensure_deps
  # No model yet (a fresh clone): offer auto fit's pick, else open the
  # dashboard without a server (its Settings tab starts one later).
  echo "CARL looks for the downloaded models."
  if [[ -z "$("$HERE/models.sh" downloaded)" ]]; then
    # Auto fit's pick from the whole catalogue (./carl.sh fit says why); nothing fits: say so.
    echo "No model is downloaded. CARL looks for the best model for this Mac. This can take 1 min."
    if ! d="$(python3 "$HERE/../tools/llama-fit.py" --pick-default)" || [[ -z "$d" ]]; then
      echo "No model fits this Mac. The dashboard opens without a server. $CMD fit shows what fits."
      exec python3 "$HERE/../tools/llama-monitor.py" --port "$LLAMA_PORT"
    fi
    echo "Auto fit chooses $d for this Mac ($("$HERE/models.sh" get "$d" bytes | awk '{printf "%.1f GB", $1/1e9}') to download). $CMD fit tells why."
    a=n
    [[ -t 0 ]] && read -r -p "Download it now? [Y/n] " a
    if [[ -t 0 && ! "$a" =~ ^[Nn] ]]; then
      "$HERE/models.sh" download "$d" || exit 1
    else
      echo "The dashboard opens without a server. To download the model later: $CMD download default"
      exec python3 "$HERE/../tools/llama-monitor.py" --port "$LLAMA_PORT"
    fi
  fi
  echo "CARL starts the server with your saved settings ($CMD config show). Help: $CMD -h"
  set -- llama
fi
case "$1" in
  help|-h|--help) show_help "${2:-}" ;;
  # The MTPLX presets were removed in 1.2.0: say where to go instead of "unknown command".
  grant|pocket) echo "error: '$1' (an MTPLX preset) was removed in CARL 1.2.0. CARL runs llama.cpp only. Use $CMD llama." >&2; exit 2 ;;
esac
# "<command> -h|--help" -> that command's help (--help-adv is handled below)
for a in "${@:2}"; do
  case "$a" in
    -h|--help) if [[ "$1" == -* && "$1" != --no-start ]]; then show_help llama; else show_help "$1"; fi ;;
  esac
done
case "$1" in
  dashboard|--no-start)
    # The dashboard without starting a server: attach to one that runs, else
    # open it offline (its Settings tab can start a server).
    shift; exec python3 "$HERE/../tools/llama-monitor.py" --port "$LLAMA_PORT" "$@" ;;
  install) shift; client_install "$@"; exit $? ;;
  monitor) shift; exec python3 "$HERE/../tools/llama-monitor.py" "$@" ;;
  fit) shift; exec python3 "$HERE/../tools/llama-fit.py" "$@" ;;
  models) shift; exec "$HERE/models.sh" list "$@" ;;
  config) shift; exec python3 "$HERE/../tools/carl.py" config "$@" ;;
  cache) shift; exec python3 "$HERE/../tools/carl.py" cache "$@" ;;
  push) exec python3 "$HERE/../tools/carl.py" push ;;
  card) shift; exec python3 "$HERE/../tools/carl.py" card "$@" ;;
  tune) shift; exec python3 "$HERE/../tools/carl-tune.py" "$@" ;;
  download|verify|delete) exec "$HERE/models.sh" "$@" ;;
  llama) shift ;;
  -*) ;;                                    # a leading flag means llama
  *) echo "error: unknown command '$1'. Run $CMD -h to see the commands." >&2; exit 2 ;;
esac

for arg in "$@"; do
  if [[ "$arg" == --help-adv ]]; then
    echo "# The flags of llama-server (llama.cpp). CARL sets some of them. CARL gives every"
    echo "# other flag on its command line to llama-server. CARL does not use some features,"
    echo "# for example -hf (a download from Hugging Face), multimodal input and LoRA."
    echo
    llama-server --help
    exit 0
  fi
done

exec "$HERE/serve-llama.sh" "$@"
