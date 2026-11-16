"""Read the visible credit balance from a user-authenticated billing page.

No API keys, cookies, passwords, or page text are exported or saved by this module.
The browser profile is memory-only and requires the user's own website login.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import time
from urllib.parse import urlsplit

BILLING_URL = 'https://platform.openai.com/settings/organization/billing/overview'
LABEL = re.compile(r'^(?:(?:api\s+)?credit balance|available credit balance|available credits|信用余额|可用余额|剩余余额)\s*[:：]?\s*(.*)$', re.I)
AMOUNT = re.compile(r'^(?P<sign>[-−])?\s*(?:US\$|\$|USD)\s*(?P<after>[-−])?\s*(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,8})?)\s*(?:USD)?$', re.I)


def is_billing_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname == 'platform.openai.com'
                and parsed.port in (None, 443) and parsed.username is None
                and parsed.path.rstrip('/') == '/settings/organization/billing/overview')
    except ValueError:
        return False


def parse_credit_balance(text: str) -> str | None:
    """Accept only money directly paired with an explicit credit-balance label."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    values = set()
    for index, line in enumerate(lines):
        match = LABEL.fullmatch(line)
        if not match:
            continue
        candidate = match.group(1) or (lines[index + 1] if index + 1 < len(lines) else '')
        amount = AMOUNT.fullmatch(candidate)
        if not amount or (amount['sign'] and amount['after']):
            continue
        try:
            number = Decimal(amount['number'].replace(',', ''))
            if amount['sign'] or amount['after']:
                number = -number
            if number.is_finite():
                values.add(number)
        except InvalidOperation:
            continue
    # Multiple conflicting balances are not safe to choose between.
    return format(next(iter(values)), 'f') if len(values) == 1 else None


def create_billing_window(parent):
    """Import Chromium only when the user opens the website connection window."""
    from PySide6.QtCore import QTimer, QUrl, Signal
    from PySide6.QtNetwork import QNetworkProxy, QNetworkProxyFactory
    from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout
    from providers import codex_environment

    class QuietPage(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, message, line_number, source):
            pass

    class BillingWindow(QDialog):
        balanceChanged = Signal(dict)

        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle('OpenAI API · 网页余额')
            self.resize(1060, 760)
            self.connected = False
            self.reading = False
            self.attempts = 0
            self.generation = 0
            self.setStyleSheet('QDialog {background:#faf9f6;} QLabel {color:#67577f;} QPushButton {padding:7px 12px;}')
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel('请登录并选择 API Key 所属组织，再点击“使用当前组织余额”。登录仅在本次运行中保留。'))
            actions = QHBoxLayout()
            connect = QPushButton('使用当前组织余额')
            connect.clicked.connect(self.confirm_organization)
            actions.addWidget(connect)
            back = QPushButton('返回余额页')
            back.clicked.connect(lambda: self.view.setUrl(QUrl(BILLING_URL)))
            actions.addWidget(back)
            actions.addStretch()
            self.status = QLabel('等待网页登录与组织确认')
            actions.addWidget(self.status)
            layout.addLayout(actions)
            self.view = QWebEngineView(self)
            # No storage name means off-the-record: no saved cookies or disk cache.
            self.profile = QWebEngineProfile(self)
            self.page = QuietPage(self.profile, self.view)
            self.view.setPage(self.page)
            layout.addWidget(self.view, 1)
            self.poll = QTimer(self)
            self.poll.setInterval(1000)
            self.poll.timeout.connect(self.read_page)
            self.page.loadFinished.connect(self.loaded)
            self.page.loadStarted.connect(self.loading)
            self.page.urlChanged.connect(self.location_changed)
            self.view.setUrl(QUrl(BILLING_URL))

        def invalidate(self):
            self.generation += 1
            self.reading = False
            self.poll.stop()
            self.balanceChanged.emit({})

        def loading(self):
            if self.connected:
                self.invalidate()
                self.status.setText('正在加载余额页…')

        def location_changed(self, url):
            if self.connected:
                self.invalidate()
                if not is_billing_url(url.toString()):
                    self.status.setText('请登录并返回余额页')

        def loaded(self, ok):
            if self.connected and ok and is_billing_url(self.page.url().toString()):
                self.begin_read()
            elif self.connected:
                self.invalidate()
                self.status.setText('页面未就绪，请登录或刷新')

        def confirm_organization(self):
            if not is_billing_url(self.page.url().toString()):
                self.status.setText('请先登录并返回余额页')
                return
            self.connected = True
            self.invalidate()
            self.begin_read()

        def begin_read(self):
            if not is_billing_url(self.page.url().toString()):
                self.status.setText('请先登录并返回余额页')
                return
            self.attempts = 0
            self.poll.start()
            self.read_page()

        def read_page(self):
            if self.reading or not is_billing_url(self.page.url().toString()):
                return
            self.reading = True
            self.attempts += 1
            generation = self.generation

            def receive(text):
                if generation != self.generation:
                    return
                self.reading = False
                if not is_billing_url(self.page.url().toString()):
                    return
                balance = parse_credit_balance(text)
                if balance is not None:
                    self.poll.stop()
                    self.status.setText('已读取：$' + balance)
                    self.balanceChanged.emit({'balance': balance, 'currency': 'USD',
                                              'checked_at': int(time.time()), 'source': 'billing_web'})
                elif self.attempts >= 20:
                    self.poll.stop()
                    self.balanceChanged.emit({})
                    self.status.setText('未识别到余额，请检查登录、组织与页面内容')
            # Read rendered text only; never inspect credentials, storage or cookies.
            self.page.toPlainText(receive)

        def refresh_balance(self):
            if self.connected:
                self.invalidate()
                if is_billing_url(self.page.url().toString()):
                    self.view.reload()
                else:
                    self.view.setUrl(QUrl(BILLING_URL))

        def closeEvent(self, event):
            event.ignore()
            self.hide()

    environment = codex_environment()
    proxy_url = environment.get('HTTPS_PROXY') or environment.get('https_proxy')
    if proxy_url:
        proxy = urlsplit(proxy_url)
        if proxy.hostname and proxy.scheme in ('http', 'https'):
            QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.ProxyType.HttpProxy,
                proxy.hostname, proxy.port or 8080, proxy.username or '', proxy.password or ''))
        else:
            QNetworkProxyFactory.setUseSystemConfiguration(True)
    else:
        QNetworkProxyFactory.setUseSystemConfiguration(True)
    return BillingWindow()
