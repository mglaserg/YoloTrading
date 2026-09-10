#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/.vendor"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required for the no-venv live dependency install." >&2
  exit 2
fi

mkdir -p "$TARGET"
uv pip install --target "$TARGET" --upgrade "hyperliquid-python-sdk==0.24.0"

echo "Installed Hyperliquid live dependencies into: $TARGET"
echo "No virtual environment and no system-Python modification were used."
echo "Check with: ./bin/yolo --execution-status"
