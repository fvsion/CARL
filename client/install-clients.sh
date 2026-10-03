#!/usr/bin/env bash
# Installs the OpenCode and/or Pi coding agents for the current user, in the
# VMware VM (Linux) or on a Mac. No sudo: everything lands in ~/.local.
#
#   ./install-clients.sh            # both
#   ./install-clients.sh opencode   # just OpenCode
#   ./install-clients.sh pi         # just Pi
#
# Both ship on npm. Pi needs Node >= 22.19, so if the system Node is missing
# or older, Node 22 LTS (Linux or macOS build) is fetched from nodejs.org (SHA-256 verified) into
# ~/.local/lib/nodejs. Run ./install.sh afterwards (or before) for the configs.
set -euo pipefail

WHAT="${1:-both}"
case "$WHAT" in both|opencode|pi) ;; *) echo "usage: $0 [both|opencode|pi]" >&2; exit 2;; esac

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
  [[ -n "$have_node" ]] && echo "Node $have_node is older than $NODE_MIN; installing a private Node 22 LTS"
  case "$(uname -s)" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
    *) echo "error: unsupported OS $(uname -s)" >&2; exit 1 ;;
  esac
  case "$(uname -m)" in
    aarch64|arm64) arch=arm64 ;;
    x86_64|amd64)  arch=x64 ;;
    *) echo "error: unsupported CPU $(uname -m)" >&2; exit 1 ;;
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
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [[ -f "$rc" ]] || continue
  # "# mtplx-vm-client" marked the same line before 1.2.0: no second copy.
  grep -qE '# (carl|mtplx)-vm-client' "$rc" || printf '\n%s\n' "$line" >> "$rc"
done

# --- Report ------------------------------------------------------------------
echo
[[ "$WHAT" == both || "$WHAT" == opencode ]] && echo "opencode $(opencode --version 2>/dev/null || echo '(not on PATH?)')  -> $(command -v opencode || true)"
[[ "$WHAT" == both || "$WHAT" == pi ]] && echo "pi       $(pi --version 2>/dev/null || echo '(not on PATH?)')  -> $(command -v pi || true)"
echo
echo "Open a new shell (or: source ~/.zshrc), then run 'opencode' or 'pi'."
[[ -f "$HOME/.config/llm-deploy/api-key" || -f "$HOME/.config/mtplx/api-key" ]] || echo "Configs not found yet: run ./install.sh (on a Mac: ./install.sh --local) to point them at the server."
