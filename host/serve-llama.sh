#!/usr/bin/env bash
# Serve a GGUF model (default: auto fit's pick for this Mac, ./carl.sh fit) with
# llama.cpp (llama-server) to this Mac and the VMware Fusion guest, with a
# quantized KV cache (q4_0 by default) and 2 slots when they fit.
#
#   ./host/serve-llama.sh [--model NAME|PATH] [--kv q4|q8 | --q4 | --q8] [--ctx N|Nk] [--local|--vm] [extra llama-server flags...]
#
#   --model NAME       a model from the catalogue or the models folder (./carl.sh models;
#                      fetch with ./host/serve.sh download NAME). Default: config.json
#                      llama.model, else auto fit's pick (the best downloaded one that fits).
#   --model PATH.gguf  any local GGUF
#
#   --kv q4 (default)  q4_0 K+V: +16% cold prefill, ~2 GB less RAM, same decode,
#                      8/8 needle recall at 66K (tools/llama-ab.sh, 2026-09-24)
#   --kv q8            q8_0 K+V: near-lossless; pick it for max long-range fidelity
#   --ctx N | Nk       context window per slot in tokens (e.g. 131072 or 128k;
#                      k = 1024). Default 96k. Clients take it from /props when
#                      install.sh runs; 128k-160k work but read and decode slower.
#
# Network (host/common.sh): --vm = VMware's 192.168.42.1, --local = 127.0.0.1
# (the default). Never 0.0.0.0.
#
# Model switching: --single (the default) runs one model; --router (or config.json
# llama.mode = router, LLAMA_MODE=router) runs llama.cpp's router: every downloaded
# model that fits, one loaded at a time, the one a client asks for (OpenCode /models).
# Each model gets the settings a single start of it would use (config.json > Auto-tune
# > catalogue: tools/carl.py router-preset); --model, --ctx, --kv and --slots don't
# apply to a router start.
# The API key file (~/.config/carl/api-key) is created on first use if missing
# (ensure_api_key in host/common.sh; it copies a key from an earlier place once).
# Interactive starts show the live monitor in this terminal; quitting it asks
# whether to stop the server or leave it running.
# Override via env, e.g.  CTX=163840 SPEC=ngram-mod ./host/serve-llama.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# Script flags (consumed here); everything else passes through to llama-server.
KV_FLAG=""
CTX_FLAG=""
SLOTS_FLAG=""
MODEL_FLAG=""
NET_FLAG=""
HOST_FLAG=""
MODE_FLAG=""
pass=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help|--help-adv) exec "$HERE/serve.sh" llama "$1" ;;
    --local) NET_FLAG=local ;;
    --vm) NET_FLAG=vm ;;
    --router) MODE_FLAG=router ;;
    --single) MODE_FLAG=single ;;
    --host|--host=*)
      v="${1#--host}"; v="${v#=}"
      if [[ -z "$v" ]]; then shift; v="${1:-}"; fi
      [[ -n "$v" ]] || { echo "error: --host needs an address." >&2; exit 2; }
      HOST="$v"; HOST_FLAG=1 ;;
    --q4) KV_FLAG=q4_0 ;;
    --q8) KV_FLAG=q8_0 ;;
    --kv|--kv=*)
      v="${1#--kv}"; v="${v#=}"
      if [[ -z "$v" ]]; then shift; v="${1:-}"; fi
      case "$v" in
        q4|q4_0) KV_FLAG=q4_0 ;;
        q8|q8_0) KV_FLAG=q8_0 ;;
        f16|bf16) KV_FLAG="$v" ;;
        *) echo "error: --kv takes q4 or q8 (or f16), not '$v'." >&2; exit 2 ;;
      esac ;;
    --model|--model=*)
      v="${1#--model}"; v="${v#=}"
      if [[ -z "$v" ]]; then shift; v="${1:-}"; fi
      [[ -n "$v" ]] || { echo "error: --model needs a model name or the path of a .gguf file." >&2; exit 2; }
      MODEL_FLAG="$v" ;;
    --slots|--slots=*)
      v="${1#--slots}"; v="${v#=}"
      if [[ -z "$v" ]]; then shift; v="${1:-}"; fi
      [[ "$v" =~ ^([1-4]|auto)$ ]] || { echo "error: --slots takes 1 to 4 or auto, not '$v'." >&2; exit 2; }
      SLOTS_FLAG="$v" ;;
    --ctx|--ctx=*)
      v="${1#--ctx}"; v="${v#=}"
      if [[ -z "$v" ]]; then shift; v="${1:-}"; fi
      v="$(printf '%s' "$v" | tr '[:upper:]' '[:lower:]')"
      if [[ "$v" =~ ^([0-9]{1,6})k$ ]]; then CTX_FLAG=$(( 10#${BASH_REMATCH[1]} * 1024 ))
      elif [[ "$v" =~ ^[0-9]{1,9}$ ]]; then CTX_FLAG=$(( 10#$v ))
      else echo "error: --ctx takes a number of tokens (for example 131072) or Nk (for example 128k), not '$v'." >&2; exit 2; fi
      if (( CTX_FLAG < 4096 || CTX_FLAG > 262144 )); then
        echo "error: --ctx must be from 4k to 256k tokens, not $CTX_FLAG." >&2; exit 2
      fi ;;
    *) pass+=("$1") ;;
  esac
  shift
done
set -- ${pass[@]+"${pass[@]}"}

# shellcheck source=SCRIPTDIR/common.sh
source "$HERE/common.sh"
migrate_conf_dir                 # the settings folder's old name, once (host/common.sh)
ensure_deps                      # llama-server, aria2, ansifilter (asks to brew install them)

# Model and settings from tools/carl.py: config.json (monitor Settings tab, or
# ./carl.sh config), this Mac's Auto-tune result, the catalogue (host/catalog.json).
# Precedence: flags > environment > config.json > Auto-tune > catalogue > defaults.
# Model: --model flag > MODEL env (a path) > config llama.model > auto fit's pick for
# this Mac (llama.auto_goal / llama.auto_fit; the best downloaded stock model that fits
# when the pick isn't downloaded).
# SETTINGS_FILE=none ignores config.json (catalogue and Auto-tune only).
# The checks below read GGUF headers (and, for a model that is not downloaded, Hugging Face):
# say so in a terminal, so a slow first start shows progress (Phase 21 audit Z4).
[[ -t 1 ]] && echo "CARL reads the model and checks the memory..."
model_arg="${MODEL_FLAG:-${MODEL:-}}"
# Where each reported setting comes from (the start lines): a flag, the environment, else what
# carl.py says (CARL_SOURCES: config, Auto-tune, catalogue, the model file, Auto fit, default).
ENV_SET=" "
for v in CTX KV SPEC SLOTS; do [[ -n "${!v:-}" ]] && ENV_SET+="$v "; done
carl_args=(launch-env); [[ -n "$model_arg" ]] && carl_args+=(--model "$model_arg")
[[ "${SETTINGS_FILE:-}" == none ]] && carl_args+=(--no-config)
CARL_ENV="$(python3 "$HERE/../tools/carl.py" "${carl_args[@]}")" || exit 1
CARL_SOURCES=""; DRAFT=""; MTP_SOURCE=""
# MODEL, MODEL_NAME, CARL_SOURCES, DRAFT and MTP_SOURCE: carl.py resolved them for the model
# (flag/env included); for the other keys the environment wins.
apply_settings "MODEL|MODEL_NAME|CARL_SOURCES|DRAFT|MTP_SOURCE|ALIAS|KV|CTX|SLOTS|SPEC|SPEC_N|TEMP|TOP_P|TOP_K|MIN_P|PRESENCE|REPEAT|NET|HOST|CACHE_RAM|UB|BATCH|CKPT|CKPT_STEP|THINK_TOGGLE|EXTRA_ARGS|LLAMA_MODE|SWA_MODE" \
  "MODEL|MODEL_NAME|CARL_SOURCES|DRAFT|MTP_SOURCE" <<< "$CARL_ENV"
LLAMA_MODE="${MODE_FLAG:-${LLAMA_MODE:-single}}"
case "$LLAMA_MODE" in single|router) ;; *) echo "error: LLAMA_MODE takes single or router, not '$LLAMA_MODE'." >&2; exit 2 ;; esac
[[ -f "$MODEL" ]] || { echo "error: the model file $MODEL does not exist. ./carl.sh models lists the models; ./carl.sh download NAME downloads one." >&2; exit 1; }
ALIAS="${ALIAS:-$(basename "$MODEL" .gguf)}"
# config.json llama.extra_args: more llama-server flags (command-line extras still
# come last). One string of space-separated words (carl.py allows no quotes or
# glob characters), split into an array: no globbing, no evaluation.
extra_args=()
[[ -n "${EXTRA_ARGS:-}" ]] && read -r -a extra_args <<< "$EXTRA_ARGS"
set -- ${extra_args[@]+"${extra_args[@]}"} "$@"
# --local / --vm win over an address saved in config.json (llama.host) or set in the environment;
# --host wins over everything
[[ -n "$NET_FLAG" && -z "$HOST_FLAG" ]] && HOST=""
resolve_host "$NET_FLAG"
PORT="${PORT:-8080}"
is_port "$PORT" || { echo "error: PORT must be a TCP port from 1 to 65535, not '$PORT'." >&2; exit 2; }
API_KEY_FILE="${API_KEY_FILE:-$CARL_KEY_FILE}"
CTX="${CTX_FLAG:-${CTX:-98304}}"   # per slot: --ctx flag > CTX env > 96K. Measured on the 35B (q4_0 KV, 2026-10-01):
                                   # 64K reads a cold prompt at 229 tok/s, decodes 20 tok/s; 128K: 117 / 13.9.
                                   # Up to 128K-160K works (8/8 recall); reference/memory.md lists the costs.
# Parallel slots (subagents). Default "auto": 2 slots when two full --ctx
# windows fit the GPU memory (tools/llama-fit.py --plan), else 1. Each
# conversation (the main OpenCode session, a subagent) keeps its own slot, so the
# main session's cache survives a subagent run. Measured 2026-10-01:
#   - follow-up after a subagent: 0.6 s (35B) / 1.9 s (27B), fully cached in place
#   - both generating at once: 35B-A3B 40.6 tok/s combined (+39%); 27B dense
#     8.8 vs 9.6 alone (it time-shares, no gain); MTP drafting works in both slots
# One unified KV pool of SLOTS x CTX (--kv-unified), each slot capped at CTX;
# --no-cache-idle-slots keeps an idle slot resident instead of parking/clearing it
# (llama.cpp's default with unified KV), and -sps 0.5 stops a subagent that shares
# the system prompt (~20% of its tokens) from taking the main session's slot.
SLOTS="${SLOTS_FLAG:-${SLOTS:-auto}}"
[[ "$SLOTS" == auto ]] || require_int SLOTS "$SLOTS" 1 4
require_int CTX "$CTX" 4096 262144

KV="${KV_FLAG:-${KV:-q4_0}}"    # --kv flag > KV env > default q4_0
KV_K="${KV_K:-$KV}"             # per-cache overrides. Keep K and V the SAME type: mixed
                                # (q8_0/q4_0) prefills ~5x slower on Metal (measured)
KV_V="${KV_V:-$KV}"
UB="${UB:-512}"                 # -ub physical batch; 512 measured best (90.5 vs 88.6/86.1 tok/s for 1024/2048)
# Speculative decoding (tools/llama-spec-sweep.sh, 400-tok gen, tok/s prose/code/edit):
#   none 7.4/7.3/7.3 | draft-mtp n1 11.1/11.4/11.4 | n2 9.0/9.6/11.5 | n3 6.6/7.7/9.8
#   ngram-mod 6.9/6.9/17.1 | draft-mtp,ngram-mod n2 9.1/10.3/17.4 | n1 10.5/10.9/27.3
# 35B-A3B (same sweep, 2026-09-25): none 33 | mtp n1 42/41/44 | ngram-mod 33/33/67
#   draft-mtp,ngram-mod n1 44/44/70 | n2 42/45/117  -> n=2 for that model
SPEC="${SPEC:-draft-mtp,ngram-mod}"   # per model: catalogue / Auto-tune / config.json, "spec[:n]" accepted
if [[ "$SPEC" == *:* ]]; then SPEC_N="${SPEC_N:-${SPEC##*:}}"; SPEC="${SPEC%%:*}"; fi
SPEC_N="${SPEC_N:-1}"           # --spec-draft-n-max; 27B dense: MTP n>1 loses on Metal
# MTP speculation (draft-mtp) needs an MTP head in the model file (Qwen) or a separate drafter
# (Gemma 4: DRAFT = its mtp-*.gguf, passed with -md; it shares the target's KV cache). carl.py
# already falls back to n-gram for a configured SPEC; this covers a SPEC from the environment.
draft_args=()
if [[ ",$SPEC," == *,draft-mtp,* ]]; then
  if [[ "${MTP_SOURCE:-}" == none ]]; then
    echo "note: ${MODEL_NAME:-$MODEL} has no MTP head and no downloaded MTP drafter, so the speculation is n-gram only." >&2
    SPEC=ngram-mod
  elif [[ -n "$DRAFT" ]]; then
    [[ -f "$DRAFT" ]] || { echo "error: the MTP drafter $DRAFT does not exist. ./carl.sh download ${MODEL_NAME:-NAME} downloads it." >&2; exit 1; }
    draft_args=(-md "$DRAFT")
  fi
fi
# Sampling: Qwen's thinking-mode recommendation (Qwen3.8 and Qwen3.6 model cards):
# temperature 1.0, top_p 0.95, top_k 20, min_p 0, presence 0, repetition 1.0.
# Set explicitly (the GGUFs embed the same values, but a file without them would
# silently get llama.cpp's generic defaults). Clients that send their own values
# win per request: OpenCode's "none" variant sends Qwen's non-thinking set
# (temperature 0.7, top_p 0.8, presence 1.5). 35B-A3B card alternatives:
# PRESENCE=1.5 (general use, fewer repetition loops) or TEMP=0.6 (precise coding).
TEMP="${TEMP:-1.0}"; TOP_P="${TOP_P:-0.95}"; TOP_K="${TOP_K:-20}"; MIN_P="${MIN_P:-0}"; PRESENCE="${PRESENCE:-0}"
REPEAT="${REPEAT:-1.0}"         # repetition penalty: Qwen says 1.0 (off)
BATCH="${BATCH:-2048}"          # -b logical batch
CKPT="${CKPT:-8}"; CKPT_STEP="${CKPT_STEP:-4096}"   # context checkpoints (hybrid models cannot trim their state)
# Server log: a new timestamped file per start + a "latest" symlink.
# LOG_FILE=none disables file logging (terminal output only).
LOG_DIR="${LOG_DIR:-$HOME/models/logs}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/llama-server-$(date +%Y%m%d-%H%M%S).log}"
for v in UB BATCH CKPT CKPT_STEP SPEC_N; do require_int "$v" "${!v}"; done
[[ -z "${CACHE_RAM:-}" ]] || require_int CACHE_RAM "$CACHE_RAM"
[[ -z "${RESERVE_GB:-}" ]] || require_int RESERVE_GB "$RESERVE_GB"

# One server per port, and in practice one model at a time (two don't fit in
# 36 GB). Fail before touching the log symlink or loading anything.
if pid=$(port_pid "$PORT") && [[ -n "$pid" ]]; then
  echo "error: port $PORT is in use by process $pid ($(ps -o command= -p "${pid%%$'\n'*}" | cut -c1-60)). Stop it first: press Ctrl-C in its terminal, or run kill $pid." >&2
  echo "       If it is a CARL server, ./carl.sh monitor opens its dashboard." >&2
  exit 1
fi
guard_other_models               # a second model can crash the Mac (host/common.sh)

ensure_api_key "$API_KEY_FILE"
CARL_CLIENT_DIR="${CARL_CLIENT_DIR:-$HERE/../client}" write_client_package "$HOST" "$PORT" "$API_KEY_FILE"

# The disk cache of prompt states (--slot-save-path): OpenCode and Pi save each agent's prompt and
# each session's conversation here through the server (client/shared/carl-cache.js). Trimmed to
# cache.disk_gb at every start (the dashboard and the clients on this Mac keep it there too).
SLOT_DIR="$CARL_CONF/slots"; mkdir -p "$SLOT_DIR"
python3 "$HERE/../tools/carl.py" cache trim >/dev/null 2>&1 || true

# The thinking toggle's chat template for a model file: TMPL (regenerated when the model
# or the generator is newer).
TMPL_DIR="$HOME/models/templates"
make_template() {
  mkdir -p "$TMPL_DIR"
  TMPL="$TMPL_DIR/$(basename "$1" .gguf).thinking-toggle.jinja"
  if [[ ! -s "$TMPL" || "$1" -nt "$TMPL" || "$HERE/gguf-chat-template.py" -nt "$TMPL" ]]; then
    python3 "$HERE/gguf-chat-template.py" "$1" "$TMPL" >/dev/null || rm -f "$TMPL"
  fi
}

# The installed llama.cpp against the version CARL is tested with (tools/carl_core/domain/llamacpp.py): one start
# line when it is older ("" otherwise; a check that cannot run says nothing).
LLAMA_NOTE="$(python3 "$HERE/../tools/carl.py" llama-version 2>/dev/null)" || LLAMA_NOTE=""

log_args=(); lv_args=(); log_filter=0
if [[ "$LOG_FILE" != "none" ]]; then
  mkdir -p "$(dirname "$LOG_FILE")"
  ln -sfn "$LOG_FILE" "$(dirname "$LOG_FILE")/llama-server-latest.log"
  log_args=(--log-file "$LOG_FILE" --log-timestamps --log-prefix)   # colours kept; tools/llama-log.sh strips them
  # A single model: -lv 4 for the RAM cache's lines (the dashboard's count), its output through CARL's log filter,
  # which keeps the log about its size at the default level (Phase 23.4.4 item 12; host/common.sh run_server).
  lv_args=(-lv 4 --log-timestamps --log-prefix); log_filter=1
fi

# Router mode: the presets for every downloaded model that fits (each with its own
# settings, as a single start of it), the start model loaded at once, at most one
# model in memory (--models-max 1: llama.cpp stops the loaded model before it loads
# the next one). The router's own log carries its models' lines ("[port] ...").
if [[ "$LLAMA_MODE" == router ]]; then
  if [[ "${THINK_TOGGLE:-1}" != 0 ]]; then
    while IFS= read -r name; do
      p="$(python3 "$HERE/../tools/carl.py" path "$name" 2>/dev/null)" && [[ -f "$p" ]] && make_template "$p"
    done < <(python3 "$HERE/../tools/carl.py" downloaded)
  fi
  PRESET="$CARL_CONF/router-presets.ini"
  presets="$(python3 "$HERE/../tools/carl.py" router-preset --out "$PRESET" --templates "$TMPL_DIR")" || exit 1
  grep -q '^model ' <<< "$presets" || { echo "error: router mode cannot start: no downloaded model fits this Mac. ./carl.sh fit shows what fits." >&2; exit 1; }
  first="$(sed -n 's/^start //p' <<< "$presets")"
  echo "CARL starts the server in router mode at http://$HOST:$PORT. $NET_NOTE"
  [[ -z "$LLAMA_NOTE" ]] || echo "$LLAMA_NOTE"
  echo "OpenCode and Pi can switch the model. The server loads one model at a time. A switch takes 30 s to 2 min."
  echo "After a switch, the new model has none of the sessions in memory. A saved session belongs to the model it"
  echo "ran on: OpenCode and Pi restore it from the disk cache when you switch back to that model (with saved sessions"
  echo "on, and for a Gemma model the full cache). A session that continues on the new model is read again."
  echo "The models (their settings: $(tilde "$PRESET")):"
  while IFS= read -r line; do
    case "$line" in
      "model "*) n="${line#model }"; n="${n%% *}"
                 echo "  $n: ${line#model "$n" }$([[ "$n" == "$first" ]] && echo " (loads first)")" ;;
      "skip "*) echo "  ${line#skip } This model is left out." ;;
    esac
  done <<< "$presets"
  run_server "$PORT" "$LOG_FILE" llama-server \
    --host "$HOST" --port "$PORT" --api-key-file "$API_KEY_FILE" \
    --models-preset "$PRESET" --models-max 1 \
    ${log_args[@]+"${log_args[@]}"} \
    "$@"
  exit $?
fi

# Slots and the RAM prompt cache, sized for this Mac (tools/llama-fit.py --plan).
# The prompt cache holds conversations parked out of a slot (a third session, a
# second subagent). Its size is what RAM allows after weights + KV + a reserve
# for macOS and apps (10 GiB with the VMware network up, else 6; RESERVE_GB=N),
# clamped to 1-8 GiB. Parked states measured 2.1-2.3 GiB at 40-60K tokens (35B).
fit_args=()
[[ -n "${RESERVE_GB:-}" ]] && fit_args=(--reserve-gb "$RESERVE_GB")
# The speculation and -ub count (MTP's draft context, the compute buffers); a drafter's weights with MTP.
spec_fit=(--spec "$SPEC" --spec-n "$SPEC_N" --ub "$UB"); SPEC_NOTE=""
[[ ${#draft_args[@]} -gt 0 ]] && spec_fit+=(--draft "$DRAFT")
# A model with sliding-window layers (Gemma): SWA is full (every layer at full length: the prompt
# states OpenCode and Pi save can be restored) or window (less memory, no restores); cache.swa =
# auto picks full when it fits with these slots.
SWA=-
if plan=$(python3 "$HERE/../tools/llama-fit.py" --plan "$MODEL" --ctx "$CTX" --kv "$KV_K" \
            --want-slots "$SLOTS" --swa "${SWA_MODE:-auto}" ${fit_args[@]+"${fit_args[@]}"} "${spec_fit[@]}" \
            2>/dev/null) && [[ "$plan" =~ ^([1-9])\ ([0-9]+)\ (full|window|-)\ ([a-z,-]+)$ ]]; then
  plan_spec="${BASH_REMATCH[4]}"
  if [[ "$plan_spec" != "$SPEC" ]]; then     # Auto fit's order: MTP goes (n-gram stays) before a slot
    SPEC="$plan_spec"; draft_args=(); spec_fit=(--spec "$SPEC" --spec-n "$SPEC_N" --ub "$UB")
    SPEC_NOTE="from Auto fit: MTP does not fit with these slots and this context on this Mac"
  fi
  if [[ "$SLOTS" == auto ]]; then
    SLOTS_NOTE="auto: $([[ "${BASH_REMATCH[1]}" == 1 ]] && echo "two slots do not fit" || echo "two slots fit")"
  fi
  SLOTS="${BASH_REMATCH[1]}"; CACHE_RAM="${CACHE_RAM:-${BASH_REMATCH[2]}}"; SWA="${BASH_REMATCH[3]}"
else
  [[ "$SLOTS" == auto ]] && { SLOTS=1; SLOTS_NOTE="auto: CARL could not calculate the memory"; }
  CACHE_RAM="${CACHE_RAM:-4096}"
fi

# Memory check (tools/llama-fit.py --check): a start whose weights + KV + buffers
# exceed what macOS lets the GPU use fails to load or swaps the Mac to a crawl, so it
# is refused (exit 3: what it needs vs the limit, the largest window that fits, auto
# fit's alternative). Expert override: FIT_CHECK=0 skips the check. A check that
# can't run (an unreadable header) only warns: llama-server reports a bad file itself.
if [[ "${FIT_CHECK:-1}" != 0 ]]; then
  fit_rc=0
  python3 "$HERE/../tools/llama-fit.py" --check "$MODEL" --ctx "$CTX" --slots "$SLOTS" --kv "$KV_K" \
    --swa "$([[ "$SWA" == window ]] && echo window || echo full)" ${fit_args[@]+"${fit_args[@]}"} "${spec_fit[@]}" \
    || fit_rc=$?
  if (( fit_rc == 3 )); then
    exit 1
  elif (( fit_rc != 0 )); then
    echo "warning: CARL could not check the memory (exit $fit_rc). The server starts anyway." >&2
  fi
fi

# Thinking toggle for OpenCode: llama-server gets the model's own chat template
# with one rule prepended -- reasoning_effort "none" => enable_thinking=false --
# because OpenCode's TUI sends reasoning_effort but not chat_template_kwargs.
# THINK_TOGGLE=0 uses the GGUF's template unmodified.
tmpl_args=()
if [[ "${THINK_TOGGLE:-1}" != 0 ]]; then
  make_template "$MODEL"
  [[ -s "$TMPL" ]] && tmpl_args=(--chat-template-file "$TMPL")
fi

# The sliding-window layers at full length (SWA=full, above): without it llama.cpp re-reads a
# restored prompt state on such a model.
swa_args=()
[[ "$SWA" == full ]] && swa_args=(--swa-full)

spec_args=()
if [[ "$SPEC" != "none" ]]; then
  spec_args=(--spec-type "$SPEC" --spec-draft-n-max "$SPEC_N")
fi

slot_args=()
(( SLOTS > 1 )) && slot_args=(--kv-unified --kv-unified-per-slot "$CTX" --no-cache-idle-slots -sps 0.5)
# The start lines: one plain sentence per setting, with its unit and where it comes from.
src_word() {
  case "$1" in
    flag) echo "from your option" ;; env) echo "from the environment" ;; config) echo "from your settings" ;;
    auto-tune) echo "from Auto-tune" ;; catalogue) echo "from the catalogue" ;; header) echo "from the model file" ;;
    auto-fit) echo "from Auto fit: the first setup in its order that fits" ;; *) echo "CARL's default" ;;
  esac
}
# source KEY FLAG_VALUE ENV_NAME: where one setting comes from, in words
source_of() {
  local s=" $CARL_SOURCES "
  if [[ -n "$2" ]]; then src_word flag
  elif [[ "$ENV_SET" == *" $3 "* ]]; then src_word env
  elif [[ "$s" == *" $1:"* ]]; then s="${s#* "$1":}"; src_word "${s%% *}"
  else src_word default; fi
}
tokens_k() { awk -v n="$1" 'BEGIN { if (n % 1024 == 0) printf "%dK", n / 1024; else printf "%.1fK", n / 1024 }'; }
mib_text() { awk -v n="$1" 'BEGIN { if (n >= 1024) printf "%.1f GiB", n / 1024; else printf "%d MiB", n }'; }
kv_text() { case "$1" in q4_0) echo q4 ;; q8_0) echo q8 ;; *) echo "$1" ;; esac; }
spec_text() {
  case "$1" in none) echo "none" ;; ngram-mod) echo "n-gram" ;; draft-mtp) echo "MTP" ;;
    draft-mtp,ngram-mod) echo "MTP + n-gram" ;; *) echo "$1" ;; esac
}
slots_src="$(source_of slots "$SLOTS_FLAG" SLOTS)"
[[ -n "${SLOTS_NOTE:-}" ]] && slots_src="$SLOTS_NOTE"
echo "CARL starts the server: ${MODEL_NAME:-$ALIAS} ($(basename "$MODEL"))."
[[ -z "$LLAMA_NOTE" ]] || echo "$LLAMA_NOTE"
echo "Address: http://$HOST:$PORT. $NET_NOTE"
echo "Slots: $SLOTS ($slots_src). Context: $(tokens_k "$CTX") tokens per slot ($(source_of ctx "$CTX_FLAG" CTX))."
if [[ "$KV_K" == "$KV_V" ]]; then
  echo "Context memory type: $(kv_text "$KV_K") ($(source_of kv "$KV_FLAG" KV)). RAM cache: $(mib_text "$CACHE_RAM")."
else
  echo "Context memory type: K $(kv_text "$KV_K"), V $(kv_text "$KV_V"). Different types read prompts about 5 times more slowly. RAM cache: $(mib_text "$CACHE_RAM")."
fi
if [[ "$SPEC" == none ]]; then
  echo "Speculation: none ($(source_of spec "" SPEC))."
else
  guesses="$SPEC_N guess$([[ "$SPEC_N" == 1 ]] || echo es)"
  drafter=""; [[ ${#draft_args[@]} -gt 0 ]] && drafter=" The MTP drafter is $(basename "$DRAFT")."
  echo "Speculation: $(spec_text "$SPEC"), $guesses (${SPEC_NOTE:-$(source_of spec "" SPEC)}).$drafter"
fi
if [[ "$SWA" == full ]]; then
  echo "Sliding window: full cache. CARL can restore saved sessions and prompts (cache.swa = ${SWA_MODE:-auto})."
elif [[ "$SWA" == window ]]; then
  echo "Sliding window: window cache. It uses less memory, but CARL cannot restore saved sessions and prompts (cache.swa = ${SWA_MODE:-auto})."
fi
log_text=none; [[ "$LOG_FILE" == none ]] || log_text="$(tilde "$LOG_FILE")"
echo "Log file: $log_text. Batch: -ub $UB. Your settings: ./carl.sh config show."
# --fit off: CARL sizes the start itself (llama-fit.py --check above); llama.cpp's own memory
# fitting only probes here (-ngl and -c are set), and the probe logs a false error for Gemma 4's
# MTP drafter ("Gemma4Assistant requires ctx_other"), which made a normal start look broken.
# Starts the server and the live monitor in this terminal (host/common.sh);
# also keeps the Mac awake while it runs: a sleeping Mac freezes requests
# mid-prompt (10:13-10:46 on 2026-09-25, sleep = 1 min on battery).
CARL_LOG_FILTER="$log_filter" run_server "$PORT" "$LOG_FILE" llama-server \
  -m "$MODEL" --alias "$ALIAS" \
  --host "$HOST" --port "$PORT" --api-key-file "$API_KEY_FILE" \
  --jinja --reasoning-format deepseek \
  --temp "$TEMP" --top-p "$TOP_P" --top-k "$TOP_K" --min-p "$MIN_P" \
  --presence-penalty "$PRESENCE" --repeat-penalty "$REPEAT" \
  --chat-template-kwargs '{"preserve_thinking":true}' \
  -ngl 999 -fa on --fit off -ctk "$KV_K" -ctv "$KV_V" -c "$(( SLOTS * CTX ))" \
  -b "$BATCH" -ub "$UB" --parallel "$SLOTS" ${slot_args[@]+"${slot_args[@]}"} --no-mmproj \
  --ctx-checkpoints "$CKPT" --checkpoint-min-step "$CKPT_STEP" --cache-ram "$CACHE_RAM" \
  --metrics --slot-save-path "$SLOT_DIR" \
  ${lv_args[@]+"${lv_args[@]}"} \
  ${tmpl_args[@]+"${tmpl_args[@]}"} \
  ${spec_args[@]+"${spec_args[@]}"} ${draft_args[@]+"${draft_args[@]}"} \
  ${swa_args[@]+"${swa_args[@]}"} \
  "$@"
