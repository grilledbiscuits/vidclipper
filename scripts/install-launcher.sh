#!/usr/bin/env bash
# Installs the VidClipper menu entry (and an optional desktop shortcut).
# Safe to re-run; it only writes files under your own home directory.

set -euo pipefail

APP_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
DESKTOP_FILE="$APPS_DIR/vidclipper.desktop"

mkdir -p "$APPS_DIR" "$ICON_DIR"
cp "$APP_DIR/assets/vidclipper.png" "$ICON_DIR/vidclipper.png"

sed -e "s|@APP_DIR@|$APP_DIR|g" \
    -e "s|@ICON@|$APP_DIR/assets/vidclipper.png|g" \
    "$APP_DIR/assets/vidclipper.desktop.in" > "$DESKTOP_FILE"
chmod +x "$DESKTOP_FILE"

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -f -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" \
    >/dev/null 2>&1 || true

echo "Installed menu entry: $DESKTOP_FILE"

# Optional copy on the desktop, marked trusted so it double-clicks cleanly.
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP_DIR" ]; then
    cp "$DESKTOP_FILE" "$DESKTOP_DIR/vidclipper.desktop"
    chmod +x "$DESKTOP_DIR/vidclipper.desktop"
    gio set "$DESKTOP_DIR/vidclipper.desktop" metadata::trusted true 2>/dev/null || true
    echo "Added desktop shortcut:  $DESKTOP_DIR/vidclipper.desktop"
fi

echo "Look for 'VidClipper' in your application menu (under Sound & Video)."
