#!/bin/zsh
# The hook test (Phase 23.5.2.2.4) on this Mac: the rules48 arm, two runs, in the directive test's harness HOME.
#   run_hook.sh HOME OUT [MODELS]
set -u
H=$(cd "$(dirname "$0")" && pwd)
HOME_DIR=$1 OUT=$2 MODELS=${3:-$HOME/models/gguf/bench-a3b}
if pgrep -qf "llama-server.*--port 9275"; then echo "the user's server runs on 9275: not now"; exit 1; fi
for run in 1 2; do
  python3 "$H/oc_directives.py" --home "$HOME_DIR" --out "$OUT" --arm rules48 --run $run --models-dir "$MODELS" || echo "SESSION FAILED rules48 $run"
done
echo "HOOK RUN DONE $(date)"
