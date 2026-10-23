"""Scheduling regression tests use synthetic responses, never exchange traffic."""
import threading
import time
import unittest
from unittest.mock import patch
from funding_collector.adapters import Adapter
from funding_collector.demo import DemoHTTP
from funding_collector.schema import now_ms
from funding_collector.transport import PublicHTTP
import test_dashboard


class SchedulingTests(unittest.TestCase):
    setUp = test_dashboard.DashboardTests.setUp
    tearDown = test_dashboard.DashboardTests.tearDown

    def test_slow_exchange_does_not_block_candidates_or_refresh(self):
        self.app.demo = False
        self.app.cfg['exchanges'] = ['gate', 'okx']
        self.app.cfg['event_poll_seconds'] = .05
        slow_started = threading.Event()
        def fake_scan(exchange, assets, http, stop):
            if exchange == 'okx':
                slow_started.set()
                stop.wait(3)
                return [], []
            return [dict(exchange='gate',asset='BTC',symbol='BTC/USDT',rate=.01,
                interval_seconds=3600,next_funding_time_ms=now_ms()+3600000,
                observed_ms=now_ms(),raw={})], []
        adapter = Adapter('gate', DemoHTTP('gate'))
        with patch('funding_collector.dashboard.discover',return_value=['BTC']), patch('funding_collector.dashboard.scan',side_effect=fake_scan), patch('funding_collector.dashboard.Adapter',return_value=adapter):
            try:
                self.app.start_auto({'basis':'event','limit':1})
                self.assertTrue(slow_started.wait(1))
                deadline = time.monotonic()+2
                while self.app.cycles < 2 and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertGreaterEqual(self.app.cycles, 2)
                self.assertGreaterEqual(self.app.total, 2)
                self.assertEqual(self.app.scan_status['okx'], 'scanning')
                self.assertEqual(self.app.auto_pairs, [('gate','BTC')])
            finally:
                self.app.close()
        self.assertFalse(self.app.running)
        self.assertIsNone(self.app.error)

    def test_failed_venue_does_not_block_successful_venue(self):
        self.app.demo = False
        self.app.cfg['exchanges'] = ['gate','okx']
        def discover(exchange, http):
            if exchange == 'okx': raise ValueError('NETWORK simulated')
            return ['BTC']
        row=dict(exchange='gate',asset='BTC',symbol='BTC/USDT',rate=.01,
                 interval_seconds=3600,next_funding_time_ms=now_ms()+3600000,
                 observed_ms=now_ms(),raw={})
        with patch('funding_collector.dashboard.discover',side_effect=discover), patch('funding_collector.dashboard.scan',return_value=([row],[])), patch('funding_collector.dashboard.Adapter',return_value=Adapter('gate',DemoHTTP('gate'))):
            try:
                self.app.start_auto({'basis':'event','limit':1})
                deadline=time.monotonic()+2
                while (not self.app.cycles or 'okx' not in self.app.scan_errors) and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertGreater(self.app.total,0)
                self.assertIn('NETWORK',self.app.scan_errors['okx'])
                self.assertIsNone(self.app.error)
            finally: self.app.close()


class TransportSharingTests(unittest.TestCase):
    def test_shared_client_serializes_requests(self):
        client=PublicHTTP()
        entered=threading.Event(); release=threading.Event(); second=threading.Event()
        def get(url, params=None):
            if url=='first':
                entered.set(); release.wait(2)
            else: second.set()
        with patch.object(client,'_get',side_effect=get):
            a=threading.Thread(target=client.get,args=('first',)); b=threading.Thread(target=client.get,args=('second',))
            a.start(); self.assertTrue(entered.wait(1)); b.start()
            try: self.assertFalse(second.wait(.05))
            finally: release.set(); a.join(); b.join()
            self.assertTrue(second.is_set())
