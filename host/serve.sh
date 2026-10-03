#!/usr/bin/env bash
# shellcheck disable=SC2088  # help texts show paths as ~/...: printed, not expanded
# Entry point for the local llama.cpp server that OpenCode / Pi in the VMware
# Fusion guest use over the NAT network (vmnet8). One server at a time: two
# models don't fit in 36 GB together.
#
#   ./host/serve.sh                       the dashboard: attaches to a running server on :8080, else
#                                         starts llama.cpp with its saved settings (config.json)
#   ./host/serve.sh -h | help [TOPIC]     help per command: llama monitor models env tuning
#   ./host/serve.sh llama [opts]          llama.cpp on :8080 (host/serve-llama.sh)
#   ./host/serve.sh monitor               live dashboard (tools/llama-monitor.py); a server start in a
#                                         terminal shows it automatically (MONITOR=0 = off)
#   --local | --vm                        network mode for any server command (host/common.sh)
#   ./host/serve.sh models|download|verify   models (host/models.sh -> tools/carl.py)
#   ./host/serve.sh tune NAME             auto-tune a model for this Mac (tools/carl-tune.py)
#   ./host/serve.sh config ...            the settings file (tools/carl.py config)
#
# Binds to the vmnet8 host address (192.168.42.1) or 127.0.0.1, never 0.0.0.0:
# with the macOS firewall off, 0.0.0.0 would expose the model on the LAN.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CMD="${CARL_CMD:-./host/serve.sh}"   # how the user started us (./carl.sh sets it), for the help text
# Default of VAR in serve-llama.sh (VAR="${VAR:-default}"), for the help text.
llama_default() { sed -n "s/^$1=\"\${$1:-\([^}]*\)}\".*/\1/p" "$HERE/serve-llama.sh"; }
row() { printf '  %-24s %s\n' "$1" "$2"; }
use() { printf '  %-36s %s\n' "$1" "$2"; }

help_main() {
  cat <<EOF
CARL (Can't Afford Remote LLMs) -- run a local LLM server on this Mac for OpenCode / Pi, used from a
VMware Fusion VM or from this Mac itself. One server at a time.

USAGE
$(use "$CMD" "the dashboard: attach to the running server, or start llama.cpp")
$(use "" "with its saved settings (the first time: the defaults)")
$(use "$CMD <command> [options]" "")
$(use "$CMD -h | help <command>" "this page / detailed help for one command")
$(use "$CMD <command> --help" "same")

SERVER COMMAND
$(row "llama" "llama.cpp server on :8080. Defaults: model qwen3.6-35b-a3b (fast MoE; IQ3 on 24 GB), q4_0 KV,")
$(row "" "96K per slot, 2 slots when they fit (main session + a subagent), RAM cache sized to free memory.")
$(row "" "Shows the live monitor in this terminal.")

TOOLS
$(row "dashboard, --no-start" "the dashboard only: attach to a running server, else open it offline (no model")
$(row "" "loads; its Settings tab can start one)")
$(row "install [opencode|pi]" "install OpenCode and/or Pi, then point them at this server (--vm / --host for")
$(row "" "another address; --clients-only / --config-only for one step)")
$(row "monitor" "attach the live dashboard to a running server (connect info, configs, stats)")
$(row "models" "list the models (catalogue + models folder + custom) and what's downloaded")
$(row "fit [--ram GB] [--slots N]" "which models fit this Mac's GPU memory, and the largest window for each")
$(row "download NAME|default|all" "download catalogue models (resumable, SHA-256 verified); default = this Mac's default")
$(row "download hf:OWNER/REPO" "ANY model from Hugging Face: lists the repo's GGUF files; then")
$(row "" "download hf:OWNER/REPO/FILE.gguf (a huggingface.co URL works too; SHA-256 verified).")
$(row "" "Any .gguf you put in ~/models/gguf also shows up as a model. In the dashboard:")
$(row "" "Settings (5) → ] Models panel → Add from Hugging Face (h)")
$(row "verify [NAME...]" "re-check downloaded models' size and SHA-256 (no names: all of them)")
$(row "delete NAME" "delete a downloaded model file")
$(row "tune NAME [--quick]" "auto-tune a model for this Mac: speculation, context window, slots (~5-10 min)")
$(row "config [show|set K V]" "the settings file ~/.config/carl/config.json (show lists every key)")
$(row "help [TOPIC]" "this page, or: llama monitor fit models download verify env tuning")

NETWORK (llama, and $CMD without arguments)
$(row "--vm" "listen on 192.168.42.1 (VMware Fusion); fails if Fusion's network is down")
$(row "--local" "listen on 127.0.0.1: OpenCode/Pi on this Mac only")
$(row "--host ADDR" "listen on one address of this Mac, e.g. its LAN address (other computers) or a")
$(row "" "Parallels / other VM network (10.211.55.2). It must exist on an interface; never 0.0.0.0")
$(row "(neither)" "auto: the VM address if Fusion's network is up, otherwise local")

DEFAULTS YOU GET (no flags needed)
  Model qwen3.6-35b-a3b (the IQ3 build on 24 GB Macs), q4_0 KV cache, 2 slots (auto):
  the recommended setup for OpenCode with subagents. Each conversation keeps its own
  cache, so the main session isn't re-read after a subagent. Each slot holds 96K tokens.
  The start-up banner shows what was chosen, e.g.
    slots: 2 (auto) x 98304 tokens, KV q4_0/q4_0, RAM prompt cache 2560 MiB
  Opt out: --slots 1 (one conversation, less memory) | --kv q8 (more KV memory, fewer slots fit)

QUICK START
  $CMD llama                          # default model; monitor opens in this terminal
  $CMD llama --local                  # this Mac only (no VM)
  $CMD llama --model qwen3.8-27b      # the dense 27B (slower; orcarouter-27b = abliterated)
  $CMD download hf:Qwen/Qwen3-0.6B-GGUF                     # list a Hugging Face repo's GGUF files
  $CMD download hf:Qwen/Qwen3-0.6B-GGUF/Qwen3-0.6B-Q8_0.gguf  # download one (then: $CMD llama --model qwen3-0.6b-q8_0)
  $CMD tune qwen3-0.6b-q8_0           # tune it for this Mac
  $CMD llama --ctx 192k --kv q8       # bigger window, q8_0 KV
  $CMD monitor                        # re-attach after "leave running"

The monitor's CONNECT section shows the URL and API key and copies ready-made
OpenCode / Pi configs. Its Settings tab (5) changes the model, KV cache, context,
slots, RAM cache, network and sampling, saves them to ~/.config/carl/config.json
and restarts the server; $CMD llama then starts with them (flags still
win; delete the file for the defaults). Quit (q, Ctrl-C or [ Quit ]) asks: stop
the server, or leave it running. Client setup: client/install.sh (in the VM) or client/install.sh --local.

No arguments opens the dashboard (see USAGE); -h prints this page. A first argument starting with "-" means "llama".
Docs: README.md (overview), USERGUIDE.md (how-to), REFERENCE.md (details).
EOF
}

help_llama() {
  cat <<EOF
llama -- llama.cpp (llama-server) on :8080 (192.168.42.1 or 127.0.0.1)

USAGE
  $CMD llama [options] [llama-server flags...]
  $CMD [options]                    (same: a leading flag means llama)

OPTIONS
$(row "--model NAME|PATH" "a model name (see: models, fit) or a .gguf path. Default: qwen3.6-35b-a3b (qwen3.6-35b-a3b-iq3 where it doesn't fit)")
$(row "--kv q4|q8" "KV cache quantization for K and V (default q4 = q4_0). --q4 / --q8 shorthands")
$(row "--ctx N|Nk" "window per slot, 4k..256k (default 96k; 128k-160k for long sessions, slower: see REFERENCE.md). Re-run client/install.sh after changing it")
$(row "--local | --vm" "listen on 127.0.0.1 / on 192.168.42.1 (default: auto, see 'help env')")
$(row "--slots N|auto" "parallel conversations (default auto = 2 if they fit, else 1). 2 = main session + a subagent, each with its own cache and the full --ctx window; KV memory x N")
$(row "--help-adv" "every llama-server flag (anything else you pass goes to llama-server)")
$(row "-h, --help" "this page")

ENVIRONMENT (current defaults; flags win over env)
$(row "CTX=98304" "window per slot in tokens")
$(row "KV=q4_0" "KV type for both caches: q4_0, q8_0, f16")
$(row "KV_K / KV_V" "per-cache override. Keep them equal: mixed types prefill ~5x slower")
$(row "UB=$(llama_default UB)" "-ub physical batch (512 measured best)")
$(row "SPEC / SPEC_N" "speculation type and draft count (default per model: catalogue, Auto-tune, config.json)")
$(row "TEMP TOP_P TOP_K MIN_P" "sampling: 1.0 0.95 20 0 (Qwen thinking mode). PRESENCE=0 (presence penalty)")
$(row "SLOTS, CACHE_RAM" "slots as --slots; RAM prompt cache in MiB (default: sized from free RAM, 1-8 GiB)")
$(row "RESERVE_GB" "RAM kept free for macOS + apps when sizing the cache (10 with the VM network up, else 6)")
$(row "MODEL=PATH, ALIAS" "model file / served model id")
$(row "LOG_FILE" "~/models/logs/llama-server-<ts>.log; none = off")
$(row "THINK_TOGGLE=1" "patched chat template (reasoning_effort none = thinking off); 0 = stock")
$(row "MONITOR=1" "show the live monitor in this terminal (server runs in the background); 0 = plain foreground server")
$(row "KEEP_AWAKE=1" "caffeinate while serving; 0 = off")
$(row "" "plus HOST, PORT, API_KEY_FILE: see 'help env'")

BEHAVIOUR
  Refuses to start if the port is in use (another server is running).
  Warns before loading if the model + window won't fit the GPU memory (see: fit; FIT_CHECK=0 skips).
  Creates the API key ~/.config/carl/api-key on first use if it doesn't exist.
  In a terminal: the server runs in the background (output in its log file) and the
  monitor runs here; quitting asks stop-or-leave-running. Not a terminal (scripts,
  nohup) or MONITOR=0: the server runs in the foreground as before.
  Flash attention on; prompt checkpoints 8 x 4K; 2 GB RAM prompt cache; --parallel 1.

EXAMPLES
  $CMD llama
  $CMD llama --model qwen3.6-35b-a3b
  $CMD llama --kv q8 --ctx 96k
  $CMD llama --slots 1              # one conversation only (saves KV memory)
  $CMD llama --model ~/models/gguf/Other.gguf
  $CMD llama --local
  SPEC=none MONITOR=0 $CMD llama
EOF
}

help_monitor() { exec python3 "$HERE/../tools/llama-monitor.py" --help; }

help_models() {
  cat <<EOF
models | download | verify | delete -- the catalogue (host/catalog.json), files in ~/models/gguf

USAGE
  $CMD models                    list models, sizes, download status, free disk
  $CMD download NAME [NAME...]   download (aria2c 16 connections, resumable), verify SHA-256
  $CMD download all              every catalogue model
  $CMD verify [NAME...]          re-check size and SHA-256 of downloaded files

  A file failing its checksum is renamed *.bad. Re-running a download resumes it.
  Any GGUF:  $CMD download hf:OWNER/REPO/FILE.gguf (verified against Hugging Face's SHA-256);
             $CMD download hf:OWNER/REPO lists the repo's files. Or drop a .gguf into ~/models/gguf.
  Built-in models: host/catalog.json. Custom models and Auto-tune results: ~/.config/carl/models.json.
  $CMD delete NAME               delete a downloaded model file
  Env: MODELS_DIR (default ~/models/gguf; config.json paths.models_dir).
EOF
}

help_env() {
  cat <<EOF
Server settings from the environment (VAR=value $CMD llama; flags win)

$(row "NET=auto" "auto | vm | local (same as --vm / --local). auto = VM address if present, else 127.0.0.1")
$(row "VM_HOST=192.168.42.1" "the VMware Fusion vmnet8 address used by --vm / auto")
$(row "HOST" "explicit bind address (wins over NET). 0.0.0.0 is refused: the LAN could reach it")
$(row "PORT" "8080")
$(row "API_KEY_FILE" "~/.config/carl/api-key, the server's Bearer key (created if missing)")
$(row "KEEP_AWAKE=1" "caffeinate -i while the server runs (a sleeping Mac stalls requests)")
$(row "MONITOR=1" "show the monitor in this terminal on start; 0 = foreground server, no monitor")
$(row "ALLOW_SECOND_MODEL=1" "start although a process > 8 GB (BIG_GB) is in memory; a second model can crash the Mac")
$(row "SETTINGS_FILE=none" "ignore ~/.config/carl/config.json (flags > env > config.json > Auto-tune > catalogue)")
$(row "MODELS_DIR" "the models folder (default ~/models/gguf; config.json paths.models_dir)")
EOF
}

help_tuning() {
  cat <<EOF
Tuning notes (measured on this M3 Pro 36 GB; details in REFERENCE.md)

  Subagents      2 slots: follow-up after a subagent 0.6 s (35B) / 1.9 s (27B), cache kept in place.
                 Both generating at once: 35B +39% combined (40.6 tok/s); 27B time-shares (no gain).
  Decode         27B: MTP(n=1)+ngram best, ~10.5-11 tok/s new text, ~27 re-emitting (none 7.4).
                 35B-A3B: MTP(n=2)+ngram, 42-45 tok/s new text, 117 re-emitting.
                 Both slow down deep into a session (27B ~7 tok/s at 60-80K, 35B ~20 at 48K).
  Prompt reading 27B ~85-90 tok/s at 2-9K, ~56-65 at 66K. 35B ~490-580 at 2-9K.
  KV cache       q4_0 vs q8_0 at 66K: same decode, +16% prefill, -2 GB, 8/8 recall both.
                 Never mix K/V types (~5x slower prefill).
  Context        96K per slot by default. 35B at 64K: cold read 229 tok/s, decode 20; at 128K: 117 / 13.9
                 (recall 8/8 at both). Longer windows work but every cold re-read takes longer.
  Memory         KV allocated up front. 27B: ~20 GB RSS at 128K q4. 35B: ~22.8 GB.
                 $CMD fit shows what fits this Mac (fit --ram 24: a 24 GB Mac).
  Reasoning      27B: low/medium/xhigh (default low), none = off. 35B: on/off only.
EOF
}

show_help() {
  case "${1:-}" in
    ""|main) help_main ;;
    llama) help_llama ;;
    monitor) help_monitor ;;
    fit) exec python3 "$HERE/../tools/llama-fit.py" --help ;;
    models|download|verify|delete) help_models ;;
    env) help_env ;;
    tuning) help_tuning ;;
    *) echo "no help topic '$1'" >&2; help_main; exit 2 ;;
  esac
}

# install [both|opencode|pi] [--local|--vm [HOST]|--host ADDR] [--clients-only|--config-only]:
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
      --key-file) net+=(--key-file "${2:?--key-file needs a file}"); shift ;;
      --key) net+=(--key "${2:?--key needs the key}"); shift ;;
      *) echo "error: install takes [both|opencode|pi] [--local|--vm [HOST]|--host ADDR] [--key-file FILE|--key KEY] [--clients-only|--config-only], got '$a'" >&2; return 2 ;;
    esac
    shift
  done
  local c="$HERE/../client"
  if [[ "$steps" != config ]]; then
    echo "== installing the clients ($what) =="
    "$c/install-clients.sh" "$what" || return $?
  fi
  if [[ "$steps" != clients ]]; then
    echo; echo "== writing the client configs =="
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
  ""|dashboard|--no-start|install|monitor|fit|models|config|tune|download|verify|delete|llama|-*) migrate_conf_dir ;;
esac
if [[ $# -eq 0 ]]; then
  # No arguments: the dashboard. Attach to a server that runs already (one model
  # at a time), else start llama.cpp with its saved settings.
  if [[ -n "$(port_pid "$LLAMA_PORT")" ]]; then
    exec python3 "$HERE/../tools/llama-monitor.py" --port "$LLAMA_PORT"
  fi
  ensure_deps
  # No model yet (a fresh clone): offer this Mac's default, else open the
  # dashboard without a server (its Settings tab starts one later).
  if [[ -z "$("$HERE/models.sh" downloaded)" ]]; then
    d="$(python3 "$HERE/../tools/llama-fit.py" --pick-default 2>/dev/null || true)"; d="${d:-$("$HERE/models.sh" default)}"
    echo "No model is downloaded yet. This Mac's default: $d ($("$HERE/models.sh" get "$d" bytes | awk '{printf "%.1f GB", $1/1e9}'))."
    a=n
    [[ -t 0 ]] && read -r -p "Download it now? [Y/n] " a
    if [[ -t 0 && ! "$a" =~ ^[Nn] ]]; then
      "$HERE/models.sh" download "$d" || exit 1
    else
      echo "Opening the dashboard without a server. Later: $CMD download default"
      exec python3 "$HERE/../tools/llama-monitor.py" --port "$LLAMA_PORT"
    fi
  fi
  echo "starting llama.cpp (saved settings: $CMD config show); $CMD -h for help"
  set -- llama
fi
case "$1" in
  help|-h|--help) show_help "${2:-}"; exit 0 ;;
  # The MTPLX presets were removed in 1.2.0: say where to go instead of "unknown command".
  grant|pocket) echo "error: '$1' (an MTPLX preset) was removed in CARL 1.2.0: CARL runs llama.cpp only. Use: $CMD llama (see CHANGELOG.md)" >&2; exit 2 ;;
esac
# "<command> -h|--help" -> that command's help (--help-adv is handled below)
for a in "${@:2}"; do
  case "$a" in
    -h|--help) if [[ "$1" == -* ]]; then show_help llama; else show_help "$1"; fi; exit 0 ;;
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
  tune) shift; exec python3 "$HERE/../tools/carl-tune.py" "$@" ;;
  download|verify|delete) exec "$HERE/models.sh" "$@" ;;
  llama) shift ;;
  -*) ;;                                    # a leading flag means llama
  *) echo "error: unknown command '$1'" >&2; echo >&2; help_main >&2; exit 2 ;;
esac

for arg in "$@"; do
  if [[ "$arg" == --help-adv ]]; then
    echo "# llama-server --help (llama.cpp's full server option list). These scripts only"
    echo "# use a handful; options such as -hf/--hf-repo (download a GGUF from Hugging Face),"
    echo "# multimodal, LoRA and router/multi-model flags are llama.cpp features we don't use."
    echo
    llama-server --help
    exit 0
  fi
done

exec "$HERE/serve-llama.sh" "$@"
