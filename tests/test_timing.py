import copy
import unittest
from funding_collector.timing import timing, preliminary
from funding_collector.decision import evaluate
from funding_collector.adapters import Adapter
from funding_collector.demo import DemoHTTP
from funding_collector.schema import now_ms
import test_dashboard

class TimingTests(unittest.TestCase):
    def setUp(self):
        self.cfg={'notional_usdt':2000,'candidate_window_seconds':600,'settlement_guard_seconds':60,
                  'time_cost':{'capital_budget_usdt':4000,'annual_cost_pct':10,
                               'entry_execution_seconds':60,'exit_buffer_seconds':120}}

    def test_wait_is_not_occupied_capital(self):
        t=timing(7*3600*1000,0,self.cfg)
        self.assertEqual(t['entry_wait_seconds'],7*3600-600)
        self.assertEqual(t['occupied_seconds'],720)
        self.assertAlmostEqual(t['capital_time_cost_usdt'],4000*.1*720/(365*86400))

    def test_near_event_does_not_blow_up_score(self):
        self.assertFalse(timing(120000,0,self.cfg)['entry_feasible'])
        self.assertTrue(timing(121000,0,self.cfg)['entry_feasible'])
        self.assertEqual(timing(300000,0,self.cfg)['occupied_seconds'],420)

class ScenarioTests(unittest.TestCase):
    setUp=test_dashboard.DashboardTests.setUp
    tearDown=test_dashboard.DashboardTests.tearDown

    def snapshot(self,asset,rate):
        h=DemoHTTP('gate');s=Adapter('gate',h).collect(asset)
        s.predicted_rate=rate
        return s

    def test_time_cost_subtracted_once_not_rate_prorated(self):
        s=self.snapshot('BTC',.01)
        cfg=copy.deepcopy(self.app.cfg['screen']);now=now_ms()
        without=copy.deepcopy(cfg);without.pop('time_cost')
        old=evaluate(s,without,now);new=evaluate(s,cfg,now)
        self.assertEqual(old.evidence['funding_estimate_usdt'],new.evidence['funding_estimate_usdt'])
        self.assertAlmostEqual(old.estimated_pnl-new.estimated_pnl,new.evidence['capital_time_cost_usdt'])
        s.interval_seconds=3600
        self.assertEqual(evaluate(s,cfg,now).estimated_pnl,new.estimated_pnl)

    def test_net_ranking_and_exclusion(self):
        self.app.auto_options['basis']='net'
        self.app.auto_pairs=[('gate','BTC'),('gate','ETH'),('gate','SUI')]
        for a,r in [('BTC',.01),('ETH',.02),('SUI',.00001)]:
            self.app.latest['gate:'+a+'/USDT']=self.snapshot(a,r)
        rows=self.app.ranked_results(now_ms())
        self.assertEqual([r['asset'] for r in rows],['ETH','BTC'])
        self.app.latest['gate:ETH/USDT'].collected_at_ms=now_ms()-120000
        self.assertEqual([r['asset'] for r in self.app.ranked_results(now_ms())],['BTC'])
        s=self.app.latest['gate:BTC/USDT'];s.next_funding_time_ms=now_ms()+60000
        self.assertEqual(self.app.ranked_results(now_ms()),[])

    def test_lower_gross_rate_can_win_after_book_cost(self):
        a=self.snapshot('BTC',.02);b=self.snapshot('ETH',.015)
        # Charge the higher-rate market a wider spot spread, within sampled depth.
        a.spot_book.asks=[[p*1.01,q] for p,q in a.spot_book.asks]
        self.app.auto_pairs=[('gate','BTC'),('gate','ETH')]
        self.app.latest={'gate:BTC/USDT':a,'gate:ETH/USDT':b}
        rows=self.app.ranked_results(now_ms())
        self.assertEqual(rows[0]['asset'],'ETH')

    def test_unknown_forecast_and_depth_rejected(self):
        s=self.snapshot('BTC',.01)
        self.app.auto_pairs=[('gate','BTC')]
        self.app.latest['gate:BTC/USDT']=s
        s.predicted_rate=None
        self.assertEqual(self.app.ranked_results(now_ms()),[])
        s.predicted_rate=.01;s.spot_book.asks=[]
        self.assertEqual(self.app.ranked_results(now_ms()),[])

    def test_preselection_uses_costs_and_excludes_unconfirmed(self):
        cfg=self.app.cfg['screen'];now=now_ms()
        rows=[dict(exchange='gate',asset='BTC',rate=.01,next_funding_time_ms=now+600000),
              dict(exchange='gate',asset='ETH',rate=.02,next_funding_time_ms=now+60000),
              dict(exchange='binance',asset='SUI',rate=.1,next_funding_time_ms=now+600000,semantics='latest_unconfirmed')]
        self.assertEqual([r['asset'] for r in preliminary(rows,cfg,now)],['BTC'])
