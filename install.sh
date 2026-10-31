#!/usr/bin/env bash
set -euo pipefail
REPO="${TERMINALTEXT_REPO:-https://github.com/williamkbukowski94/terminaltext.git}"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/terminaltext"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
if ! command -v git >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1 || ! command -v kdeconnect-cli >/dev/null 2>&1; then
  PM=""
  for candidate in apt-get dnf pacman zypper; do
    if command -v "$candidate" >/dev/null 2>&1; then PM="$candidate"; break; fi
  done
  if [[ -z "$PM" ]]; then
    printf '%s\n' 'Install git, Python 3, KDE Connect, and xdg-utils with your package manager, then rerun.' >&2
    exit 1
  fi
  if [[ "$EUID" -eq 0 ]]; then SUDO=(); elif command -v sudo >/dev/null 2>&1; then SUDO=(sudo); else
    printf '%s\n' 'sudo is required to install system dependencies.' >&2; exit 1
  fi
  case "$PM" in
    apt-get) "${SUDO[@]}" apt-get update; "${SUDO[@]}" apt-get install -y git python3 kdeconnect xdg-utils ;;
    dnf) "${SUDO[@]}" dnf install -y git python3 kde-connect xdg-utils ;;
    pacman) "${SUDO[@]}" pacman -Sy --needed --noconfirm git python kdeconnect xdg-utils ;;
    zypper) "${SUDO[@]}" zypper --non-interactive install git python3 kdeconnect-kde xdg-utils ;;
  esac
fi
mkdir -p "$(dirname "$DEST")" "$BIN"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone --depth 1 "$REPO" "$TMP/repo"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$TMP/repo/." "$DEST/"
chmod 755 "$DEST/terminaltext.sh" "$DEST/install.sh"
cat > "$BIN/terminaltext" <<EOF
#!/usr/bin/env bash
exec "$DEST/terminaltext.sh" "\$@"
EOF
chmod 755 "$BIN/terminaltext"
printf 'Installed. Run: %s\n' "$BIN/terminaltext"
case ":$PATH:" in *":$BIN:"*) ;; *) printf 'Add this to PATH: export PATH="%s:\$PATH"\n' "$BIN" ;; esac
