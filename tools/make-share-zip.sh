#!/usr/bin/env bash
# Build a clean zip of this project to share (e.g. with a friend):
#   tools/make-share-zip.sh [OUT.zip]   default: ../CARL-YYYYMMDD.zip
# The zip holds one folder, CARL/, whatever this folder is called. Its files come from a list,
# not from the folder: the files that git tracks (git ls-files), so nothing untracked goes in (a
# test folder, a local note, a cache, a log). Never an api-key, remote.json or
# installed-models.json (the server's files in client/; git ignores them too), nor a backup or a
# cache. docs/ (the development notes) is git-ignored, so it is never in the zip. Models, logs and keys
# live outside the repo (~/models, ~/.config/carl).
# The client package for another computer is ./carl.sh package (it holds the key: this zip never).
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
name=CARL
repo="$PWD"
case "${1:-}" in -*) echo "error: make-share-zip.sh takes no options, only the zip's path (docs/ is never zipped)." >&2; exit 2 ;; esac
out="${1:-../$name-$(date +%Y%m%d).zip}"
case "$out" in /*) ;; *) out="$PWD/$out" ;; esac          # absolute, before we cd
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "error: make-share-zip.sh needs the git repository: it takes the files that git tracks." >&2; exit 1; }
stage="$(mktemp -d)"; trap 'rm -rf "$stage"' EXIT
# The list: each tracked file that is still on the disk, without the server's files, backups and caches.
skip='(^|/)(api-key|remote\.json|installed-models\.json|\.DS_Store)$|(^|/)(__pycache__|\.mypy_cache|\.pytest_cache)/|\.pyc$|\.bak\.|\.before-carl$'
git -c core.quotepath=off ls-files | grep -Ev "$skip" | while IFS= read -r f; do
  if [[ -f "$f" ]]; then printf '%s/%s\n' "$name" "$f"; fi
done > "$stage/list"
rm -f "$out"
ln -s "$repo" "$stage/$name"                              # zip follows the link: the files go in as CARL/...
( cd "$stage" && zip -q -X "$out" -@ < list )
echo "$out  ($(du -h "$out" | cut -f1), $(zipinfo -1 "$out" | grep -vc '/$') files)"
