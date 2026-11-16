#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
python3 -m venv .venv || { echo '请安装 python3-venv，例如 sudo apt install python3-venv' >&2; exit 1; }
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
chmod +x start.sh
mkdir -p "$HOME/.local/share/applications"
# Paths are enclosed in desktop-entry double quotes.
ESCAPED_DIR="${DIR//\\/\\\\}"
ESCAPED_DIR="${ESCAPED_DIR//\"/\\\"}"
cat > "$HOME/.local/share/applications/ai-usage-pet.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=AI Usage Pet
Name[zh_CN]=AI 用量宠物
Comment=Codex and DeepSeek usage monitor
Exec="$ESCAPED_DIR/start.sh"
Icon=$ESCAPED_DIR/assets/pet.svg
Terminal=false
Categories=Utility;Monitor;
EOF
echo ''
echo '安装完成。运行: ./start.sh'
echo '也可以在应用菜单搜索 “AI 用量宠物”。'
