#!/usr/bin/env bash
#
# Install pw-midi-volume for the current user without root privileges.
# The Python package is installed into a private virtual environment under
# ~/.local/share/pw-midi-volume, launchers are symlinked into ~/.local/bin,
# and a systemd user unit plus a desktop entry are installed.
#
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
BIN_HOME="$HOME/.local/bin"
PREFIX="$DATA_HOME/pw-midi-volume"
VENV="$PREFIX/venv"

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 is not installed" >&2
    exit 1
fi
if ! command -v wpctl >/dev/null 2>&1 || ! command -v pw-dump >/dev/null 2>&1; then
    echo "warning: wpctl/pw-dump were not found. Install PipeWire and WirePlumber." >&2
fi
if ! command -v gdbus >/dev/null 2>&1; then
    echo "warning: gdbus was not found. Multimedia MPRIS controls will not work." >&2
fi

echo "==> Creating virtual environment in $VENV"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
echo "==> Installing pw-midi-volume"
"$VENV/bin/python" -m pip install --upgrade "${ROOT}[gui]"

echo "==> Installing launchers"
mkdir -p "$BIN_HOME"
ln -sfn "$VENV/bin/pw-midi-volume" "$BIN_HOME/pw-midi-volume"
ln -sfn "$VENV/bin/pw-midi-volume-gui" "$BIN_HOME/pw-midi-volume-gui"

echo "==> Installing icon and desktop entry"
install -Dm644 \
    "$ROOT/src/pw_midi_volume/assets/pw-midi-volume.svg" \
    "$DATA_HOME/icons/hicolor/scalable/apps/pw-midi-volume.svg"
mkdir -p "$DATA_HOME/applications"
sed "s|^Exec=.*|Exec=$BIN_HOME/pw-midi-volume-gui|" \
    "$ROOT/contrib/pw-midi-volume-gui.desktop" \
    > "$DATA_HOME/applications/pw-midi-volume-gui.desktop"
chmod 644 "$DATA_HOME/applications/pw-midi-volume-gui.desktop"

echo "==> Installing systemd user unit"
mkdir -p "$CONFIG_HOME/systemd/user"
sed "s|^ExecStart=.*|ExecStart=$BIN_HOME/pw-midi-volume daemon|" \
    "$ROOT/contrib/pw-midi-volume.service" \
    > "$CONFIG_HOME/systemd/user/pw-midi-volume.service"
chmod 644 "$CONFIG_HOME/systemd/user/pw-midi-volume.service"

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DATA_HOME/applications" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
fi

cat <<EOF

Installation complete.

Start the background service:
    systemctl --user enable --now pw-midi-volume.service

Open the configuration GUI:
    pw-midi-volume-gui

Optional: start the GUI automatically in the tray at login:
    mkdir -p "\$HOME/.config/autostart"
    sed "s|^Exec=.*|Exec=$BIN_HOME/pw-midi-volume-gui --minimized|" \\
        "$ROOT/contrib/pw-midi-volume-gui-autostart.desktop" \\
        > "\$HOME/.config/autostart/pw-midi-volume-gui.desktop"

Configuration file:
    $CONFIG_HOME/pw-midi-volume/config.yaml

Service logs:
    journalctl --user -u pw-midi-volume -f

Uninstall (keeps the YAML configuration):
    "$ROOT/scripts/uninstall-user.sh"
Uninstall and remove configuration:
    "$ROOT/scripts/uninstall-user.sh" --purge

EOF
