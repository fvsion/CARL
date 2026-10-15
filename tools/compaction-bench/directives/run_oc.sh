#!/bin/zsh
# The OpenCode directive test on this Mac: a fresh harness HOME, then the four sessions, one at a time.
#   run_oc.sh HOME OUT
set -u
H=$(cd "$(dirname "$0")" && pwd)
HOME_DIR=$1 OUT=$2 MODELS=${3:-$HOME/models/gguf/bench-a3b}   # only the A3B: the package lists every GGUF there
mkdir -p "$OUT"
if pgrep -qf "llama-server.*--port 9275"; then echo "the user's server runs on 9275: not now"; exit 1; fi
python3 -c "import json,sys; sys.exit(0 if json.load(open('$HOME_DIR/.agent-bench.json')).get('versions') else 1)" 2>/dev/null || python3 "$H/../compbench.py" prepare --home "$HOME_DIR" --models-dir "$MODELS" || { echo "PREPARE FAILED"; exit 1; }
for run in 1 2; do
  for arm in compact48 control96; do
    python3 "$H/oc_directives.py" --home "$HOME_DIR" --out "$OUT" --arm $arm --run $run --models-dir "$MODELS" || echo "SESSION FAILED $arm $run"
  done
done
echo "OC RUN DONE $(date)"
