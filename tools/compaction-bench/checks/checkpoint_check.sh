#!/bin/zsh
# Ranked changes 9 and 14 (Phase 23.5.2), on this Mac: one llama-server at a time on a test port (never the user's
# 9275), the A3B IQ3 as CARL starts it (--jinja, deepseek reasoning, CARL's chat template, preserve_thinking).
#   checkpoint_check.sh MODEL.gguf TEMPLATE.jinja REQUEST.json OUTDIR
set -u
M=$1 T=$2 R=$3 O=$4
H=$(cd "$(dirname "$0")" && pwd)
P=8199
mkdir -p "$O"
serve() {   # $1 name, then llama-server's extra flags
  local name=$1; shift
  llama-server -m "$M" --host 127.0.0.1 --port $P -c 98304 -np 1 --jinja --reasoning-format deepseek \
    --chat-template-file "$T" --chat-template-kwargs '{"preserve_thinking":true}' "$@" > "$O/server-$name.log" 2>&1 &
  echo $! > "$O/server.pid"
  until curl -sf http://127.0.0.1:$P/health >/dev/null 2>&1; do sleep 2; done
}
stop() { kill "$(cat "$O/server.pid")" 2>/dev/null; sleep 3; }
if pgrep -qf "llama-server.*--port 9275"; then echo "the user's server runs on 9275: not now"; exit 1; fi
serve grammar --ctx-checkpoints 8 --checkpoint-min-step 4096
python3 "$H/grammar_check.py" http://127.0.0.1:$P | tee "$O/grammar.out"
stop
for s in "carl 8 4096" "llamacpp 32 8192" "dense 32 4096"; do
  set -- ${=s}
  serve "$1" --ctx-checkpoints $2 --checkpoint-min-step $3 --cache-ram 0
  python3 "$H/checkpoint_check.py" http://127.0.0.1:$P "$R" "$1" "$O/checkpoints.jsonl"
  grep -i "checkpoint" "$O/server-$1.log" | tail -5
  stop
done
echo "CHECKS DONE $(date)"
