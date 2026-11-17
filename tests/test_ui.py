"""UI regression checks without network access or writes to user preferences."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock
from copy import deepcopy
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtCore import Qt, QEvent, QPointF, QPoint
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication, QProgressBar
from PySide6.QtTest import QTest
from main import UsagePet
from widgets import WindowDrag

DATA = {'checked_at': 1800000000, 'codex': {'ok': True, 'data': {
    'mode': 'chatgpt', 'email': 'demo@example.com', 'plan': 'plus', 'buckets': [
        {'id': 'codex', 'windows': [
            {'remaining': 76, 'duration_mins': 300, 'resets_at': 1800003600},
            {'remaining': 42, 'duration_mins': 10080, 'resets_at': 1800400000}]}],
    'resets': {'available': 2, 'details': []}}},
    'deepseek': {'ok': True, 'data': {'balances': [
        {'currency': 'CNY', 'total': '28.50', 'topped_up': '25', 'granted': '3.50'}], 'is_available': True}}}

class UI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.config_dir = tempfile.TemporaryDirectory()
        patch('preferences.CONFIG', Path(self.config_dir.name) / 'settings.json').start()
        patch('preferences.save').start()
        self.no_refresh = patch.object(UsagePet, 'refresh').start()
        patch.object(UsagePet, 'create_tray', lambda w: setattr(w, 'tray', None)).start()
        self.pet = UsagePet(self.app)
        self.pet.show()
        self.app.processEvents()

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.hide()
        self.pet.deleteLater()
        self.app.processEvents()
        patch.stopall()
        self.config_dir.cleanup()

    def test_live_data_and_unavailable_clear_previous_values(self):
        self.pet.updated(DATA)
        self.app.processEvents()
        self.assertEqual((self.pet.width(), self.pet.height()), (240, 88))
        self.assertEqual(self.pet.scene.remaining, 42)
        self.pet.toggle()
        self.app.processEvents()
        bars = self.pet.codex_box.parentWidget().findChildren(QProgressBar)
        self.assertEqual([b.value() for b in bars if b.isVisible()], [760, 420])
        self.pet.toggle()
        self.assertEqual(self.pet.hero_value.text(), '42%')
        self.assertIn('Plus', self.pet.provider_selector.itemText(0))
        self.pet.provider_selector.setCurrentIndex(1)
        self.assertEqual(self.pet.hero_value.text(), '¥28.50')
        self.assertEqual(self.pet.quota_windows, [])
        self.pet.updated({'checked_at': 1800000001, 'codex': {'ok': False, 'error': '读取失败'}, 'deepseek': {'ok': False, 'error': '余额读取失败'}})
        self.assertEqual(self.pet.hero_value.text(), '—')
        self.assertIn('余额读取失败', self.pet.balance_line.text())
        self.pet.provider_selector.setCurrentIndex(0)
        self.assertIn('读取失败', self.pet.balance_line.text())
        self.assertEqual(self.pet.summary_reset.text(), '—')
        self.assertEqual(self.pet.quota_windows, [])

    def test_prolite_only_renders_returned_weekly_window(self):
        data = deepcopy(DATA)
        result = data['codex']['data']
        result['plan'] = 'prolite'
        result['buckets'][0]['windows'] = [{'remaining': 8, 'duration_mins': 10080, 'resets_at': 1800400000}]
        self.pet.updated(data)
        self.assertIn('Pro Lite', self.pet.provider_selector.itemText(0))
        self.assertEqual(len(self.pet.quota_windows), 1)
        self.assertEqual(self.pet.hero_value.text(), '8%')
        self.assertIn('每周', self.pet.hero_caption.text())
        self.assertNotIn('5 小时', str(self.pet.quota_windows))
        self.assertEqual(self.pet.scene.remaining, 8)

    def test_all_buckets_are_visible_not_truncated_to_two(self):
        data = deepcopy(DATA)
        data['codex']['data']['buckets'].append({'id': 'special', 'name': 'Special', 'windows': [
            {'remaining': 33, 'duration_mins': 60}, {'remaining': 44, 'duration_mins': 1440}]})
        self.pet.updated(data)
        self.assertEqual(len(self.pet.quota_windows), 4)
        self.app.processEvents()
        self.assertEqual(self.pet.height(), 88)
        self.pet.toggle()
        self.app.processEvents()
        bars = self.pet.codex_box.parentWidget().findChildren(QProgressBar)
        self.assertEqual(len([b for b in bars if b.isVisible()]), 4)

    def test_switching_to_api_clears_plan_quota_and_resets(self):
        from providers import api_mode_info
        data = deepcopy(DATA)
        self.pet.updated(data)
        self.assertEqual(self.pet.hero_value.text(), '42%')
        data['codex']['data'] = api_mode_info({})
        self.pet.updated(data)
        self.app.processEvents()
        self.assertEqual(self.pet.hero_value.text(), '—')
        self.assertIn('API', self.pet.provider_selector.itemText(0))
        self.assertEqual(self.pet.quota_windows, [])
        self.assertIsNone(self.pet.scene.remaining)
        self.assertEqual(self.pet.summary_reset.text(), '—')
        self.assertIn('等待扩展连接', self.pet.mini_note.text())
        self.assertEqual((self.pet.width(), self.pet.height()), (240, 88))
        with patch('web_balance.create_billing_window') as create_window:
            self.pet.open_embedded_billing()
            create_window.assert_called_once_with(self.pet)
            self.pet.billing_window.show.assert_called_once()
        self.pet.updated(DATA)
        self.assertEqual(self.pet.hero_value.text(), '42%')

    def test_account_login_pauses_web_monitoring_and_ignores_queued_receipts(self):
        from providers import api_mode_info
        import time
        data = deepcopy(DATA)
        data['codex']['data'] = api_mode_info({})
        self.pet.updated(data)
        self.pet.browser_bridge = Mock()
        sample = {'balance': '12.34', 'checked_at': int(time.time()), 'source': 'chrome_web'}
        self.pet.apply_web_balance(sample)
        self.pet.provider_selector.setCurrentIndex(self.pet.provider_selector.findData('openai'))
        self.assertEqual(self.pet.hero_value.text(), '$12.34')
        self.pet.updated(DATA)
        self.pet.browser_bridge.set_monitoring_mode.assert_called_with('chatgpt')
        self.assertEqual(self.pet.hero_value.text(), '—')
        self.assertEqual(self.pet.web_state, 'paused')
        self.pet.apply_web_balance(sample)
        self.assertEqual(self.pet.web_balance, {})
        self.pet.provider_selector.setCurrentIndex(0)
        self.assertEqual(self.pet.hero_value.text(), '42%')
        self.pet.updated(data)
        self.pet.browser_bridge.set_monitoring_mode.assert_called_with('api')
        self.pet.apply_web_balance(sample)
        self.assertEqual(self.pet.hero_value.text(), '$12.34')

    def test_loopback_balance_reaches_qt_and_connection_dialog(self):
        import http.client
        import json
        from providers import api_mode_info
        data = deepcopy(DATA)
        data['codex']['data'] = api_mode_info({})
        self.pet.updated(data)
        self.pet.provider_selector.setCurrentIndex(self.pet.provider_selector.findData('openai'))
        self.pet.open_api_billing()
        bridge = self.pet.browser_bridge
        try:
            connection = http.client.HTTPConnection('127.0.0.1', bridge.port, timeout=3)
            connection.request('POST', '/balance', json.dumps({'balance': '12.34', 'client_version': '1.4.0'}),
                               {'Authorization': 'Bearer ' + bridge.token, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 200)
            self.app.processEvents()
            self.assertEqual(self.pet.hero_value.text(), '$12.34')
            self.assertIn('$12.34', self.pet.browser_dialog.status.text())
        finally:
            bridge.close()
            self.app.aboutToQuit.disconnect(bridge.close)

    def test_saved_pairing_restores_receiver_without_opening_dialog(self):
        import preferences
        from browser_balance import BalanceBridge
        original = BalanceBridge(lambda _: None, state_path=preferences.CONFIG.parent / 'browser-pairing.json')
        code = original.pairing_code
        original.close()
        self.pet.restore_browser_bridge()
        restored = self.pet.browser_bridge
        try:
            self.assertIsNotNone(restored)
            self.assertEqual(restored.pairing_code, code)
            self.assertIsNone(self.pet.browser_dialog)
            self.assertFalse(restored.monitoring_policy['monitoring_allowed'])
        finally:
            restored.close()
            self.app.aboutToQuit.disconnect(restored.close)

    def test_refresh_preserves_fresh_value_but_expiry_and_login_failure_explain_blank(self):
        import time
        from providers import api_mode_info
        data = deepcopy(DATA)
        data['codex']['data'] = api_mode_info({})
        self.pet.updated(data)
        self.pet.provider_selector.setCurrentIndex(self.pet.provider_selector.findData('openai'))
        checked = int(time.time())
        self.pet.apply_web_balance({'balance': '12.34', 'checked_at': checked, 'source': 'chrome_web'})
        self.pet.apply_web_balance({'state': 'refreshing'})
        self.assertEqual(self.pet.hero_value.text(), '$12.34')
        self.assertEqual(self.pet.web_balance['checked_at'], checked)
        self.assertIn('正在更新余额', self.pet.mini_note.text())
        self.pet.web_balance['checked_at'] = checked - 151
        self.pet.update_timing()
        self.assertEqual(self.pet.hero_value.text(), '—')
        self.assertIn('余额更新中断', self.pet.mini_note.text())
        self.pet.apply_web_balance({'balance': '12.34', 'checked_at': checked, 'source': 'chrome_web'})
        self.pet.apply_web_balance({'state': 'login_required'})
        self.assertEqual(self.pet.hero_value.text(), '—')
        self.assertIn('网页需要登录', self.pet.mini_note.text())

    def test_web_balance_is_organization_money_and_stale_data_is_hidden(self):
        from providers import api_mode_info
        import time
        data = deepcopy(DATA)
        data['codex']['data'] = api_mode_info({})
        self.pet.updated(data)
        self.pet.apply_web_balance({'balance': '12.34', 'currency': 'USD', 'checked_at': int(time.time())})
        self.assertEqual(self.pet.hero_value.text(), '$12.34')
        self.assertEqual(self.pet.hero_caption.text(), '组织余额')
        self.assertIsNone(self.pet.scene.remaining)
        self.assertIn('网页更新', self.pet.mini_note.text())
        self.pet.apply_web_balance({})
        self.assertEqual(self.pet.hero_value.text(), '—')
        self.pet.apply_web_balance({'balance': '12.34', 'checked_at': int(time.time()) - 3600})
        self.assertEqual(self.pet.hero_value.text(), '—')
        self.pet.updated(DATA)
        self.assertEqual(self.pet.hero_value.text(), '42%')

    def test_expand_collapse_and_settings(self):
        small_height = self.pet.height()
        QTest.mouseClick(self.pet.toggle_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(self.pet.scroll.isVisible())
        self.assertEqual(self.pet.width(), 320)
        self.pet.show_settings()
        self.assertTrue(self.pet.settings_dialog.isVisible())
        self.assertFalse(self.pet.settings_dialog.isModal())
        self.pet.settings_dialog.hide()
        self.pet.toggle()
        self.app.processEvents()
        self.assertEqual(self.pet.width(), 240)
        self.assertLessEqual(self.pet.height(), small_height)
        self.assertFalse(self.pet.scroll.isVisible())

    def test_scrolling_over_closed_provider_selector_does_not_switch_source(self):
        selector = self.pet.provider_selector
        before = selector.currentIndex()
        event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        self.app.sendEvent(selector, event)
        self.assertEqual(selector.currentIndex(), before)

    def test_avatar_click_does_not_toggle_and_doubleclick_does(self):
        QTest.mouseClick(self.pet.scene, Qt.MouseButton.LeftButton)
        self.assertFalse(self.pet.expanded)
        QTest.mouseDClick(self.pet.scene, Qt.MouseButton.LeftButton)
        self.assertTrue(self.pet.expanded)

    def test_drag_requests_native_move_and_wayland_has_no_manual_fallback(self):
        window = Mock()
        window.frameGeometry.return_value.topLeft.return_value = QPointF(0, 0).toPoint()
        window.windowHandle.return_value.startSystemMove.return_value = True
        drag = WindowDrag(self.pet)
        drag.window = window
        def event(kind, pos):
            return QMouseEvent(kind, QPointF(pos, pos), QPointF(pos, pos), Qt.MouseButton.LeftButton,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        drag.eventFilter(self.pet.scene, event(QEvent.Type.MouseButtonPress, 10))
        window.windowHandle.return_value.startSystemMove.assert_called_once()
        drag.eventFilter(self.pet.scene, event(QEvent.Type.MouseMove, 30))
        window.move.assert_not_called()
        window.windowHandle.return_value.startSystemMove.return_value = False
        drag.eventFilter(self.pet.scene, event(QEvent.Type.MouseButtonPress, 10))
        with patch('widgets.QApplication.platformName', return_value='wayland'):
            drag.eventFilter(self.pet.scene, event(QEvent.Type.MouseMove, 30))
        window.move.assert_not_called()
        with patch('widgets.QApplication.platformName', return_value='xcb'):
            drag.eventFilter(self.pet.scene, event(QEvent.Type.MouseMove, 30))
        window.move.assert_called_once()

if __name__ == '__main__':
    unittest.main()
