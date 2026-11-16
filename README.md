# AI Usage Pet · Ubuntu

240 × 88 的桌面用量浮条，查看 Codex 剩余额度、重置时间和 DeepSeek API 余额。

![界面示例](docs/preview.png)

## 安装与启动

需要 Python 3.10+，以及已通过 ChatGPT 账户登录的 [Codex CLI](https://github.com/openai/codex)。

```bash
git clone https://github.com/Putaonjfu/ai-usage-pet-ubuntu.git
cd ai-usage-pet-ubuntu
bash install.sh
./start.sh
```

启动后可关闭终端，也可从应用菜单打开。

## 操作

- 下拉切换 Codex / DeepSeek。
- 拖动图标移动窗口，双击或点击箭头展开全部额度。
- 浮条显示额度重置时间，悬停查看倒计时。
- “⋯”菜单：刷新、设置、隐藏、退出，以及数据下次查询时间。
- DeepSeek Key 在设置中填写，保存到系统密钥环；也支持 `DEEPSEEK_API_KEY` 环境变量。
- 设置中可开启登录后自动启动。

Codex 按接口返回的套餐和额度窗口显示，不固定添加 5 小时额度。DeepSeek 查询 API 余额。程序不执行模型任务，不使用重置卡。

Codex 优先沿用代理环境变量；未设置时读取 GNOME 已配置的手动 HTTP/HTTPS 代理。Wayland 下置顶行为由桌面合成器决定。

## 排错与测试

```bash
./start.sh --foreground
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests
```

后台日志：`~/.cache/ai-usage-pet/app.log`。卸载菜单和开机启动项：`bash uninstall.sh`。
