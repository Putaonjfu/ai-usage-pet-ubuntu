import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from providers import fetch_codex, CodexRPC

SIMULATED_CODEX = r'''#!/usr/bin/env python3
import sys,json
assert sys.argv[1] == 'app-server'
for line in sys.stdin:
    q=json.loads(line)
    method=q['method']
    if 'id' not in q: continue
    assert 'params' in q, 'app-server requires params on every request'
    if method == 'initialize':
        result={'userAgent':'test'}
    elif method == 'account/read':
        result={'account':{'type':'chatgpt','email':'example@test.invalid','planType':'pro'}}
    elif method == 'account/rateLimits/read':
        result={'rateLimitsByLimitId':{'codex':{
            'limitId':'codex', 'primary':{'usedPercent':40,'windowDurationMins':300,'resetsAt':1900000000},
            'secondary':{'usedPercent':60,'windowDurationMins':10080,'resetsAt':1900000010}}},
            'rateLimitResetCredits':{'availableCount':2, 'credits':None}}
    elif method == 'account/usage/read':
        result={'summary':{'lifetimeTokens':12345}, 'dailyUsageBuckets':[]}
    else:
        sys.stdout.write(json.dumps({'id':q['id'],'error':{'message':'Unsupported'}})+'\n')
        sys.stdout.flush()
        continue
    sys.stdout.write(json.dumps({'id':q['id'],'result':result})+'\n')
    sys.stdout.flush()
'''

class TestProtocol(unittest.TestCase):
    def test_read_only_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'codex'
            fake.write_text(SIMULATED_CODEX)
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            with patch('providers.locate_codex', return_value=str(fake)):
                data = fetch_codex()
        self.assertEqual(data['plan'],'pro')
        self.assertEqual(data['resets']['available'],2)
        self.assertIsNone(data['resets']['details'])
        self.assertEqual(data['buckets'][0]['windows'][1]['remaining'],40)
        self.assertEqual(data['token_summary']['lifetimeTokens'],12345)

class TestAPILogin(unittest.TestCase):
    def run_server(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'codex'
            fake.write_text(source)
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            with patch('providers.locate_codex', return_value=str(fake)):
                return fetch_codex()

    def test_api_key_does_not_call_plan_quota_or_usage(self):
        source = SIMULATED_CODEX.replace(
            "result={'account':{'type':'chatgpt','email':'example@test.invalid','planType':'pro'}}",
            "result={'account':{'type':'apiKey'},'requiresOpenaiAuth':True}")
        source = source.replace("    assert 'params' in q", "    assert method not in ('account/rateLimits/read','account/usage/read'), 'API mode must not read plan quotas'\n    assert 'params' in q")
        result = self.run_server(source)
        self.assertEqual(result['mode'], 'api')
        self.assertEqual(result['api']['provider'], 'OpenAI')
        self.assertIsNone(result['api']['balance'])
        self.assertEqual(result['buckets'], [])
        self.assertIsNone(result['resets'])

    def test_active_external_provider_overrides_stored_chatgpt_account(self):
        source = SIMULATED_CODEX.replace(
            "    elif method == 'account/rateLimits/read':",
            "    elif method == 'config/read':\n        result={'config':{'model_provider':'gateway','model_providers':{'gateway':{'base_url':'https://example.invalid/v1','requires_openai_auth':False}}}}\n    elif method == 'account/rateLimits/read':")
        result = self.run_server(source)
        self.assertEqual(result['mode'], 'api')
        self.assertEqual(result['api']['host'], 'example.invalid')
        self.assertIsNone(result['api']['billing_url'])


if __name__ == '__main__':
    unittest.main()
