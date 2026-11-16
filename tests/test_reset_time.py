import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import reset_text


class QuotaTiming(unittest.TestCase):
    def test_today_tomorrow_and_complete_tooltip(self):
        now = datetime(2026, 11, 16, 14, 0)
        target = datetime(2026, 11, 17, 21, 22)
        text, tooltip = reset_text(target.timestamp(), now)
        self.assertEqual(text, '明日 21:22 重置')
        self.assertIn('2026-11-17 21:22', tooltip)
        self.assertIn('1 天 7 小时 22 分钟', tooltip)
        self.assertEqual(reset_text((now + timedelta(hours=2)).timestamp(), now)[0], '今天 16:00 重置')

    def test_expired_time_does_not_claim_quota_is_restored(self):
        now = datetime(2026, 11, 16, 14, 0)
        text, tooltip = reset_text((now - timedelta(seconds=1)).timestamp(), now)
        self.assertEqual(text, '等待额度更新')
        self.assertIn('等待下一次查询确认', tooltip)

    def test_missing_time_is_explicit(self):
        for value in (None, True, 'unknown'):
            self.assertEqual(reset_text(value)[0], '重置时间未提供')
