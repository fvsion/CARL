#!/usr/bin/env bash
# Installs the OpenCode and/or Pi coding agents for the current user, in the
# VMware VM (Linux) or on a Mac. No sudo: everything lands in ~/.local.
#
#   ./install-clients.sh            # both
#   ./install-clients.sh opencode   # just OpenCode
#   ./install-clients.sh pi         # just Pi
#   ./install-clients.sh --help     # the help page
#
# Both ship on npm. Pi needs Node >= 22.19, so if the system Node is missing
# or older, Node 22 LTS (Linux or macOS build) is fetched from nodejs.org (SHA-256 verified) into
# ~/.local/lib/nodejs. client/setup runs this script, then ./install.sh for the configs.
set -euo pipefail

# The width of the help page: COLUMNS, else the terminal's, else 80.
help_width() {
  local w="${COLUMNS:-}"
  if ! [[ "$w" =~ ^[0-9]+$ ]]; then
    w=""
    [[ -t 1 ]] && w="$(tput cols 2>/dev/null || true)"
    [[ "$w" =~ ^[0-9]+$ ]] || w=80
  fi
  (( w >= 50 )) || w=50
  printf '%s' "$w"
}

# The help text from stdin, wrapped to the width. A line "TERM\tTEXT" (a backslash and a t) is a term
# with its text in a column; the other lines wrap with their own indent; an empty line stays.
wrap_help() {
  awk -v W="$(help_width)" -v C=24 '
    function emit(text, first, rest,    n, words, i, line, started) {
      n = split(text, words, / +/); line = first; started = 0
      for (i = 1; i <= n; i++) {
        if (words[i] == "") continue
        if (started && length(line) + 1 + length(words[i]) > W) { print line; line = rest words[i] }
        else if (started) line = line " " words[i]
        else { line = line words[i]; started = 1 }
      }
      print line
    }
    $0 == "" { print ""; next }
    (k = index($0, "\\t")) {
      t = substr($0, 1, k - 1); d = substr($0, k + 2); pad = sprintf("%" C "s", "")
      if (length(t) + 2 > C) { print t; emit(d, pad, pad) } else emit(d, sprintf("%-" C "s", t), pad)
      next
    }
    { match($0, /^ */); ind = substr($0, 1, RLENGTH); emit(substr($0, RLENGTH + 1), ind, ind) }'
}

usage() {
  wrap_help <<'HELP'
CARL: install the coding agents OpenCode and Pi for this user.

Run ./setup instead. It installs OpenCode and Pi when they are missing or older (with this script), then writes their configs. This script is an internal part of the setup.

Usage: ./install-clients.sh [both | opencode | pi]

  both\tInstall OpenCode and Pi. This is the default.
  opencode\tInstall only OpenCode.
  pi\tInstall only Pi.
  -h, --help\tShow this help.

The script works on a Mac and in a Linux VM. It needs no sudo: everything goes into ~/.local.

Both agents come from npm. Pi needs Node 22.19 or newer. If Node is missing or older, the script downloads Node 22 LTS from nodejs.org into ~/.local/lib/nodejs. It checks the SHA-256 sum of the download.

The script adds ~/.local/bin to PATH in ~/.zshrc and ~/.bashrc, if these files exist. It makes a backup of each file first.

Then the setup runs ./install.sh. It connects OpenCode and Pi to the CARL server. On the server Mac, ./carl.sh install runs the setup.
HELP
}

WHAT="${1:-both}"
case "$WHAT" in
  both|opencode|pi) ;;
  -h|--help|help) usage; exit 0 ;;
  *) echo "error: unknown argument '$WHAT'. Use both, opencode or pi. Help: $0 --help" >&2; exit 2 ;;
esac

NODE_MIN="22.19.0"
NODE_LINE="latest-v22.x"
PREFIX="$HOME/.local"
BIN="$PREFIX/bin"
mkdir -p "$BIN"
export PATH="$BIN:$PATH"

version_ge() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]; }

# --- Node --------------------------------------------------------------------
have_node=""
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  have_node="$(node -p 'process.versions.node')"
fi

if [[ -n "$have_node" ]] && version_ge "$have_node" "$NODE_MIN"; then
  echo "Node $have_node found ($(command -v node))"
else
  [[ -n "$have_node" ]] && echo "Node $have_node is older than $NODE_MIN. The script installs Node 22 LTS for this user only."
  case "$(uname -s)" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
    *) echo "error: unsupported OS $(uname -s): install Node $NODE_MIN or newer yourself, then run this again" >&2; exit 1 ;;
  esac
  case "$(uname -m)" in
    aarch64|arm64) arch=arm64 ;;
    x86_64|amd64)  arch=x64 ;;
    *) echo "error: unsupported CPU $(uname -m): install Node $NODE_MIN or newer yourself, then run this again" >&2; exit 1 ;;
  esac
  base="https://nodejs.org/dist/$NODE_LINE"
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "$base/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
  tarball="$(grep -oE "node-v[0-9.]+-$os-$arch\.tar\.xz" "$tmp/SHASUMS256.txt" | head -n1)"
  [[ -n "$tarball" ]] || { echo "error: no $os-$arch tarball in $base" >&2; exit 1; }
  echo "Downloading $tarball"
  curl -fL --progress-bar "$base/$tarball" -o "$tmp/$tarball"
  if command -v sha256sum >/dev/null 2>&1; then sumcmd=(sha256sum -c -); else sumcmd=(shasum -a 256 -c -); fi
  (cd "$tmp" && grep " $tarball\$" SHASUMS256.txt | "${sumcmd[@]}")
  dest="$PREFIX/lib/nodejs"
  rm -rf "$dest.new"; mkdir -p "$dest.new"
  tar -xJf "$tmp/$tarball" -C "$dest.new" --strip-components=1
  rm -rf "$dest"; mv "$dest.new" "$dest"
  for b in node npm npx; do ln -sf "$dest/bin/$b" "$BIN/$b"; done
  hash -r
  v="$(node -p 'process.versions.node')" || { echo "error: installed Node does not run on this system" >&2; exit 1; }
  echo "Node $v installed at $dest"
fi

# --- Agents ------------------------------------------------------------------
pkgs=()
[[ "$WHAT" == both || "$WHAT" == opencode ]] && pkgs+=("opencode-ai@latest")
[[ "$WHAT" == both || "$WHAT" == pi ]] && pkgs+=("@earendil-works/pi-coding-agent@latest")
echo "npm install -g --prefix $PREFIX ${pkgs[*]}"
npm install -g --prefix "$PREFIX" "${pkgs[@]}"
hash -r

# --- PATH --------------------------------------------------------------------
# shellcheck disable=SC2016  # written to the rc file literally; the shell expands it there
line='export PATH="$HOME/.local/bin:$PATH"  # carl-vm-client'
# One marked line, appended to a profile that exists (never created), after a backup.
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [[ -f "$rc" ]] || continue
  # "# mtplx-vm-client" marked the same line before 1.2.0: no second copy.
  grep -qE '# (carl|mtplx)-vm-client' "$rc" && continue
  bak="$rc.bak.$(date +%Y%m%d-%H%M%S)"
  cp -p "$rc" "$bak"
  printf '\n%s\n' "$line" >> "$rc"
  echo "Added ~/.local/bin to PATH in ~/${rc##*/} (marked # carl-vm-client; backup: ${bak##*/})"
done

# --- Report ------------------------------------------------------------------
echo
[[ "$WHAT" == both || "$WHAT" == opencode ]] && echo "opencode $(opencode --version 2>/dev/null || echo 'is not on PATH')  -> $(command -v opencode || true)"
[[ "$WHAT" == both || "$WHAT" == pi ]] && echo "pi       $(pi --version 2>/dev/null || echo 'is not on PATH')  -> $(command -v pi || true)"
echo
echo "Open a new terminal, or run: source ~/.zshrc. Then run opencode or pi."
[[ -f "$HOME/.config/carl/api-key" || -f "$HOME/.config/llm-deploy/api-key" || -f "$HOME/.config/mtplx/api-key" ]] || echo "There is no CARL config yet. Run ./setup to connect OpenCode and Pi to the server."
