import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web_balance import BILLING_URL, is_billing_url, parse_credit_balance


class WebCreditBalance(unittest.TestCase):
    def test_only_explicit_balance_is_read_not_spend_or_recharge(self):
        text = 'Usage this month\n$88.00\nCredit balance\n$12.34\nAuto recharge\n$20.00'
        self.assertEqual(parse_credit_balance(text), '12.34')
        self.assertIsNone(parse_credit_balance('Usage this month\n$88.00\nMonthly limit\n$100.00'))

    def test_same_line_zero_negative_and_grouped_money(self):
        for text, expected in [('Credit balance: $0.00', '0.00'),
                               ('API credit balance\nUS$12.34', '12.34'),
                               ('Credit balance\n−$0.50', '-0.50'),
                               ('Available credit balance\nUSD 1,234.56', '1234.56'),
                               ('可用余额：$10.25', '10.25')]:
            self.assertEqual(parse_credit_balance(text), expected)

    def test_api_trial_balance_does_not_use_next_expiring_grant_as_total(self):
        text = ('Billing\nFree trial\nAPI credit balance\nUS$12.34\n'
                '下一笔到期额度：US$5.00，到期日期为 2027年1月2日。\n'
                'Subscription benefit\nChatGPT subscription linked')
        self.assertEqual(parse_credit_balance(text), '12.34')
        self.assertIsNone(parse_credit_balance('下一笔到期额度：US$5.00，到期日期为 2027年1月2日。'))

    def test_ambiguous_missing_and_unrelated_values_are_rejected(self):
        self.assertIsNone(parse_credit_balance('Credit balance\n$12.34\nCredit balance\n$99.00'))
        self.assertIsNone(parse_credit_balance('Credit balance\nAdd credits\n$20.00'))
        self.assertIsNone(parse_credit_balance('Credit balance\n$NaN'))
        self.assertIsNone(parse_credit_balance('Credit balance\n$1,23.00'))
        self.assertIsNone(parse_credit_balance('Log in to continue'))

    def test_only_official_billing_page_can_be_read(self):
        self.assertTrue(is_billing_url(BILLING_URL))
        self.assertTrue(is_billing_url(BILLING_URL + '?organization=demo'))
        for url in ['http://platform.openai.com/settings/organization/billing/overview',
                    'https://platform.openai.com.evil.invalid/settings/organization/billing/overview',
                    'https://platform.openai.com/api-keys',
                    'https://auth.openai.com/log-in',
                    'https://user:pass@platform.openai.com/settings/organization/billing/overview']:
            self.assertFalse(is_billing_url(url))


if __name__ == '__main__':
    unittest.main()
