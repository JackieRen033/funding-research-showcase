import threading
import time
import unittest
from unittest.mock import patch
from funding_collector.scanner import ranked, scan
from funding_collector.schema import now_ms
from funding_collector.adapters import Adapter
from funding_collector.demo import DemoHTTP
from funding_collector.dashboard import Dashboard
import test_dashboard

class RankingTests(unittest.TestCase):
    def row(self, asset, rate, hours=8, **changes):
        return dict(exchange='gate',asset=asset,rate=rate,interval_seconds=hours*3600,
                    observed_ms=1000000,next_funding_time_ms=2000000,**changes)

    def test_strict_threshold_positive_only_and_sort(self):
        rows=[self.row('A',.001),self.row('B',.002),self.row('C',.0005),self.row('D',-.1)]
        self.assertEqual([r['asset'] for r in ranked(rows,.05,now=1000000)],['B','A'])

    def test_hour_basis_changes_ranking(self):
        rows=[self.row('A',.004,8),self.row('B',.001,1)]
        self.assertEqual([r['asset'] for r in ranked(rows,.01,'hour',1000000)],['B','A'])
        self.assertEqual([r['asset'] for r in ranked(rows,.01,'event',1000000)],['A','B'])

    def test_stale_source_expired_target_and_unknown_interval(self):
        fresh=self.row('A',.1)
        rows=[dict(fresh,observed_ms=1),dict(fresh,source_ms=1),dict(fresh,next_funding_time_ms=999999),dict(fresh,source_ms=2000000)]
        self.assertEqual(ranked(rows,0,now=1000000),[])
        self.assertEqual(ranked([dict(fresh,interval_seconds=None)],0,'hour',1000000),[])
        self.assertEqual(len(ranked([dict(fresh,interval_seconds=None)],0,'event',1000000)),1)

    def test_binance_does_not_invent_interval_or_prediction(self):
        class HTTP:
            def get(self,url,params=None):
                return {'error':None,'data':[] if url.endswith('fundingInfo') else [
                    {'symbol':'SUIUSDT','lastFundingRate':'.001','nextFundingTime':2000000,'time':1000000}]}
        rows,errors=scan('binance',['SUI'],HTTP(),threading.Event())
        self.assertFalse(errors)
        self.assertIsNone(rows[0]['interval_seconds'])
        self.assertEqual(rows[0]['semantics'],'latest_unconfirmed')

    def test_okx_failure_keeps_other_market_and_stop(self):
        class HTTP:
            def get(self,url,params=None):
                if params['instId'].startswith('BAD'):return {'error':'HTTP_500'}
                return {'error':None,'data':{'data':[{'fundingRate':'.001','fundingTime':'2000000','nextFundingTime':'5600000'}]}}
        rows,errors=scan('okx',['BAD','SUI'],HTTP(),threading.Event())
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['interval_seconds'],3600)
        self.assertEqual(len(errors),1)
        stop=threading.Event();stop.set()
        self.assertEqual(scan('okx',['SUI'],HTTP(),stop),([],[]))

class AutoTests(unittest.TestCase):
    setUp=test_dashboard.DashboardTests.setUp
    tearDown=test_dashboard.DashboardTests.tearDown

    def test_auto_ranks_caps_and_saves(self):
        self.app.demo=False
        self.app.cfg['exchanges']=['gate']
        def fake_scan(*args):
            return [dict(exchange='gate',asset=a,symbol=a+'/USDT',rate=r,interval_seconds=3600,
                         next_funding_time_ms=now_ms()+3600000,observed_ms=now_ms(),raw={})
                    for a,r in [('SUI',.001),('ARB',.002)]],[]
        real_adapter=Adapter('gate',DemoHTTP('gate'))
        with patch('funding_collector.dashboard.discover',return_value=['SUI','ARB']),patch('funding_collector.dashboard.scan',side_effect=fake_scan),patch('funding_collector.dashboard.Adapter',return_value=real_adapter):
            self.app.start_auto({'threshold_pct':.05,'limit':1,'basis':'event'})
            deadline=time.monotonic()+3
            while not self.app.cycles and time.monotonic()<deadline: time.sleep(.01)
            self.app.close()
        self.assertIsNone(self.app.error)
        self.assertEqual(self.app.total,1)
        self.assertEqual(self.app.auto_pairs,[('gate','ARB')])
        self.assertEqual([r['asset'] for r in self.app.state()['scanner']['rows']],['ARB','SUI'])

    def test_invalid_auto_options(self):
        self.app.demo=False
        for options in ({'threshold_pct':float('nan')},{'threshold_pct':-1},{'limit':11},{'basis':'annual'}):
            with self.assertRaises(ValueError):self.app.start_auto(options)
        self.assertFalse(self.app.running)
