# AI Usage Pet · Ubuntu

240 × 88 的桌面用量浮条，查看 Codex 剩余额度、重置时间和 DeepSeek API 余额。

![界面示例](docs/preview.png)

## 安装与启动

需要 Python 3.10+，以及已登录的 [Codex CLI](https://github.com/openai/codex)。

```bash
git clone https://github.com/Putaonjfu/ai-usage-pet-ubuntu.git
cd ai-usage-pet-ubuntu
bash install.sh
./start.sh
```

启动后可关闭终端，也可从应用菜单打开。

## 操作

- 下拉切换 Codex / DeepSeek / OpenAI API。
- 拖动图标移动窗口，双击或点击箭头展开全部额度。
- 浮条显示额度重置时间，悬停查看倒计时。
- “⋯”菜单：刷新、设置、隐藏、退出，以及数据下次查询时间。
- DeepSeek Key 在设置中填写，保存到系统密钥环；也支持 `DEEPSEEK_API_KEY` 环境变量。
- 设置中可开启登录后自动启动。

Codex 自动识别登录方式：ChatGPT 账号显示套餐额度；API Key 登录显示组织余额。账号额度按接口返回，不固定添加 5 小时窗口。程序不执行模型任务，不使用重置卡。

OpenAI 网页余额：在浮条“⋯ → 连接 Chrome 余额”中复制扩展路径和连接码。在 Chrome 的 `chrome://extensions` 开启开发者模式，加载 `chrome-extension` 文件夹。在 Chrome 登录官方余额页、选好组织，点击扩展图标，粘贴连接码并确认一次。之后自动重连，页面金额变化时同步，每分钟刷新查询；官网记录可能延迟。扩展自动维护一个后台余额标签页，网页登录过期时需重新登录，返回余额页后自动恢复。显示浏览器当前组织余额，不代表单个 Key 的预算。下拉列表可单独选择 OpenAI API。

配对仅保存在本机；浮条配置 `~/.config/ai-usage-pet/browser-pairing.json` 权限为 600，扩展使用本地存储。可在扩展中“取消配对”停止更新。不读取或导出 Cookie、密码、API Key；无有效数据时停止显示旧余额。

关闭浏览器窗口后继续更新：在 `chrome://settings/system` 启用“关闭 Google Chrome 后继续运行后台应用”。扩展使用 `background` 权限；没有浏览器窗口时会维护一个最小化的余额窗口，不抢焦点。从菜单“退出”Chrome、禁用扩展或系统休眠时无法查询；官网记录可能延迟。网页登录过期仍需重新登录。

Codex 优先沿用代理环境变量；未设置时读取 GNOME 已配置的手动 HTTP/HTTPS 代理。Wayland 下置顶行为由桌面合成器决定。

## 排错与测试

```bash
./start.sh --foreground
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests
node tests/test_extension.js
```

后台日志：`~/.cache/ai-usage-pet/app.log`。卸载菜单和开机启动项：`bash uninstall.sh`。
