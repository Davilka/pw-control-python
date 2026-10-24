#!/usr/bin/env bash
#
# Remove a per-user installation created by scripts/install-user.sh.
#
# By default the YAML configuration is preserved. Use --purge to remove it too.
#
set -euo pipefail

if [ -z "${HOME:-}" ]; then
    echo "error: HOME is not set" >&2
    exit 1
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
BIN_HOME="$HOME/.local/bin"
PREFIX="$DATA_HOME/pw-midi-volume"
VENV="$PREFIX/venv"
CONFIG_DIR="$CONFIG_HOME/pw-midi-volume"
SERVICE_FILE="$CONFIG_HOME/systemd/user/pw-midi-volume.service"
DESKTOP_FILE="$DATA_HOME/applications/pw-midi-volume-gui.desktop"
ICON_FILE="$DATA_HOME/icons/hicolor/scalable/apps/pw-midi-volume.svg"
AUTOSTART_FILE="$CONFIG_HOME/autostart/pw-midi-volume-gui.desktop"
AUTOSTART_FILE_HOME="$HOME/.config/autostart/pw-midi-volume-gui.desktop"

PURGE=0
ASSUME_YES=0

usage() {
    cat <<EOF
Usage: $0 [--purge] [--yes]

Remove pw-midi-volume for the current user.

  --purge   also delete $CONFIG_DIR
  --yes     do not ask for confirmation
  -h, --help
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --purge)
            PURGE=1
            ;;
        --yes|-y)
            ASSUME_YES=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if [ "$ASSUME_YES" -eq 0 ]; then
    if [ ! -t 0 ]; then
        echo "error: no interactive terminal; use --yes to confirm uninstallation" >&2
        exit 1
    fi
    echo "Будут удалены:"
    echo "  - systemd user service pw-midi-volume"
    echo "  - приложение и виртуальное окружение: $PREFIX"
    echo "  - ярлыки в $BIN_HOME"
    echo "  - desktop entry, иконка и autostart entry"
    if [ "$PURGE" -eq 1 ]; then
        echo "  - конфигурация: $CONFIG_DIR"
    else
        echo "Конфигурация сохранится: $CONFIG_DIR"
        echo "Для её удаления используйте: $0 --purge"
    fi
    printf '\nПродолжить? [y/N] '
    read -r answer
    case "$answer" in
        y|Y|yes|YES)
            ;;
        *)
            echo "Отменено."
            exit 0
            ;;
    esac
fi

echo "==> Останавливаю и отключаю сервис"
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now pw-midi-volume.service >/dev/null 2>&1 || true
    systemctl --user reset-failed pw-midi-volume.service >/dev/null 2>&1 || true
fi

# Stop a GUI or a manually started daemon that is still using this venv.
# The pattern is tied to our private venv path, so unrelated processes are
# not affected.
if command -v pgrep >/dev/null 2>&1; then
    pids="$(pgrep -f -- "$VENV/bin/pw-midi-volume" 2>/dev/null || true)"
    if [ -n "$pids" ]; then
        echo "==> Останавливаю запущенные процессы pw-midi-volume"
        # shellcheck disable=SC2086
        kill -TERM $pids 2>/dev/null || true
        for _ in $(seq 1 20); do
            sleep 0.1
            pids="$(pgrep -f -- "$VENV/bin/pw-midi-volume" 2>/dev/null || true)"
            [ -z "$pids" ] && break
        done
        if [ -n "$pids" ]; then
            echo "warning: processes did not exit after SIGTERM: $pids" >&2
        fi
    fi
fi

echo "==> Удаляю systemd user unit"
rm -f "$SERVICE_FILE"
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

echo "==> Удаляю ярлыки"
for launcher in \
    "$BIN_HOME/pw-midi-volume" \
    "$BIN_HOME/pw-midi-volume-gui"
do
    if [ -L "$launcher" ]; then
        target="$(readlink -f "$launcher" 2>/dev/null || true)"
        case "$target" in
            "$VENV"/*)
                rm -f "$launcher"
                ;;
            *)
                echo "warning: оставляю $launcher (ссылается на $target)" >&2
                ;;
        esac
    elif [ -e "$launcher" ]; then
        echo "warning: оставляю $launcher (это не символическая ссылка)" >&2
    fi
done

echo "==> Удаляю desktop entry, иконку и autostart"
rm -f "$DESKTOP_FILE" "$ICON_FILE" "$AUTOSTART_FILE" "$AUTOSTART_FILE_HOME"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DATA_HOME/applications" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "==> Удаляю приложение"
rm -rf "$PREFIX"

if [ "$PURGE" -eq 1 ]; then
    echo "==> Удаляю конфигурацию"
    rm -rf "$CONFIG_DIR"
    echo "Конфигурация удалена: $CONFIG_DIR"
else
    echo "Конфигурация сохранена: $CONFIG_DIR"
    echo "Удалить её можно командой: rm -rf \"$CONFIG_DIR\""
fi

cat <<EOF

Удаление завершено.

Проверить, что сервис удалён:
    systemctl --user status pw-midi-volume.service

Если GUI запускался автоматически, запись autostart также удалена.
Исходный код проекта остался в:
    $ROOT

EOF
