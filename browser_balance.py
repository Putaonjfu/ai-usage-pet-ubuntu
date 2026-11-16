"""Session-only, authenticated loopback receiver for the companion extension."""
from __future__ import annotations

from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
import time

DECIMAL = re.compile(r'-?\d{1,12}(?:\.\d{1,8})?\Z')
EXTENSION_ORIGIN = re.compile(r'chrome-extension://[a-p]{32}\Z')


class BalanceBridge:
    def __init__(self, on_balance, refresh_minutes=5):
        self.token = secrets.token_hex(32)
        self.refresh_minutes = max(1, min(60, int(refresh_minutes)))
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def log_message(self, *_):
                pass  # Never log the pairing code, request body or headers.

            def allowed_origin(self):
                origin = self.headers.get('Origin')
                return origin is None or EXTENSION_ORIGIN.fullmatch(origin) is not None

            def valid_host(self):
                return self.headers.get('Host') == f'127.0.0.1:{bridge.port}'

            def reply(self, code):
                body = json.dumps({'ok': code == 200, 'refresh_minutes': bridge.refresh_minutes}).encode()
                self.send_response(code)
                origin = self.headers.get('Origin', '')
                if EXTENSION_ORIGIN.fullmatch(origin):
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Vary', 'Origin')
                    self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
                    self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self.reply(200 if self.path == '/balance' and self.valid_host() and self.allowed_origin() else 403)

            def do_POST(self):
                if self.path != '/balance' or not self.valid_host() or not self.allowed_origin():
                    self.reply(403)
                    return
                supplied = self.headers.get('Authorization', '')
                if not secrets.compare_digest(supplied.encode('utf-8'), ('Bearer ' + bridge.token).encode('ascii')):
                    self.reply(401)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 256 or self.headers.get_content_type() != 'application/json':
                        raise ValueError()
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict) or set(data) != {'balance'}:
                        raise ValueError()
                    balance = data['balance']
                    if balance is not None and (not isinstance(balance, str) or not DECIMAL.fullmatch(balance)):
                        raise ValueError()
                except (ValueError, UnicodeError, TimeoutError, OSError):
                    self.reply(400)
                    return
                if balance is None:
                    on_balance({})
                else:
                    on_balance({'balance': format(Decimal(balance), 'f'), 'currency': 'USD',
                                'checked_at': int(time.time()), 'source': 'chrome_web'})
                self.reply(200)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.1}, daemon=True)
        self.thread.start()

    @property
    def pairing_code(self):
        return f'{self.port}:{self.token}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)


def create_browser_dialog(parent, bridge):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QApplication, QDialog, QLabel, QLineEdit, QPushButton, QVBoxLayout
    from web_balance import BILLING_URL

    dialog = QDialog(parent)
    dialog.setWindowTitle('连接 Chrome · API 余额')
    dialog.resize(540, 340)
    dialog.setStyleSheet('QDialog {background:#faf9f6;} QLabel {color:#292b3b;} QPushButton {padding:8px;}')
    layout = QVBoxLayout(dialog)
    for text in [
        '1. 在系统 Chrome 打开 chrome://extensions，开启“开发者模式”，加载下面的扩展文件夹。',
        '2. 在 Chrome 登录 OpenAI 余额页，选择 API Key 所属组织，点击扩展图标。',
        '3. 将连接码粘贴到扩展中，确认组织后连接。请保留该余额标签页。',
    ]:
        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)
    extension_path = Path(__file__).resolve().parent / 'chrome-extension'
    path = QLineEdit(str(extension_path))
    path.setReadOnly(True)
    layout.addWidget(path)
    code = QLineEdit(bridge.pairing_code)
    code.setReadOnly(True)
    layout.addWidget(code)
    copy = QPushButton('复制连接码')
    copy.clicked.connect(lambda: QApplication.clipboard().setText(bridge.pairing_code))
    layout.addWidget(copy)
    website = QPushButton('在默认浏览器打开 OpenAI 余额页')
    website.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(BILLING_URL)))
    layout.addWidget(website)
    note = QLabel(f'扩展每 {bridge.refresh_minutes} 分钟刷新并读取该标签页，只将金额传给本机。连接码在小工具重启后失效。')
    note.setWordWrap(True)
    layout.addWidget(note)
    dialog.status = QLabel('等待 Chrome 扩展连接…')
    layout.addWidget(dialog.status)
    return dialog
