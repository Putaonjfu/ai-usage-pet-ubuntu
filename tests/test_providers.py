import unittest
from unittest.mock import patch, MagicMock
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from providers import parse_codex_rate_limits, fetch_deepseek, ProviderError

class Parsers(unittest.TestCase):
    def test_multiple_bucket_and_credit_count_is_authoritative(self):
        raw = {"rateLimitsByLimitId": {
            "codex": {"limitId": "codex", "primary": {"usedPercent": 72, "windowDurationMins": 300, "resetsAt": 1800000000}, "secondary": {"usedPercent": 10, "windowDurationMins": 10080, "resetsAt": 1800000001}},
            "codex_other": {"limitId": "codex_other", "primary": {"usedPercent": 99, "windowDurationMins": 60, "resetsAt": 1800000002}}
        }, "rateLimitResetCredits": {"availableCount": 3, "credits": [{"title": "A", "expiresAt": 1800000010}]}}
        out = parse_codex_rate_limits(raw)
        self.assertEqual(len(out["buckets"]), 2)
        self.assertEqual(out["buckets"][0]["windows"][0]["remaining"], 28.0)
        self.assertEqual(out["resets"]["available"], 3)
        self.assertEqual(len(out["resets"]["details"]), 1)

    def test_missing_resets_not_zero(self):
        self.assertIsNone(parse_codex_rate_limits({"rateLimits": {"limitId": "codex"}})["resets"])

    def test_empty_credit_details_does_not_erase_count(self):
        info = parse_codex_rate_limits({"rateLimitResetCredits": {"availableCount": 2, "credits": []}})
        self.assertEqual(info["resets"]["available"], 2)

    def test_clamp_usage(self):
        info = parse_codex_rate_limits({"rateLimits": {"primary": {"usedPercent": 120}}})
        self.assertEqual(info["buckets"][0]["windows"][0]["remaining"], 0)

    def test_deepseek_no_key(self):
        with patch("providers.get_deepseek_key", return_value=None):
            with self.assertRaisesRegex(ProviderError, "未设置"):
                fetch_deepseek()

    def test_deepseek_balance(self):
        class Res:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"is_available":true,"balance_infos":[{"currency":"CNY","total_balance":"12.50","granted_balance":"2.50","topped_up_balance":"10.00"}]}'
        with patch("providers.get_deepseek_key", return_value="sk-example"):
            with patch("providers.urlopen", return_value=Res()) as u:
                result = fetch_deepseek()
        self.assertEqual(result["balances"][0]["total"], "12.50")
        self.assertEqual(u.call_args.args[0].full_url, "https://api.deepseek.com/user/balance")


class DesktopProxy(unittest.TestCase):
    def test_existing_manual_proxy_is_used_without_changing_environment(self):
        import os
        import subprocess
        from providers import codex_environment
        values = {('org.gnome.system.proxy', 'mode'): "'manual'",
                  ('org.gnome.system.proxy.http', 'host'): "'127.0.0.1'",
                  ('org.gnome.system.proxy.http', 'port'): '7897',
                  ('org.gnome.system.proxy.https', 'host'): "'127.0.0.1'",
                  ('org.gnome.system.proxy.https', 'port'): '7897'}
        def setting(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout=values[tuple(args[-2:])])
        with patch.dict(os.environ, {}, clear=True), patch('providers.shutil.which', return_value='/usr/bin/gsettings'), patch('providers.subprocess.run', side_effect=setting):
            result = codex_environment()
            self.assertEqual(result['HTTPS_PROXY'], 'http://127.0.0.1:7897')
            self.assertNotIn('HTTPS_PROXY', os.environ)

    def test_explicit_proxy_takes_precedence(self):
        import os
        from providers import codex_environment
        with patch.dict(os.environ, {'ALL_PROXY': 'socks5://localhost:1234'}, clear=True), patch('providers.subprocess.run') as read:
            self.assertEqual(codex_environment()['ALL_PROXY'], 'socks5://localhost:1234')
            read.assert_not_called()

if __name__ == "__main__":
    unittest.main()
