#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo "Do not run this script with sudo/root; these are user-level units." >&2
  exit 1
fi

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
systemctl --user disable --now yolo-daily.timer 2>/dev/null || true
rm -f "$UNIT_DIR/yolo-daily.timer" "$UNIT_DIR/yolo-daily.service"
systemctl --user daemon-reload
systemctl --user reset-failed yolo-daily.service 2>/dev/null || true

echo "Removed user-level YOLO systemd units. Runtime data was left untouched."
