"""Authenticated loopback receiver, with an optional persistent local pairing."""
from __future__ import annotations

from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time

DECIMAL = re.compile(r'-?\d{1,12}(?:\.\d{1,8})?\Z')
EXTENSION_ORIGIN = re.compile(r'chrome-extension://[a-p]{32}\Z')
STATES = {'refreshing', 'login_required', 'page_missing', 'read_failed', 'network_error', 'multiple_pages', 'disconnected'}


def read_pairing(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor) as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError('本机配对文件权限应为 600，且归当前用户所有')
        try:
            data = json.loads(file.read(1024))
            if (not isinstance(data, dict) or set(data) != {'port', 'token'}
                    or type(data['port']) is not int or not 1 <= data['port'] <= 65535
                    or not isinstance(data['token'], str)
                    or not re.fullmatch(r'[a-f0-9]{64}', data['token'])):
                raise ValueError()
            return data
        except (ValueError, UnicodeError) as exc:
            raise OSError('本机配对文件损坏，请检查后重新配对') from exc


def save_pairing(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as file:
            json.dump(data, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class BalanceBridge:
    def __init__(self, on_balance, refresh_minutes=5, state_path=None):
        saved = read_pairing(state_path) if state_path is not None else None
        self.token = saved['token'] if saved else secrets.token_hex(32)
        self.refresh_minutes = max(1, min(60, int(refresh_minutes)))
        self.last_contact_at = 0
        self.last_balance_at = 0
        self.last_state = 'waiting'
        self.client_version = None
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

            def authenticated(self):
                supplied = self.headers.get('Authorization', '')
                return secrets.compare_digest(supplied.encode('utf-8'), ('Bearer ' + bridge.token).encode('ascii'))

            def reply(self, code, payload=None):
                body = json.dumps(payload if payload is not None else {'ok': code == 200, 'refresh_minutes': bridge.refresh_minutes}).encode()
                self.send_response(code)
                origin = self.headers.get('Origin', '')
                if EXTENSION_ORIGIN.fullmatch(origin):
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Vary', 'Origin')
                    self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                    self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self.reply(200 if self.path == '/balance' and self.valid_host() and self.allowed_origin() else 403)

            def do_GET(self):
                if self.path != '/status' or not self.valid_host() or not self.allowed_origin():
                    self.reply(403)
                elif not self.authenticated():
                    self.reply(401)
                else:
                    self.reply(200, {'ok': True, 'last_contact_at': bridge.last_contact_at,
                        'last_balance_at': bridge.last_balance_at, 'state': bridge.last_state,
                        'client_version': bridge.client_version})

            def do_POST(self):
                if self.path != '/balance' or not self.valid_host() or not self.allowed_origin():
                    self.reply(403)
                    return
                if not self.authenticated():
                    self.reply(401)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 256 or self.headers.get_content_type() != 'application/json':
                        raise ValueError()
                    data = json.loads(self.rfile.read(length))
                    version = data.pop('client_version', None) if isinstance(data, dict) else None
                    if version is not None and (not isinstance(version, str) or not re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){2,3}', version)):
                        raise ValueError()
                    if isinstance(data, dict) and set(data) == {'state'}:
                        if not isinstance(data['state'], str) or data['state'] not in STATES:
                            raise ValueError()
                        bridge.last_contact_at = int(time.time())
                        bridge.last_state = data['state']
                        bridge.client_version = version
                        on_balance({'state': data['state'], 'received_at': bridge.last_contact_at})
                        self.reply(200)
                        return
                    if (not isinstance(data, dict) or 'balance' not in data
                            or set(data) - {'balance', 'sampled_at'}):
                        raise ValueError()
                    balance = data['balance']
                    if balance is not None and (not isinstance(balance, str) or not DECIMAL.fullmatch(balance)):
                        raise ValueError()
                    sampled = data.get('sampled_at', int(time.time()))
                    if type(sampled) is not int or not 0 <= time.time() - sampled <= 150:
                        raise ValueError()
                except (ValueError, UnicodeError, TimeoutError, OSError):
                    self.reply(400)
                    return
                if balance is None:
                    bridge.last_state = 'read_failed'
                    on_balance({})
                else:
                    bridge.last_state = 'connected'
                    bridge.last_balance_at = int(time.time())
                    on_balance({'balance': format(Decimal(balance), 'f'), 'currency': 'USD',
                                'checked_at': sampled, 'source': 'chrome_web'})
                bridge.last_contact_at = int(time.time())
                bridge.client_version = version
                self.reply(200)

        self.server = ThreadingHTTPServer(('127.0.0.1', saved['port'] if saved else 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        if state_path is not None and saved is None:
            try:
                save_pairing(state_path, {'port': self.port, 'token': self.token})
            except OSError:
                self.server.server_close()
                raise
        self.closed = False
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.1}, daemon=True)
        self.thread.start()

    @property
    def pairing_code(self):
        return f'{self.port}:{self.token}'

    def close(self):
        if self.closed:
            return
        self.closed = True
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
        '3. 首次粘贴连接码并确认组织，之后自动重连。扩展会创建独立的最小化余额窗口。',
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
    note = QLabel('独立余额窗口请保持最小化，普通 Chrome 窗口可以关闭。完全退出 Chrome 会停止更新；网页登录失效时，在扩展中点“打开独立余额窗口”。')
    note.setWordWrap(True)
    layout.addWidget(note)
    dialog.status = QLabel('等待 Chrome 扩展连接…')
    layout.addWidget(dialog.status)
    return dialog
