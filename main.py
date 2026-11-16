#!/usr/bin/env python3
"""AI Usage Pet: read-only Ubuntu floating widget for Codex and DeepSeek."""
from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSystemTrayIcon, QVBoxLayout, QWidget,
    QMenu, QComboBox,
)

from widgets import PetScene, WindowDrag

from providers import refresh_all, save_deepseek_key, delete_deepseek_key
import preferences

if QApplication.instance() is None:
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

APP_DIR = Path(__file__).resolve().parent
COLOR = {
    "bg": "#faf9f6", "panel": "#ffffff", "panel2": "#eeeaf8",
    "text": "#292b3b", "muted": "#848494", "accent": "#8271c7",
    "warning": "#ca934c", "danger": "#cf7384", "line": "#e7e5ed",
}
STYLE = f"""
QWidget {{ font-family: 'Noto Sans CJK SC', 'Noto Sans', 'Sans Serif'; color: {COLOR['text']}; font-size: 12px; }}
QWidget#Root {{ background: {COLOR['bg']}; border: 1px solid {COLOR['line']}; border-radius: 18px; }}
QFrame#Card {{ background: white; border: 1px solid {COLOR['line']}; border-radius: 14px; }}
QLabel#Muted {{ color: {COLOR['muted']}; }}
QLabel#Title {{ font-size: 15px; font-weight: 700; }}
QLabel#Section {{ font-size: 12px; font-weight: 600; }}
QLabel#Accent {{ color: {COLOR['accent']}; font-weight: 600; }}
QPushButton {{ background: #f0edf6; border: none; border-radius: 8px; padding: 7px 10px; }}
QPushButton:hover {{ background: #e4dff1; }}
QPushButton#Primary {{ background: #8271c7; color: white; font-weight: 600; }}
QProgressBar {{ border: none; background: #eeedf2; height: 6px; border-radius: 3px; }}
QProgressBar::chunk {{ background: {COLOR['accent']}; border-radius: 3px; }}
QLineEdit {{ background: white; border: 1px solid {COLOR['line']}; border-radius: 8px; padding: 8px; }}
QComboBox {{ background: white; border: 1px solid {COLOR['line']}; border-radius: 10px; padding: 8px 10px; min-height: 18px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: white; color: #292b3b; selection-background-color: #eeeaf8; selection-color: #655298; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{background: transparent; width: 5px;}}
QScrollBar::handle:vertical {{background: #d2cbdc; min-height: 24px; border-radius: 2px;}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{height: 0px;}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{background: none;}}
"""


def format_date(timestamp):
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
        return "未提供"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return "未知"


def reset_text(timestamp, now=None):
    """Describe quota restoration, independently of the polling interval."""
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
        return "重置时间未提供", "服务未返回额度重置时间"
    now = now or datetime.now()
    try:
        target = datetime.fromtimestamp(timestamp)
    except (ValueError, OSError, OverflowError):
        return "重置时间未知", "无法识别服务返回的重置时间"
    absolute = target.strftime("%Y-%m-%d %H:%M")
    seconds = (target - now).total_seconds()
    if seconds <= 0:
        return "等待额度更新", f"服务给出的重置时间为 {absolute}，等待下一次查询确认额度"
    days = (target.date() - now.date()).days
    date_label = "今天" if days == 0 else "明日" if days == 1 else target.strftime("%m-%d")
    minutes = max(1, int((seconds + 59) // 60))
    d, rest = divmod(minutes, 1440)
    h, m = divmod(rest, 60)
    countdown = (f"{d} 天 " if d else "") + (f"{h} 小时 " if h else "") + f"{m} 分钟"
    return f"{date_label} {target:%H:%M} 重置", f"额度重置：{absolute}\n距离重置：{countdown}"


def format_duration(minutes):
    if not isinstance(minutes, (int, float)) or minutes <= 0:
        return "额度窗口"
    if minutes >= 6 * 24 * 60:
        return "每周额度" if minutes <= 8 * 24 * 60 else f"{minutes / 1440:g} 天额度"
    if minutes >= 60 and minutes % 60 == 0:
        return f"{int(minutes // 60)} 小时额度"
    return f"{int(minutes)} 分钟额度"


def money(value, currency="CNY"):
    try:
        amount = Decimal(str(value))
        return f"{'¥' if currency == 'CNY' else '$' if currency == 'USD' else currency + ' '}{amount:,.2f}"
    except (InvalidOperation, TypeError):
        return "未提供"


def caption(text):
    l = QLabel(text)
    l.setObjectName("Muted")
    l.setWordWrap(True)
    return l


def card(parent_layout):
    pane = QFrame()
    pane.setObjectName("Card")
    box = QVBoxLayout(pane)
    box.setContentsMargins(14, 13, 14, 13)
    box.setSpacing(8)
    parent_layout.addWidget(pane)
    return box


class RefreshWorker(QThread):
    done = Signal(dict)

    def run(self):
        self.done.emit(refresh_all())


class KeyDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DeepSeek API Key")
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("输入你的 DeepSeek API Key"))
        layout.addWidget(caption("仅保存在本机系统密钥环中；不会写入配置文件。已设置环境变量时，优先使用环境变量。"))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("sk-...")
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.edit)
        self.setStyleSheet(f"QDialog {{background:{COLOR['bg']};}}" + STYLE)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)


class UsageRow(QWidget):
    def __init__(self, title, window, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 5)
        v.setSpacing(5)
        remaining = window["remaining"]
        header = QHBoxLayout()
        header.addWidget(QLabel(title))
        header.addStretch()
        x = QLabel(f"剩余 {remaining:.1f}%")
        x.setStyleSheet(f"color: {COLOR['accent'] if remaining > 25 else COLOR['warning'] if remaining > 10 else COLOR['danger']}; font-weight: 700;")
        header.addWidget(x)
        v.addLayout(header)
        bar = QProgressBar()
        bar.setTextVisible(False)
        bar.setRange(0, 1000)
        bar.setValue(round(remaining * 10))
        color = COLOR['accent'] if remaining > 25 else COLOR['warning'] if remaining > 10 else COLOR['danger']
        bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; border-radius: 4px; }}")
        bar.setFixedHeight(6)
        v.addWidget(bar)
        v.addWidget(caption(f"下次重置：{format_date(window.get('resets_at'))}"))


class UsagePet(QWidget):
    browserBalanceReceived = Signal(dict)

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.prefs = preferences.load()
        self.expanded = False
        self.current = {}
        self.billing_window = None
        self.browser_bridge = None
        self.browser_dialog = None
        self.web_balance = {}
        self.web_state = 'waiting'
        self.browserBalanceReceived.connect(self.apply_web_balance)
        self.notified = set()
        self.worker = None
        self.setWindowTitle("用量小猫 · AI Usage Pet")
        self.setWindowIcon(QIcon(str(APP_DIR / "assets" / "pet.svg")))
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(240)
        self.drag = WindowDrag(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        self.root = QWidget()
        self.root.setObjectName("Root")
        outer.addWidget(self.root)
        self.root_layout = QVBoxLayout(self.root)
        self.root_layout.setContentsMargins(10, 8, 10, 8)
        self.root_layout.setSpacing(8)
        ribbon = QWidget()
        ribbon.setFixedHeight(64)
        ribbon_layout = QHBoxLayout(ribbon)
        ribbon_layout.setContentsMargins(0, 0, 0, 0)
        ribbon_layout.setSpacing(9)
        self.scene = PetScene()
        self.scene.setFixedSize(58, 60)
        self.drag.attach(self.scene)
        ribbon_layout.addWidget(self.scene)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(0)
        self.provider_selector = QComboBox()
        self.provider_selector.addItem("Codex ▾", "codex")
        self.provider_selector.addItem("DeepSeek ▾", "deepseek")
        self.provider_selector.addItem("OpenAI API ▾", "openai")
        self.provider_selector.setStyleSheet("QComboBox {background: transparent; border: none; border-radius: 0px; padding: 0px; min-height: 0px; font-size: 10px; color: #777184;} QComboBox::drop-down {width: 0px;}")
        self.provider_selector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.provider_selector.setFixedHeight(18)
        self.provider_selector.setCurrentIndex(max(0, self.provider_selector.findData(self.prefs.get("selected_provider", "codex"))))
        self.provider_selector.currentIndexChanged.connect(self.provider_changed)
        top.addWidget(self.provider_selector, 1)
        menu_button = QPushButton("⋯")
        menu_button.setToolTip("刷新、设置、退出")
        menu_button.setFixedSize(20, 18)
        menu_button.setStyleSheet("padding: 0; background: transparent; color: #8e8799; font-weight: 700;")
        menu_button.clicked.connect(lambda: self.show_actions(menu_button))
        top.addWidget(menu_button)
        text.addLayout(top)
        value_row = QHBoxLayout()
        value_row.setSpacing(5)
        self.hero_value = QLabel("—")
        self.hero_value.setStyleSheet("font-size: 22px; font-weight: 600; color: #67577f;")
        self.drag.attach(self.hero_value)
        value_row.addWidget(self.hero_value)
        self.hero_caption = QLabel("等待数据")
        self.hero_caption.setStyleSheet("font-size: 10px; color: #908997;")
        self.hero_caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.drag.attach(self.hero_caption)
        value_row.addWidget(self.hero_caption, 1)
        self.toggle_button = QPushButton("⌄")
        self.toggle_button.setToolTip("展开 / 收起完整额度")
        self.toggle_button.setFixedSize(18, 22)
        self.toggle_button.setStyleSheet("padding: 0; background: transparent; color: #8b849a; font-size: 16px;")
        self.toggle_button.clicked.connect(self.toggle)
        value_row.addWidget(self.toggle_button)
        text.addLayout(value_row)
        self.mini_note = QLabel("正在连接…")
        self.mini_note.setStyleSheet("font-size: 10px; color: #8b8594;")
        self.mini_note.setFixedHeight(15)
        self.mini_note.linkActivated.connect(lambda _: self.open_api_billing())
        self.drag.attach(self.mini_note)
        text.addWidget(self.mini_note)
        ribbon_layout.addLayout(text, 1)
        self.root_layout.addWidget(ribbon)
        self.balance_line = caption("正在读取额度…")
        self.balance_line.hide()
        self.sync = caption("准备更新")
        self.sync.hide()
        self.quick_refresh = QPushButton("刷新")
        self.quick_refresh.hide()
        self.quick_refresh.clicked.connect(self.manual_refresh)
        self.drag.attach(self.root)
        self.quota_windows = []

        self.summary_codex = QLabel("—")
        self.summary_reset = QLabel("—")
        self.summary_deepseek = QLabel("—")

        self.details = QWidget()
        d = QVBoxLayout(self.details)
        d.setContentsMargins(0, 0, 0, 0)
        d.setSpacing(11)

        actionbar = QHBoxLayout()
        self.refresh_btn = QPushButton("↻ 立即刷新")
        self.refresh_btn.setObjectName("Primary")
        self.refresh_btn.clicked.connect(self.manual_refresh)
        actionbar.addWidget(self.refresh_btn)
        d.addLayout(actionbar)
        self.codex_box = card(d)
        self.ds_box = card(d)
        self.api_box = card(d)
        self.settings_dialog = QDialog(self)
        self.settings_dialog.setWindowTitle("用量小猫 · 设置")
        self.settings_dialog.setFixedWidth(380)
        self.settings_dialog.setStyleSheet(f"QDialog {{background:{COLOR['bg']};}}" + STYLE)
        settings_layout = QVBoxLayout(self.settings_dialog)
        settings_layout.setContentsMargins(20, 20, 20, 20)
        settings_layout.setSpacing(14)
        self.settings_box = card(settings_layout)
        settings_label = QLabel("偏好设置")
        settings_label.setObjectName("Section")
        self.settings_box.addWidget(settings_label)
        settings_line = QHBoxLayout()
        settings_line.addWidget(caption("刷新间隔"))
        self.interval = QComboBox()
        for n in (1, 3, 5, 10, 15):
            self.interval.addItem(f"{n} 分钟", n)
        idx = self.interval.findData(self.prefs.get("refresh_minutes", 5))
        self.interval.setCurrentIndex(max(0, idx))
        self.interval.currentIndexChanged.connect(self.update_interval)
        settings_line.addWidget(self.interval)
        settings_line.addStretch()
        self.settings_box.addLayout(settings_line)
        self.autostart = QCheckBox("开机自动启动")
        self.autostart.setChecked(preferences.is_autostart())
        self.autostart.toggled.connect(self.toggle_autostart)
        self.settings_box.addWidget(self.autostart)
        self.settings_box.addWidget(caption("额度低于 20%，或 DeepSeek 余额低于 ¥10 时提醒。"))
        self.key_btn = QPushButton("设置 DeepSeek API Key")
        self.key_btn.clicked.connect(self.set_key)
        settings_layout.addWidget(self.key_btn)
        settings_layout.addWidget(caption("密钥保存在系统密钥环。拖动猫咪或背景可移动窗口，双击可展开或收起。"))
        done = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        done.button(QDialogButtonBox.StandardButton.Close).setText("完成")
        done.rejected.connect(self.settings_dialog.hide)
        settings_layout.addWidget(done)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMaximumHeight(340)
        self.scroll.setMinimumHeight(240)
        self.scroll.setWidget(self.details)
        self.details.setAutoFillBackground(False)
        self.scroll.viewport().setAutoFillBackground(False)

        self.scroll.setVisible(False)
        self.root_layout.addWidget(self.scroll)

        self.setStyleSheet(STYLE)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(int(self.prefs.get("refresh_minutes", 5)) * 60000)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_timing)
        self.clock_timer.start(30000)
        self.render_selected()
        self.create_tray()
        QTimer.singleShot(150, self.refresh)
        QTimer.singleShot(0, self.restore_browser_bridge)
        # On GNOME Wayland placement is compositor-controlled. This is best-effort.
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.right() - 380, geo.top() + 100)

    def show_settings(self):
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def provider_changed(self):
        self.prefs["selected_provider"] = self.provider_selector.currentData()
        preferences.save(self.prefs)
        self.render_selected()

    def show_actions(self, anchor):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu {background: #faf9f6; border: 1px solid #e7e5ed; padding: 5px; border-radius: 8px;} QMenu::item {padding: 7px 16px;} QMenu::item:selected {background: #eeeaf8; border-radius: 5px;}")
        menu.addAction("收起详情" if self.expanded else "查看全部额度", self.toggle)
        menu.addAction("刷新用量", self.manual_refresh)
        menu.addAction("设置", self.show_settings)
        if self.provider_selector.currentData() in ("codex", "openai"):
            menu.addAction("连接 Chrome 余额", self.open_api_billing)
            menu.addAction("内置网页登录（备用）", self.open_embedded_billing)
        menu.addSeparator()
        menu.addAction(self.polling_text()).setEnabled(False)
        menu.addSeparator()
        menu.addAction("隐藏到托盘", self.hide_or_quit)
        menu.addAction("退出", self.app.quit)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        menu.deleteLater()

    def render_compact(self, windows):
        self.quota_windows = windows

    def render_selected(self):
        provider = self.provider_selector.currentData()
        codex = self.current.get("codex", {})
        ds = self.current.get("deepseek", {})
        result = codex.get("data", {})
        plan = result.get("plan") or next((b.get("plan") for b in result.get("buckets", []) if b.get("plan")), None)
        plan_label = {"plus": "Plus", "pro": "Pro", "prolite": "Pro Lite"}.get(plan, str(plan).title() if plan else "套餐未知")
        api_mode = result.get("mode") == "api"
        self.provider_selector.setItemText(0, "Codex · API ▾" if api_mode else "Codex · " + plan_label + " ▾")
        self.codex_box.parentWidget().setVisible(provider == "codex")
        self.ds_box.parentWidget().setVisible(provider == "deepseek")
        self.api_box.parentWidget().setVisible(provider == "openai")
        self.hero_value.setText("—")
        self.render_compact([])
        if provider == "codex":
            self.hero_caption.setText("剩余额度")
            if not codex.get("ok"):
                self.balance_line.setText(codex.get("error", "正在读取 Codex 账户…"))
            elif api_mode:
                web_balance = self.current_web_balance()
                if web_balance and result["api"].get("billing_url"):
                    self.hero_value.setText(money(web_balance["balance"], "USD"))
                    self.hero_caption.setText("组织余额")
                    self.balance_line.setText("来源：OpenAI 网页当前组织；请确认该组织与 API Key 对应。")
                else:
                    self.hero_caption.setText("API 余额")
                    self.balance_line.setText(result["api"]["message"])
            else:
                buckets = result.get("buckets", [])
                windows = []
                for bucket in buckets:
                    for window in sorted(bucket.get("windows", []), key=lambda w: w.get("duration_mins") or 0):
                        label = format_duration(window.get("duration_mins"))
                        if len(buckets) > 1:
                            label = (bucket.get("name") or bucket.get("id") or "Codex") + " · " + label
                        windows.append({**window, "label": label})
                self.render_compact(windows)
                main = [w for b in buckets if b.get("id") == "codex" for w in b.get("windows", [])] or windows
                if main:
                    limiting = min(main, key=lambda w: w["remaining"])
                    self.hero_caption.setText(format_duration(limiting.get("duration_mins")).replace("额度", "剩余"))
                    self.hero_value.setText(f"{limiting['remaining']:.0f}%")
                resets = result.get("resets") or {}
                count = resets.get("available")
                info = f"重置卡  {count} 张" if count is not None else "重置卡数量未提供"
                if not windows:
                    info = "服务未返回额度窗口 · " + info
                self.balance_line.setText(info)
        elif provider == "openai":
            balance = self.current_web_balance()
            self.hero_caption.setText("组织余额")
            self.hero_value.setText(money(balance["balance"], "USD") if balance else "—")
            self.hero_value.setStyleSheet("font-size: 21px; font-weight: 600; color: #67577f;")
            self.balance_line.setText("OpenAI 网页当前组织余额；请确认组织与 API Key 对应。" if balance else "在 Chrome 登录余额页，通过扩展连接浮条。")
            self.render_api_balance()
        else:
            self.hero_caption.setText("可用余额")
            self.hero_value.setText(self.summary_deepseek.text())
            self.hero_value.setStyleSheet("font-size: 21px; font-weight: 600; color: #67577f;")
            if not ds.get("ok"):
                self.balance_line.setText(ds.get("error", "正在读取 DeepSeek 余额…"))
            else:
                self.balance_line.setText("充值与赠送余额 · 详情中查看明细")
        if provider == "codex":
            self.hero_value.setStyleSheet("font-size: 22px; font-weight: 600; color: #67577f;")
        self.scene.remaining = limiting["remaining"] if provider == "codex" and codex.get("ok") and self.quota_windows else None
        self.scene.update()
        self.update_timing()
        self.root_layout.activate()
        self.adjustSize()
        QTimer.singleShot(0, self.adjustSize)

    def polling_text(self):
        if self.provider_selector.currentData() == "openai":
            balance = self.current_web_balance()
            if not balance:
                return "网页余额：等待 Chrome 扩展读取"
            return "页面变化时同步 · 每分钟刷新查询（官网可能延迟）"
        if not self.quick_refresh.isEnabled():
            return "账户数据：正在查询…"
        remaining = self.timer.remainingTime()
        if remaining < 0:
            return "数据自动查询未启用"
        next_time = datetime.fromtimestamp(datetime.now().timestamp() + remaining / 1000)
        return "数据下次查询：" + next_time.strftime("%H:%M")

    def update_timing(self):
        if self.web_balance and not self.current_web_balance():
            self.web_balance = {}
            if self.web_state in ('connected', 'refreshing'):
                self.web_state = 'expired'
            self.render_codex(self.current.get("codex", {}))
            self.render_selected()
            return
        provider = self.provider_selector.currentData()
        codex = self.current.get("codex", {})
        ds = self.current.get("deepseek", {})
        details = self.balance_line.text()
        is_api = codex.get("data", {}).get("mode") == "api"
        if provider == "openai" or (provider == "codex" and codex.get("ok") and is_api):
            self.mini_note.removeEventFilter(self.drag)
            self.mini_note.setCursor(Qt.CursorShape.PointingHandCursor)
            if provider == "openai" or codex["data"]["api"].get("billing_url"):
                balance = self.current_web_balance()
                if balance and self.web_state == 'connected':
                    note = "网页更新 " + datetime.fromtimestamp(balance["checked_at"]).strftime("%H:%M") + " ↗"
                else:
                    note = self.web_state_text() + " ↗"
                self.mini_note.setText('<a href="billing" style="color:#8b8594; text-decoration:none;">' + note + '</a>')
            else:
                self.mini_note.setText("余额接口未适配")
        elif provider == "codex" and codex.get("ok") and self.quota_windows:
            self.drag.attach(self.mini_note)
            buckets = codex["data"].get("buckets", [])
            windows = [w for b in buckets if b.get("id") == "codex" for w in b.get("windows", [])] or self.quota_windows
            limiting = min(windows, key=lambda w: w["remaining"])
            text, timing = reset_text(limiting.get("resets_at"))
            self.mini_note.setText(text)
            details = timing + "\n" + details
        elif provider == "deepseek" and ds.get("ok"):
            self.drag.attach(self.mini_note)
            self.mini_note.setText(self.sync.text())
        else:
            self.mini_note.setText("读取失败 · 查看详情" if self.current else "正在连接…")
        web_mode = provider == 'openai' or (provider == 'codex' and is_api)
        tooltip = details + "\n" + (self.web_state_text() if web_mode else self.sync.text()) + "\n" + self.polling_text()
        self.root.setToolTip(tooltip)
        self.hero_value.setToolTip(tooltip)
        self.mini_note.setToolTip(tooltip)

    def current_web_balance(self):
        checked_at = self.web_balance.get("checked_at", 0)
        age = datetime.now().timestamp() - checked_at
        lifetime = 150 if self.web_balance.get('source') == 'chrome_web' else self.prefs.get("refresh_minutes", 5) * 60 + 90
        return self.web_balance if 0 <= age <= lifetime else {}

    def apply_web_balance(self, data):
        if 'state' in data:
            self.web_state = data['state']
            if self.web_state != 'refreshing':
                self.web_balance = {}
        else:
            self.web_balance = data
            self.web_state = 'connected' if data else 'read_failed'
        if self.browser_dialog is not None:
            self.browser_dialog.status.setText("已收到网页余额：" + money(data["balance"], "USD")
                if data.get('balance') is not None else self.web_state_text())
        self.render_codex(self.current.get("codex", {}))
        self.render_selected()

    def web_state_text(self):
        return {'waiting': '等待扩展连接', 'connected': '网页余额已更新',
                'refreshing': '正在更新余额', 'expired': '余额更新中断',
                'login_required': '网页需要登录', 'page_missing': '正在恢复余额页',
                'read_failed': '网页读取失败', 'network_error': '网页连接超时',
                'multiple_pages': '请选择余额页', 'disconnected': '已取消配对'}.get(self.web_state, '等待网页更新')

    def start_browser_bridge(self):
        if self.browser_bridge is None:
            from browser_balance import BalanceBridge
            self.browser_bridge = BalanceBridge(self.browserBalanceReceived.emit, 1,
                state_path=preferences.CONFIG.parent / 'browser-pairing.json')
            self.app.aboutToQuit.connect(self.browser_bridge.close)

    def restore_browser_bridge(self):
        if (preferences.CONFIG.parent / 'browser-pairing.json').exists():
            try:
                self.start_browser_bridge()
            except OSError:
                pass  # The connection dialog reports the failure when opened.

    def open_api_billing(self):
        try:
            self.start_browser_bridge()
        except OSError as error:
            QMessageBox.warning(self, "连接不可用", "无法恢复本机余额连接：" + str(error))
            return
        if self.browser_dialog is None:
            from browser_balance import create_browser_dialog
            self.browser_dialog = create_browser_dialog(self, self.browser_bridge)
        self.browser_dialog.status.setText(self.web_state_text())
        self.browser_dialog.show()
        self.browser_dialog.raise_()
        self.browser_dialog.activateWindow()

    def render_api_balance(self):
        self.clear_layout(self.api_box)
        self.api_box.addWidget(QLabel("OpenAI API · 网页组织余额"))
        balance = self.current_web_balance()
        if balance:
            self.api_box.addWidget(QLabel(money(balance["balance"], "USD")))
            self.api_box.addWidget(caption("网页读取于 " + datetime.fromtimestamp(balance["checked_at"]).strftime("%m-%d %H:%M")))
        else:
            self.api_box.addWidget(caption(self.web_state_text() + '。在 Chrome 打开官方余额页，扩展中点“使用当前页”可切回正确标签。'))
        self.api_box.addWidget(caption("显示浏览器当前组织余额，不代表单个 Key 的独立预算。页面变化时同步，每分钟刷新查询。"))
        button = QPushButton("连接 Chrome 余额 ↗")
        button.clicked.connect(self.open_api_billing)
        self.api_box.addWidget(button)

    def open_embedded_billing(self):
        if self.billing_window is None:
            try:
                from web_balance import create_billing_window
                self.billing_window = create_billing_window(self)
                self.billing_window.balanceChanged.connect(self.apply_web_balance)
            except (ImportError, RuntimeError):
                QMessageBox.warning(self, "网页组件不可用", "当前环境无法启动内置网页窗口。可先在浏览器查看 OpenAI 账单页。")
                return
        self.billing_window.show()
        self.billing_window.raise_()
        self.billing_window.activateWindow()

    def create_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(QIcon(str(APP_DIR / "assets" / "pet.svg")), self)
        menu = QMenu()
        show = menu.addAction("显示 / 隐藏宠物")
        show.triggered.connect(lambda: self.hide() if self.isVisible() else self.show_and_raise())
        settings = menu.addAction("设置")
        settings.triggered.connect(self.show_settings)
        refresh = menu.addAction("刷新用量")
        refresh.triggered.connect(self.manual_refresh)
        menu.addSeparator()
        exit_action = menu.addAction("退出")
        exit_action.triggered.connect(self.app.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(self.tray_activated)
        tray.setToolTip("AI Usage Pet - 账户用量监控")
        tray.show()
        self.tray = tray

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show_and_raise()

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        event.ignore()
        self.hide_or_quit()

    def hide_or_quit(self):
        self.settings_dialog.hide()
        if self.tray and self.tray.isVisible():
            self.hide()
        else:
            self.app.quit()

    def toggle(self):
        self.expanded = not self.expanded
        self.setFixedWidth(320 if self.expanded else 240)
        self.scroll.setVisible(self.expanded)
        self.toggle_button.setText("⌃" if self.expanded else "⌄")
        self.root_layout.activate()
        self.adjustSize()
        QTimer.singleShot(0, self.adjustSize)

    def manual_refresh(self):
        if self.provider_selector.currentData() == "openai":
            self.open_api_billing()
            if self.browser_dialog is not None and self.current_web_balance():
                self.browser_dialog.status.setText('立即读取请在 Chrome 扩展中点击“立即刷新”；定时读取会自动执行。')
            return
        self.refresh()

    def refresh(self):
        if self.worker and self.worker.isRunning():
            return
        if self.billing_window and self.billing_window.connected and self.current.get("codex", {}).get("data", {}).get("mode") == "api":
            self.billing_window.refresh_balance()
        self.sync.setText("正在更新…")
        self.scene.busy = True
        self.refresh_btn.setEnabled(False)
        self.quick_refresh.setEnabled(False)
        self.update_timing()
        self.worker = RefreshWorker(self)
        task = self.worker
        task.done.connect(self.updated)
        task.finished.connect(lambda: self.worker_finished(task))
        task.start()

    def worker_finished(self, task):
        if self.worker is task:
            self.worker = None
        task.deleteLater()

    def updated(self, data):
        self.scene.busy = False
        self.current = data
        self.refresh_btn.setEnabled(True)
        self.quick_refresh.setEnabled(True)
        self.sync.setText("更新于 " + datetime.fromtimestamp(data["checked_at"]).strftime("%H:%M"))
        codex = data.get("codex", {})
        ds = data.get("deepseek", {})
        self.render_codex(codex)
        self.render_deepseek(ds)
        self.render_selected()
        self.alert_if_needed(codex, ds)
        self.adjustSize()

    @staticmethod
    def clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
            if item.layout():
                UsagePet.clear_layout(item.layout())

    def render_codex(self, codex):
        self.clear_layout(self.codex_box)
        title = QLabel("Codex / ChatGPT")
        title.setObjectName("Section")
        self.codex_box.addWidget(title)
        if not codex.get("ok"):
            self.summary_codex.setText("—")
            self.summary_reset.setText("—")
            self.codex_box.addWidget(caption(codex.get("error", "未连接")))
            self.codex_box.addWidget(caption("可在终端执行 codex login status 检查登录状态。"))
            return
        result = codex["data"]
        if result.get("mode") == "api":
            title.setText("Codex / " + result["api"]["provider"])
            self.summary_codex.setText("—")
            self.summary_reset.setText("—")
            self.codex_box.addWidget(QLabel("当前登录方式：API Key"))
            balance = self.current_web_balance() if result["api"].get("billing_url") else {}
            if balance:
                self.codex_box.addWidget(QLabel("网页组织余额：" + money(balance["balance"], "USD")))
                self.codex_box.addWidget(caption("此余额来自你确认的网页组织，不代表单个 Key 的独立预算。"))
            else:
                self.codex_box.addWidget(caption(result["api"]["message"]))
            if result["api"].get("billing_url"):
                button = QPushButton("连接 Chrome / 更新组织余额 ↗")
                button.clicked.connect(self.open_api_billing)
                self.codex_box.addWidget(button)
            return
        account = " · ".join(filter(None, (result.get("email"), str(result["plan"]).upper() if result.get("plan") else None)))
        if account:
            self.codex_box.addWidget(caption(account))
        buckets = result.get("buckets") or []
        windows_all = []
        if not buckets:
            self.codex_box.addWidget(caption("服务未返回额度窗口，可能需要更新 Codex CLI。"))
        for b in buckets:
            heading = b.get("name") or b.get("id") or "Codex"
            # The main pool gets the simple label; other limit buckets keep their IDs.
            if b.get("id") != "codex":
                self.codex_box.addWidget(caption("额度类型：" + str(heading)))
            for w in b.get("windows", []):
                windows_all.append(w)
                self.codex_box.addWidget(UsageRow(format_duration(w.get("duration_mins")), w))
        main_windows = [w for b in buckets if b.get("id") == "codex" for w in b.get("windows", [])]
        display_windows = main_windows or windows_all
        if display_windows:
            self.summary_codex.setText(f"{min(w['remaining'] for w in display_windows):.0f}%")
        else:
            self.summary_codex.setText("—")
        resets = result.get("resets")
        if resets is None or resets.get("available") is None:
            self.summary_reset.setText("—")
            self.codex_box.addWidget(caption("重置卡：此账户/版本未返回可用数量"))
        else:
            self.summary_reset.setText(str(resets["available"]) + " 张")
            self.codex_box.addWidget(QLabel(f"可用重置卡：{resets['available']} 张"))
            detail_rows = resets.get("details")
            if detail_rows:
                for entry in detail_rows[:6]:
                    self.codex_box.addWidget(caption(f"• {entry['title']} · 到期 {format_date(entry['expires_at'])}"))
            elif detail_rows is None:
                self.codex_box.addWidget(caption("没有返回重置卡到期明细"))
        summary = result.get("token_summary")
        if isinstance(summary, dict) and summary.get("lifetimeTokens") is not None:
            self.codex_box.addWidget(caption(f"账户累计 Token 活动量：{summary['lifetimeTokens']:,}"))

    def render_deepseek(self, ds):
        self.clear_layout(self.ds_box)
        header = QLabel("DeepSeek API 余额")
        header.setObjectName("Section")
        self.ds_box.addWidget(header)
        if not ds.get("ok"):
            self.summary_deepseek.setText("—")
            self.ds_box.addWidget(caption(ds.get("error", "未连接")))
            return
        data = ds["data"]
        balances = data.get("balances", [])
        if not balances:
            self.summary_deepseek.setText("—")
            self.ds_box.addWidget(caption("接口未返回余额数据"))
            return
        first = next((b for b in balances if b.get("currency") == "CNY"), balances[0])
        self.summary_deepseek.setText(money(first.get("total"), first.get("currency")))
        for b in balances:
            cc = b.get("currency")
            line = QLabel(f"{cc} 可用总额：{money(b.get('total'), cc)}")
            line.setObjectName("Accent")
            self.ds_box.addWidget(line)
            self.ds_box.addWidget(caption(f"充值：{money(b.get('topped_up'), cc)}    赠送：{money(b.get('granted'), cc)}"))
        self.ds_box.addWidget(caption("API 可调用：" + ("是" if data.get("is_available") else "否") if data.get("is_available") is not None else "API 可调用：未知"))

    def alert_if_needed(self, codex, ds):
        if codex.get("ok"):
            buckets = codex["data"].get("buckets", [])
            main_windows = [w for b in buckets if b.get("id") == "codex" for w in b.get("windows", [])]
            all_windows = main_windows or [w for b in buckets for w in b.get("windows", [])]
            low = bool(all_windows) and min(w["remaining"] for w in all_windows) <= self.prefs.get("warn_codex_below", 20)
            self.alert("codex", low, "Codex 额度提醒", "剩余额度已降至 20% 或更低")
        if ds.get("ok"):
            for b in ds["data"].get("balances", []):
                if b.get("currency") != "CNY":
                    continue
                try:
                    low = Decimal(str(b["total"])) < Decimal(str(self.prefs.get("warn_deepseek_cny_below", 10)))
                    self.alert("deepseek", low, "DeepSeek 余额提醒", f"当前余额：{money(b['total'], 'CNY')}")
                except (InvalidOperation, ValueError):
                    pass

    def alert(self, identifier, active, title, msg):
        if not active:
            self.notified.discard(identifier)
        elif identifier not in self.notified:
            self.notified.add(identifier)
            if self.tray and self.tray.supportsMessages():
                self.tray.showMessage(title, msg, QSystemTrayIcon.MessageIcon.Warning, 6000)

    def update_interval(self):
        v = self.interval.currentData()
        if isinstance(v, int):
            self.prefs["refresh_minutes"] = v
            preferences.save(self.prefs)
            self.timer.start(v * 60000)
            self.update_timing()

    def toggle_autostart(self, enabled):
        try:
            preferences.set_autostart(enabled, APP_DIR / "start.sh")
        except OSError as exc:
            QMessageBox.warning(self, "开机启动失败", str(exc))
            self.autostart.blockSignals(True)
            self.autostart.setChecked(not enabled)
            self.autostart.blockSignals(False)

    def set_key(self):
        dlg = KeyDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            entered = dlg.edit.text().strip()
            if not entered:
                QMessageBox.information(self, "未修改", "未输入新的密钥，配置不变。")
                return
            try:
                save_deepseek_key(entered)
            except Exception as exc:
                QMessageBox.warning(self, "保存失败", str(exc))
                return
            QMessageBox.information(self, "已保存", "密钥已保存在系统密钥环中。")
            self.refresh()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AI Usage Pet")
    app.setQuitOnLastWindowClosed(False)
    pet = UsagePet(app)
    pet.show()
    if "--connect-billing" in sys.argv:
        QTimer.singleShot(100, pet.open_api_billing)
    code = app.exec()
    if pet.worker and pet.worker.isRunning():
        # QThread must finish before its QObject is destroyed. Network calls
        # have finite request timeouts, so this cannot block indefinitely.
        pet.worker.wait()
    return code

if __name__ == "__main__":
    sys.exit(main())
