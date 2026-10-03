#!/usr/bin/env bash
# Build a clean zip of this folder to share (e.g. with a friend):
#   tools/make-share-zip.sh [--with-docs] [OUT.zip]   default: ../CARL-YYYYMMDD.zip
# The zip holds one folder, CARL/, whatever this folder is called.
# Leaves out the development notes (--with-docs keeps them), caches,
# Finder files and any api-key file. Models, logs and keys live outside the
# repo (~/models, ~/.mtplx), so nothing personal is included.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
name=CARL
repo="$PWD"
docs=0
[[ "${1:-}" == --with-docs ]] && { docs=1; shift; }
out="${1:-../$name-$(date +%Y%m%d).zip}"
case "$out" in /*) ;; *) out="$PWD/$out" ;; esac          # absolute, before we cd
excl=("$name/.git/*" "*/__pycache__/*" "*.pyc" "*/.mypy_cache/*" "*/.pytest_cache/*" "*.DS_Store" "*/api-key" "*.bak.*" "$name/carl_logo.JPG")
excl+=("$name/task_manager.py" "$name/tasks.json")      # local files from a client session, not part of the project
[[ $docs == 1 ]] || excl+=("$name/docs/*")
rm -f "$out"
stage="$(mktemp -d)"; trap 'rm -rf "$stage"' EXIT
ln -s "$repo" "$stage/$name"                              # zip follows the link: the files go in as CARL/...
( cd "$stage" && zip -qr -X "$out" "$name" -x "${excl[@]}" )
echo "$out  ($(du -h "$out" | cut -f1), $(zipinfo -1 "$out" | grep -vc '/$') files$([[ $docs == 1 ]] || echo ', without development notes'))"
