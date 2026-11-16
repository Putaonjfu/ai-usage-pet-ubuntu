"""Exercise the real loopback boundary without accessing any account or website."""
import http.client
import json
import sys
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


if __name__ == '__main__':
    unittest.main()
