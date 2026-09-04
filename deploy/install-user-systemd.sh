#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  echo "Do not run this installer with sudo/root. It installs a user-level systemd timer." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${1:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PYTHON_BIN="${2:-$(command -v python3 || true)}"
DATA_DIR="${YOLO_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/yolotrading}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ ! -d "$REPO_DIR/src/crypto_yolo" ]]; then
  echo "YOLO source tree not found under: $REPO_DIR" >&2
  exit 1
fi

if [[ ! -x "$REPO_DIR/bin/yolo" ]]; then
  echo "YOLO launcher is missing or not executable: $REPO_DIR/bin/yolo" >&2
  exit 1
fi

if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "Python 3 interpreter is not executable: ${PYTHON_BIN:-<not found>}" >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit(f"YOLO requires Python >=3.11; found {sys.version.split()[0]}")
PY

mkdir -p "$UNIT_DIR" "$DATA_DIR"
chmod 700 "$DATA_DIR"

"$PYTHON_BIN" - "$REPO_DIR/deploy/systemd/yolo-daily.service" "$UNIT_DIR/yolo-daily.service" "$REPO_DIR" "$PYTHON_BIN" "$DATA_DIR" <<'PY'
from pathlib import Path
import sys

template, destination, repo_dir, python_bin, data_dir = sys.argv[1:]
text = Path(template).read_text()
text = text.replace("@REPO_DIR@", repo_dir)
text = text.replace("@PYTHON_BIN@", python_bin)
text = text.replace("@DATA_DIR@", data_dir)
Path(destination).write_text(text)
PY


install -m 0644 "$REPO_DIR/deploy/systemd/yolo-daily.timer" "$UNIT_DIR/yolo-daily.timer"

systemctl --user daemon-reload
systemctl --user enable --now yolo-daily.timer

echo
echo "Installed user-level YOLO timer."
echo "Repository : $REPO_DIR"
echo "Python     : $PYTHON_BIN"
echo "State      : $DATA_DIR"
echo
systemctl --user list-timers yolo-daily.timer --no-pager || true

echo
echo "For unattended runs while logged out, enable lingering once:"
echo "  sudo loginctl enable-linger $USER"
echo
echo "Test immediately with:"
echo "  systemctl --user start yolo-daily.service"
echo "  journalctl --user -u yolo-daily.service -n 200 --no-pager"
