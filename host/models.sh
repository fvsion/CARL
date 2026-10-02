#!/usr/bin/env bash
# GGUF model registry + downloader for the llama.cpp preset.
# Registry: host/models.conf. Files live in $MODELS_DIR (default ~/models/gguf).
#
#   host/models.sh list                 # registry + local status
#   host/models.sh download NAME...     # download (aria2c 16x, resumable) + sha256 verify
#   host/models.sh download all
#   host/models.sh download default     # the default model for this Mac (see: serve.sh fit)
#   host/models.sh verify NAME...       # re-check sha256 of a local file
#   host/models.sh path NAME            # print local path (exit 1 if not downloaded)
#   host/models.sh get NAME FIELD       # FIELD: repo rev file sha256 bytes alias spec notes
#
# Also reachable as: ./host/serve.sh models | download NAME...
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CONF="${MODELS_CONF:-$HERE/models.conf}"
MODELS_DIR="${MODELS_DIR:-$HOME/models/gguf}"

names() { grep -v '^\s*#' "$CONF" | grep -v '^\s*$' | cut -d'|' -f1; }
default_name() { names | head -n1; }
line() { grep -v '^\s*#' "$CONF" | awk -F'|' -v n="$1" '$1==n' | head -n1; }
field() {  # field NAME FIELD
  local l; l="$(line "$1")"; [[ -n "$l" ]] || { echo "error: unknown model '$1' (see: host/models.sh list)" >&2; return 1; }
  local i; case "$2" in
    name) i=1;; repo) i=2;; rev) i=3;; file) i=4;; sha256) i=5;; bytes) i=6;; alias) i=7;; spec) i=8;; notes) i=9;;
    *) echo "error: unknown field '$2'" >&2; return 1;; esac
  printf '%s\n' "$l" | cut -d'|' -f"$i"
}
local_path() { printf '%s/%s\n' "$MODELS_DIR" "$(field "$1" file)"; }
human() { awk -v b="$1" 'BEGIN{printf "%.1f GB", b/1e9}'; }

status() {  # downloaded (size matches) | partial | missing
  local p size want; p="$(local_path "$1")"; want="$(field "$1" bytes)"
  if [[ -f "$p" && ! -e "$p.aria2" ]]; then
    size=$(stat -f %z "$p"); [[ "$size" == "$want" ]] && { echo downloaded; return; }
  fi
  [[ -e "$p" || -e "$p.aria2" ]] && echo partial || echo missing
}

cmd_list() {
  local def; def="$(default_name)"
  printf '%-26s %-9s %-11s %s\n' NAME SIZE STATUS NOTES
  for n in $(names); do
    local mark=""; [[ "$n" == "$def" ]] && mark=" [default]"
    printf '%-26s %-9s %-11s %s%s\n' "$n" "$(human "$(field "$n" bytes)")" "$(status "$n")" "$(field "$n" notes)" "$mark"
    printf '%-26s %s\n' "" "hf: $(field "$n" repo) / $(field "$n" file)"
  done
  echo; echo "dir: $MODELS_DIR   free: $(df -h "$MODELS_DIR" 2>/dev/null | awk 'NR==2{print $4}')"
}

verify() {  # verify NAME -> 0 ok
  local p want got; p="$(local_path "$1")"; want="$(field "$1" sha256)"
  [[ -f "$p" ]] || { echo "  $1: not downloaded" >&2; return 1; }
  echo "  $1: verifying sha256 ($(human "$(stat -f %z "$p")"))..."
  got="$(shasum -a 256 "$p" | cut -d' ' -f1)"
  if [[ "$got" == "$want" ]]; then echo "  $1: OK $got"; else echo "  $1: MISMATCH got $got want $want" >&2; return 1; fi
}

download() {  # download NAME
  local n="$1" p url bytes free_kb
  p="$(local_path "$n")"; bytes="$(field "$n" bytes)"
  url="https://huggingface.co/$(field "$n" repo)/resolve/$(field "$n" rev)/$(field "$n" file)"
  mkdir -p "$MODELS_DIR"
  if [[ "$(status "$n")" == downloaded ]]; then
    echo "== $n already downloaded: $p"; verify "$n"; return
  fi
  free_kb=$(df -k "$MODELS_DIR" | awk 'NR==2{print $4}')
  if (( free_kb * 1024 < bytes + 5 * 1024**3 )); then
    echo "error: $n needs $(human "$bytes") + 5 GB headroom; only $(( free_kb / 1024 / 1024 )) GB free" >&2; return 1
  fi
  echo "== $n: $(human "$bytes") -> $p"
  if command -v aria2c >/dev/null; then
    aria2c -x16 -s16 -k1M --continue=true --file-allocation=none --summary-interval=30 \
      --console-log-level=warn -d "$MODELS_DIR" -o "$(field "$n" file)" "$url"
  else
    curl -fL -C - --progress-bar -o "$p" "$url"
  fi
  [[ "$(stat -f %z "$p")" == "$bytes" ]] || { echo "error: size mismatch for $p" >&2; return 1; }
  if ! verify "$n"; then mv "$p" "$p.bad"; echo "error: bad checksum; moved to $p.bad" >&2; return 1; fi
}

case "${1:-list}" in
  list|ls) cmd_list ;;
  download|dl)
    shift; [[ $# -gt 0 ]] || { echo "usage: $0 download NAME...|default|all" >&2; exit 2; }
    [[ "$1" == all ]] && set -- $(names)
    if [[ "$1" == default ]]; then   # this Mac's default (tools/llama-fit.py --pick-default)
      d="$(python3 "$HERE/../tools/llama-fit.py" --pick-default 2>/dev/null)"; d="${d:-$(default_name)}"
      echo "== default model for this Mac: $d"; set -- "$d"
    fi
    for n in "$@"; do field "$n" name >/dev/null && download "$n"; done ;;
  verify) shift; for n in "$@"; do verify "$n"; done ;;
  path) field "${2:?NAME}" name >/dev/null || exit 1; p="$(local_path "$2")"; [[ "$(status "$2")" == downloaded ]] || { echo "error: $2 not downloaded (run: ./carl.sh download $2)" >&2; exit 1; }; echo "$p" ;;
  get) field "${2:?NAME}" "${3:?FIELD}" ;;
  default) default_name ;;
  downloaded) for n in $(names); do [[ "$(status "$n")" == downloaded ]] && echo "$n"; done; true ;;
  -h|--help|help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) echo "usage: $0 list | download NAME...|all | verify NAME... | path NAME | get NAME FIELD" >&2; exit 2 ;;
esac
