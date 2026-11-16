#!/usr/bin/env bash
set -euo pipefail
rm -f "$HOME/.local/share/applications/ai-usage-pet.desktop" "$HOME/.config/autostart/ai-usage-pet.desktop"
echo '已移除应用菜单项和开机启动项。程序目录、设置和系统密钥环凭据保持不变；需要时可手动删除。'
