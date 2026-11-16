#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$DIR/.venv/bin/python" ]]; then
  echo "请先运行: bash '$DIR/install.sh'" >&2
  exit 1
fi
cd "$DIR"
# For troubleshooting only: keep the GUI attached and print logs in the terminal.
if [[ "${1:-}" == "--foreground" ]]; then
  shift
  exec "$DIR/.venv/bin/python" "$DIR/main.py" "$@"
fi
exec "$DIR/.venv/bin/python" "$DIR/launcher.py" "$@"
