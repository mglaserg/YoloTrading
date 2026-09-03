#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${1:-/opt/yolotrading}"
RUN_USER="${2:-yolo}"
PYTHON_BIN="${3:-/usr/bin/python3}"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/install-systemd.sh [repo_dir] [run_user] [python_bin]" >&2
  exit 1
fi

if [[ ! -d "$REPO_DIR/src/crypto_yolo" ]]; then
  echo "YOLO source tree not found under: $REPO_DIR" >&2
  exit 1
fi

if ! id "$RUN_USER" >/dev/null 2>&1; then
  echo "Linux user does not exist: $RUN_USER" >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python interpreter is not executable: $PYTHON_BIN" >&2
  exit 1
fi

sed \
  -e "s|User=yolo|User=$RUN_USER|" \
  -e "s|Group=yolo|Group=$RUN_USER|" \
  -e "s|WorkingDirectory=/opt/yolotrading|WorkingDirectory=$REPO_DIR|" \
  -e "s|Environment=PYTHONPATH=/opt/yolotrading/src|Environment=PYTHONPATH=$REPO_DIR/src|" \
  -e "s|ExecStart=/usr/bin/python3|ExecStart=$PYTHON_BIN|" \
  deploy/systemd/yolo-daily.service > /etc/systemd/system/yolo-daily.service

install -m 0644 deploy/systemd/yolo-daily.timer /etc/systemd/system/yolo-daily.timer
systemctl daemon-reload
systemctl enable --now yolo-daily.timer

echo
echo "Installed YOLO timer."
systemctl list-timers yolo-daily.timer --no-pager
