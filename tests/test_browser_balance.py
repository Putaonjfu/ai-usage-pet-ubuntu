"""Exercise the real loopback boundary without accessing any account or website."""
import http.client
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_balance import BalanceBridge


class BrowserBridge(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.bridge = BalanceBridge(self.received.append, 3)

    def tearDown(self):
        self.bridge.close()

    def post(self, value, **overrides):
        connection = http.client.HTTPConnection('127.0.0.1', self.bridge.port, timeout=3)
        body = json.dumps(value)
        headers = {'Authorization': 'Bearer ' + self.bridge.token, 'Content-Type': 'application/json',
                   'Origin': 'chrome-extension://' + 'a' * 32}
        headers.update(overrides)
        connection.request('POST', '/balance', body, headers)
        response = connection.getresponse()
        status, data = response.status, json.loads(response.read())
        connection.close()
        return status, data

    def test_accepted_money_has_receiver_timestamp_and_failed_read_clears_it(self):
        status, data = self.post({'balance': '12.34'})
        self.assertEqual(status, 200)
        self.assertEqual(data['refresh_minutes'], 3)
        self.assertEqual(self.received[0]['balance'], '12.34')
        self.assertEqual(self.received[0]['source'], 'chrome_web')
        self.assertIsInstance(self.received[0]['checked_at'], int)
        self.assertEqual(self.post({'balance': None})[0], 200)
        self.assertEqual(self.received[-1], {})
        self.assertEqual(self.bridge.server.server_address[0], '127.0.0.1')

    def test_wrong_token_web_origin_and_rebound_host_cannot_change_balance(self):
        self.assertEqual(self.post({'balance': '12.34'}, Authorization='Bearer wrong')[0], 401)
        self.assertEqual(self.post({'balance': '12.34'}, Origin='https://platform.openai.com')[0], 403)
        self.assertEqual(self.post({'balance': '12.34'}, Host='evil.invalid')[0], 403)
        self.assertEqual(self.received, [])

    def test_reject_unbounded_non_money_and_extra_data(self):
        for body in [{'balance': 12.34}, {'balance': 'NaN'}, {'balance': 'Infinity'},
                     {'balance': '1e9'}, {'balance': '1' * 300},
                     {'balance': '12.34', 'cookies': 'must not be accepted'}, [], None]:
            self.assertEqual(self.post(body)[0], 400)
        self.assertEqual(self.received, [])

    def test_persistent_pairing_survives_receiver_restart_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app' / 'browser-pairing.json'
            receiver = BalanceBridge(self.received.append, state_path=path)
            code = receiver.pairing_code
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            receiver.close()
            restored = BalanceBridge(self.received.append, state_path=path)
            try:
                self.assertEqual(restored.pairing_code, code)
            finally:
                restored.close()
            os.chmod(path, 0o644)
            with self.assertRaises(OSError):
                BalanceBridge(self.received.append, state_path=path)

    def test_page_timestamp_is_preserved_and_stale_sample_is_rejected(self):
        sampled = int(time.time()) - 90
        self.assertEqual(self.post({'balance': '12.34', 'sampled_at': sampled})[0], 200)
        self.assertEqual(self.received[-1]['checked_at'], sampled)
        self.assertEqual(self.post({'balance': '12.34', 'sampled_at': sampled - 1000})[0], 400)
        self.assertEqual(self.post({'balance': '12.34', 'sampled_at': True})[0], 400)
        self.assertEqual(len(self.received), 1)

    def test_authenticated_lifecycle_states_carry_no_money_or_new_sample_time(self):
        self.assertEqual(self.post({'state': 'refreshing'})[0], 200)
        self.assertEqual(self.received[-1]['state'], 'refreshing')
        self.assertNotIn('balance', self.received[-1])
        self.assertNotIn('checked_at', self.received[-1])
        self.assertEqual(self.post({'state': 'login_required'})[0], 200)
        self.assertEqual(self.post({'state': 'arbitrary page text'})[0], 400)
        self.assertEqual(self.post({'state': 'refreshing', 'cookies': 'no'})[0], 400)

    def test_diagnostics_prove_receipts_without_exposing_balance_or_pairing(self):
        def get_status(authorization):
            connection = http.client.HTTPConnection('127.0.0.1', self.bridge.port, timeout=3)
            connection.request('GET', '/status', headers={'Authorization': authorization})
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        self.assertEqual(get_status('Bearer wrong')[0], 401)
        self.assertEqual(self.post({'balance': '12.34', 'client_version': '1.3.0'})[0], 200)
        status, diagnostic = get_status('Bearer ' + self.bridge.token)
        self.assertEqual(status, 200)
        self.assertEqual(diagnostic['client_version'], '1.3.0')
        self.assertEqual(diagnostic['state'], 'connected')
        self.assertGreater(diagnostic['last_balance_at'], 0)
        self.assertNotIn('balance', diagnostic)
        self.assertNotIn(self.bridge.token, json.dumps(diagnostic))
        self.assertEqual(self.post({'state': 'refreshing', 'client_version': '1.3.0'})[0], 200)
        self.assertEqual(self.post({'balance': '12.34', 'client_version': 'not a version'})[0], 400)


if __name__ == '__main__':
    unittest.main()
