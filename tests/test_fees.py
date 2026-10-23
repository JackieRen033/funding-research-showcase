import unittest
from funding_collector.fees import fee_legs,validate_rules

class FeeTests(unittest.TestCase):
    def setUp(self):
        self.rule=dict(id='example',exchange='binance',symbol='BTC/USDC',market='perp',phase='both',
                       liquidity='taker',bps=4,source='official + account confirmation',
                       valid_from_ms=100,valid_until_ms=200,enabled=True,eligibility_confirmed=True)
        self.cfg={'fees_bps':{'binance':{'spot':10,'perp':5}},'fee_rules':[self.rule]}
    def test_quote_and_market_do_not_leak(self):
        usdt=fee_legs(self.cfg,'binance','BTC/USDT',150,160)
        usdc=fee_legs(self.cfg,'binance','BTC/USDC',150,160)
        self.assertEqual(usdt['perp_entry']['bps'],5)
        self.assertEqual(usdc['perp_entry']['bps'],4)
        self.assertEqual(usdc['spot_entry']['bps'],10)
    def test_exit_after_expiry_returns_to_baseline(self):
        f=fee_legs(self.cfg,'binance','BTC/USDC',150,200)
        self.assertEqual(f['perp_entry']['bps'],4)
        self.assertEqual(f['perp_exit']['bps'],5)
    def test_unknown_eligibility_and_maker_not_used_as_taker(self):
        self.rule['eligibility_confirmed']=False
        self.assertEqual(fee_legs(self.cfg,'binance','BTC/USDC',150,160)['perp_entry']['bps'],5)
        self.rule.update(eligibility_confirmed=True,liquidity='maker',bps=0)
        self.assertEqual(fee_legs(self.cfg,'binance','BTC/USDC',150,160)['perp_entry']['bps'],5)
    def test_no_stacked_conflicting_discounts(self):
        self.cfg['fee_rules'].append({**self.rule,'id':'duplicate'})
        with self.assertRaises(ValueError):fee_legs(self.cfg,'binance','BTC/USDC',150,160)
    def test_bad_configuration_fails(self):
        validate_rules([self.rule])
        for value in (float('nan'),True,101):
            with self.assertRaises(ValueError):validate_rules([{**self.rule,'bps':value}])
        with self.assertRaises(ValueError):validate_rules([{**self.rule,'valid_until_ms':100}])
