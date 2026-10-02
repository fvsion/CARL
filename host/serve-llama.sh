#!/usr/bin/env bash
# Serve a registry GGUF (default qwen3.6-35b-a3b; the IQ3 build on 24 GB) with
# llama.cpp (llama-server) to this Mac and the VMware Fusion guest, with a real
# quantized KV cache (MTPLX 2.11.3 cannot hold long sessions on this Mac -- see
# REFERENCE.md section 3).
#
#   ./host/serve-llama.sh [--model NAME|PATH] [--kv q4|q8 | --q4 | --q8] [--ctx N|Nk] [--local|--vm] [extra llama-server flags...]
#
#   --model NAME       a model from host/models.conf (see ./host/serve.sh models;
#                      fetch with ./host/serve.sh download NAME). Default: first entry.
#   --model PATH.gguf  any local GGUF
#
#   --kv q4 (default)  q4_0 K+V: +16% cold prefill, ~2 GB less RAM, same decode,
#                      8/8 needle recall at 66K (tools/llama-ab.sh, 2026-09-24)
#   --kv q8            q8_0 K+V: near-lossless; pick it for max long-range fidelity
#   --ctx N | Nk       context window per slot in tokens (e.g. 131072 or 128k;
#                      k = 1024). Default 96k. Clients take it from /props when
#                      install.sh runs; 128k-160k work but read and decode slower.
#
# Network (host/common.sh): --vm = VMware's 192.168.42.1, --local = 127.0.0.1,
# default auto (VM address if Fusion's network is up, else local). Never 0.0.0.0.
# The API key file (~/.mtplx/api-key) is created on first use if missing.
# Interactive starts show the live monitor in this terminal; quitting it asks
# whether to stop the server or leave it running.
# Override via env, e.g.  CTX=163840 SPEC=ngram-mod ./host/serve-llama.sh
set -euo pipefail

# Script flags (consumed here); everything else passes through to llama-server.
KV_FLAG=""
CTX_FLAG=""
SLOTS_FLAG=""
MODEL_FLAG=""
NET_FLAG=""
pass=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help|--help-adv) exec "$(dirname "$0")/serve.sh" llama "$1" ;;
    --local) NET_FLAG=local ;;
    --vm) NET_FLAG=vm ;;
    --host|--host=*)
      v="${1#--host}"; v="${v#=}"
      if [[ -z "$v" ]]; then shift; v="${1:-}"; fi
      [[ -n "$v" ]] || { echo "error: --host needs an address" >&2; exit 2; }
      HOST="$v" ;;
    --q4) KV_FLAG=q4_0 ;;
    --q8) KV_FLAG=q8_0 ;;
    --kv|--kv=*)
      v="${1#--kv}"; v="${v#=}"
      if [[ -z "$v" ]]; then shift; v="${1:-}"; fi
      case "$v" in
        q4|q4_0) KV_FLAG=q4_0 ;;
        q8|q8_0) KV_FLAG=q8_0 ;;
        f16|bf16) KV_FLAG="$v" ;;
        *) echo "error: --kv takes q4 or q8 (or f16), got '$v'" >&2; exit 2 ;;
      esac ;;
    --model|--model=*)
      v="${1#--model}"; v="${v#=}"
      if [[ -z "$v" ]]; then shift; v="${1:-}"; fi
      [[ -n "$v" ]] || { echo "error: --model needs a registry name or a .gguf path" >&2; exit 2; }
      MODEL_FLAG="$v" ;;
    --slots|--slots=*)
      v="${1#--slots}"; v="${v#=}"
      if [[ -z "$v" ]]; then shift; v="${1:-}"; fi
      [[ "$v" =~ ^([1-4]|auto)$ ]] || { echo "error: --slots takes 1-4 or auto, got '$v'" >&2; exit 2; }
      SLOTS_FLAG="$v" ;;
    --ctx|--ctx=*)
      v="${1#--ctx}"; v="${v#=}"
      if [[ -z "$v" ]]; then shift; v="${1:-}"; fi
      v="$(printf '%s' "$v" | tr '[:upper:]' '[:lower:]')"
      if [[ "$v" =~ ^([0-9]+)k$ ]]; then CTX_FLAG=$(( ${BASH_REMATCH[1]} * 1024 ))
      elif [[ "$v" =~ ^[0-9]+$ ]]; then CTX_FLAG="$v"
      else echo "error: --ctx takes tokens (e.g. 131072) or Nk (e.g. 128k), got '$v'" >&2; exit 2; fi
      if (( CTX_FLAG < 4096 || CTX_FLAG > 262144 )); then
        echo "error: --ctx must be between 4k and 256k (model max 262144), got $CTX_FLAG" >&2; exit 2
      fi ;;
    *) pass+=("$1") ;;
  esac
  shift
done
set -- ${pass[@]+"${pass[@]}"}

# Saved settings, written by the monitor's Settings tab (tab 5). Order of
# precedence: flags > environment > this file > built-in defaults.
# SETTINGS_FILE=none ignores the file; delete it to go back to the defaults.
SETTINGS_FILE="${SETTINGS_FILE:-$HOME/.config/llm-deploy/llama.env}"
SETTINGS_USED=()
if [[ "$SETTINGS_FILE" != none && -f "$SETTINGS_FILE" ]]; then
  while IFS='=' read -r k v || [[ -n "$k" ]]; do
    [[ "$k" =~ ^(MODEL_NAME|KV|KV_K|KV_V|CTX|SLOTS|NET|HOST|TEMP|TOP_P|TOP_K|MIN_P|PRESENCE|REPEAT|SPEC|SPEC_N|CACHE_RAM|UB|BATCH|CKPT|CKPT_STEP)$ ]] || continue
    [[ "$v" =~ ^[A-Za-z0-9_.,:/-]+$ && -z "${!k:-}" ]] || continue
    printf -v "$k" '%s' "$v"; SETTINGS_USED+=("$k=$v")
  done < "$SETTINGS_FILE"
fi

# Model: --model flag > MODEL env (path) > saved MODEL_NAME > registry default (first line of models.conf).
MODELS="$(dirname "$0")/models.sh"
REG_NAME=""
if [[ -n "$MODEL_FLAG" && ( "$MODEL_FLAG" == */* || "$MODEL_FLAG" == *.gguf ) ]]; then
  MODEL="$MODEL_FLAG"
elif [[ -n "$MODEL_FLAG" ]]; then
  REG_NAME="$MODEL_FLAG"
elif [[ -z "${MODEL:-}" && -n "${MODEL_NAME:-}" ]]; then
  REG_NAME="$MODEL_NAME"
elif [[ -z "${MODEL:-}" ]]; then
  # Default model for this Mac: the registry's first entry, or its "default-small"
  # entry when the first can't fit one window in GPU memory (tools/llama-fit.py).
  REG_NAME="$(python3 "$(dirname "$0")/../tools/llama-fit.py" --pick-default --ctx "${CTX_FLAG:-${CTX:-98304}}" 2>/dev/null)"
  [[ -n "$REG_NAME" ]] || REG_NAME="$("$MODELS" default)"
fi
if [[ -n "$REG_NAME" ]]; then
  MODEL="$("$MODELS" path "$REG_NAME")" || exit 1
  REG_ALIAS="$("$MODELS" get "$REG_NAME" alias)"
  REG_SPEC="$("$MODELS" get "$REG_NAME" spec)"
fi
[[ -f "$MODEL" ]] || { echo "error: model file not found: $MODEL" >&2; exit 1; }
ALIAS="${ALIAS:-${REG_ALIAS:-$(basename "$MODEL" .gguf)}}"
source "$(dirname "$0")/common.sh"
resolve_host "$NET_FLAG"
PORT="${PORT:-8080}"
API_KEY_FILE="${API_KEY_FILE:-$HOME/.mtplx/api-key}"
CTX="${CTX_FLAG:-${CTX:-98304}}"   # per slot: --ctx flag > CTX env > 96K. Measured on the 35B (q4_0 KV, 2026-10-01):
                                   # 64K reads a cold prompt at 229 tok/s, decodes 20 tok/s; 128K: 117 / 13.9.
                                   # Up to 128K-160K works (8/8 recall); REFERENCE.md lists the costs.
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
SPEC="${SPEC:-${REG_SPEC:-draft-mtp,ngram-mod}}"   # registry default per model, "spec[:n]"
if [[ "$SPEC" == *:* ]]; then SPEC_N="${SPEC_N:-${SPEC##*:}}"; SPEC="${SPEC%%:*}"; fi
SPEC_N="${SPEC_N:-1}"           # --spec-draft-n-max; 27B dense: MTP n>1 loses on Metal
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

# One server per port, and in practice one model at a time (two don't fit in
# 36 GB). Fail before touching the log symlink or loading anything.
if pid=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null) && [[ -n "$pid" ]]; then
  echo "error: port $PORT is already in use by: $(ps -o command= -p ${pid%%$'\n'*} | cut -c1-100)" >&2
  echo "       Stop it first (Ctrl-C in its terminal, or kill $pid). Watch it: ./carl.sh monitor" >&2
  exit 1
fi
guard_other_models               # a second model can crash the Mac (host/common.sh)

ensure_api_key "$API_KEY_FILE"

# Slots and the RAM prompt cache, sized for this Mac (tools/llama-fit.py --plan).
# The prompt cache holds conversations parked out of a slot (a third session, a
# second subagent). Its size is what RAM allows after weights + KV + a reserve
# for macOS and apps (10 GiB with the VMware network up, else 6; RESERVE_GB=N),
# clamped to 1-8 GiB. Parked states measured 2.1-2.3 GiB at 40-60K tokens (35B).
if plan=$(python3 "$(dirname "$0")/../tools/llama-fit.py" --plan "$MODEL" --ctx "$CTX" --kv "$KV_K" \
            --want-slots "$SLOTS" ${RESERVE_GB:+--reserve-gb "$RESERVE_GB"} 2>/dev/null); then
  [[ "$SLOTS" == auto ]] && SLOTS_NOTE="auto" || SLOTS_NOTE="set"
  SLOTS="${plan%% *}"; CACHE_RAM="${CACHE_RAM:-${plan##* }}"
else
  [[ "$SLOTS" == auto ]] && SLOTS=1; SLOTS_NOTE="fallback"; CACHE_RAM="${CACHE_RAM:-4096}"
fi

# Memory check (tools/llama-fit.py): warn, don't block, if weights + KV +
# buffers exceed what macOS lets the GPU use. FIT_CHECK=0 skips it.
if [[ "${FIT_CHECK:-1}" != 0 ]]; then
  python3 "$(dirname "$0")/../tools/llama-fit.py" --check "$MODEL" --ctx "$CTX" --slots "$SLOTS" --kv "$KV_K" || true
fi

# Thinking toggle for OpenCode: llama-server gets the model's own chat template
# with one rule prepended -- reasoning_effort "none" => enable_thinking=false --
# because OpenCode's TUI sends reasoning_effort but not chat_template_kwargs.
# THINK_TOGGLE=0 uses the GGUF's template unmodified.
tmpl_args=()
if [[ "${THINK_TOGGLE:-1}" != 0 ]]; then
  TMPL_DIR="$HOME/models/templates"; mkdir -p "$TMPL_DIR"
  TMPL="$TMPL_DIR/$(basename "$MODEL" .gguf).thinking-toggle.jinja"
  if [[ ! -s "$TMPL" || "$MODEL" -nt "$TMPL" || "$(dirname "$0")/gguf-chat-template.py" -nt "$TMPL" ]]; then
    python3 "$(dirname "$0")/gguf-chat-template.py" "$MODEL" "$TMPL" || rm -f "$TMPL"
  fi
  [[ -s "$TMPL" ]] && tmpl_args=(--chat-template-file "$TMPL")
fi

log_args=()
if [[ "$LOG_FILE" != "none" ]]; then
  mkdir -p "$(dirname "$LOG_FILE")"
  ln -sfn "$LOG_FILE" "$(dirname "$LOG_FILE")/llama-server-latest.log"
  log_args=(--log-file "$LOG_FILE" --log-timestamps --log-prefix)   # colours kept; tools/llama-log.sh strips them
fi

spec_args=()
if [[ "$SPEC" != "none" ]]; then
  spec_args=(--spec-type "$SPEC" --spec-draft-n-max "$SPEC_N")
fi

echo "network: $NET_NOTE"
slot_args=()
(( SLOTS > 1 )) && slot_args=(--kv-unified --kv-unified-per-slot "$CTX" --no-cache-idle-slots -sps 0.5)
echo "slots: $SLOTS ($SLOTS_NOTE) x ${CTX} tokens, KV $KV_K/$KV_V, RAM prompt cache ${CACHE_RAM} MiB"
(( ${#SETTINGS_USED[@]} )) && echo "saved settings: ${SETTINGS_USED[*]} (${SETTINGS_FILE/#$HOME/~})"
echo "model=$(basename "$MODEL") alias=$ALIAS ctx=$CTX slots=$SLOTS kv=$KV_K/$KV_V ub=$UB spec=$SPEC n=$SPEC_N log=$LOG_FILE"
# Starts the server and the live monitor in this terminal (host/common.sh);
# also keeps the Mac awake while it runs: a sleeping Mac freezes requests
# mid-prompt (10:13-10:46 on 2026-09-25, sleep = 1 min on battery).
run_server "$PORT" "$LOG_FILE" llama-server \
  -m "$MODEL" --alias "$ALIAS" \
  --host "$HOST" --port "$PORT" --api-key-file "$API_KEY_FILE" \
  --jinja --reasoning-format deepseek \
  --temp "$TEMP" --top-p "$TOP_P" --top-k "$TOP_K" --min-p "$MIN_P" \
  --presence-penalty "$PRESENCE" --repeat-penalty "$REPEAT" \
  --chat-template-kwargs '{"preserve_thinking":true}' \
  -ngl 999 -fa on -ctk "$KV_K" -ctv "$KV_V" -c "$(( SLOTS * CTX ))" \
  -b "$BATCH" -ub "$UB" --parallel "$SLOTS" ${slot_args[@]+"${slot_args[@]}"} --no-mmproj \
  --ctx-checkpoints "$CKPT" --checkpoint-min-step "$CKPT_STEP" --cache-ram "$CACHE_RAM" \
  --metrics \
  ${log_args[@]+"${log_args[@]}"} \
  ${tmpl_args[@]+"${tmpl_args[@]}"} \
  ${spec_args[@]+"${spec_args[@]}"} \
  "$@"
