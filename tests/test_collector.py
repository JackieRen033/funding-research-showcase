import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from funding_collector.adapters import Adapter, book
from funding_collector.schema import FundingSnapshot, FundingEvent, State, number, now_ms
from funding_collector.demo import DemoHTTP
from funding_collector.config import load
from funding_collector.storage import Store
from funding_collector.decision import evaluate, vwap
from funding_collector.presentation import Presenter
from funding_collector.transport import PublicHTTP

ROOT = Path(__file__).resolve().parents[1]


class MappingTests(unittest.TestCase):
    def test_all_adapters_units_and_realized(self):
        for ex in ('binance', 'okx', 'bybit', 'gate'):
            with self.subTest(exchange=ex):
                s = Adapter(ex, DemoHTTP(ex)).collect('BTC')
                self.assertEqual(s.issues, [])
                self.assertEqual(s.interval_seconds, 3600)
                self.assertAlmostEqual(s.open_interest_base, 123)
                self.assertEqual(s.perp_book.bids[0], [99999, 10])
                self.assertEqual(s.spot_book.bids[0], [99999, 10])
                self.assertEqual(s.realized_rate, .002)
                self.assertEqual(s.spot, 100000)
                if ex != 'binance':
                    self.assertEqual(s.predicted_rate_time_ms, s.next_funding_time_ms)
                    self.assertEqual(s.predicted_rate, .006)
                else:
                    self.assertIsNone(s.predicted_rate)
                    self.assertIn('historical', s.interval_source)

    def test_okx_target_is_funding_time_not_following_time(self):
        http = DemoHTTP('okx')
        s = Adapter('okx', http).collect('BTC')
        self.assertEqual(s.next_funding_time_ms, http.now + 300000)
        self.assertEqual(s.predicted_rate_time_ms, s.next_funding_time_ms)
        self.assertEqual(s.settlement_rate, .002)
        self.assertNotEqual(s.current_rate, s.realized_rate)

    def test_dynamic_interval_and_empty_forecast(self):
        class Changed(DemoHTTP):
            def get(self, url, params=None):
                e = super().get(url, params)
                if url.endswith('/tickers'):
                    e['data']['result']['list'][0]['fundingIntervalHour'] = '4'
                    e['data']['result']['list'][0]['fundingRate'] = ''
                return e
        s = Adapter('bybit', Changed('bybit')).collect('BTC')
        self.assertEqual(s.interval_seconds, 14400)
        self.assertIsNone(s.predicted_rate)
        self.assertIn('MISSING: funding', s.issues)

    def test_binance_explicit_interval_wins(self):
        class Changed(DemoHTTP):
            def get(self, url, params=None):
                e = super().get(url, params)
                if url.endswith('fundingInfo'):
                    e['data'] = [{'symbol': 'BTCUSDT', 'fundingIntervalHours': 2}]
                return e
        s = Adapter('binance', Changed('binance')).collect('BTC')
        self.assertEqual(s.interval_seconds, 7200)
        self.assertEqual(s.interval_source, 'fundingInfo.fundingIntervalHours')

    def test_binance_missing_interval_not_defaulted(self):
        class Changed(DemoHTTP):
            def get(self, url, params=None):
                e = super().get(url, params)
                if url.endswith('fundingRate'):
                    e['data'] = []
                return e
        s = Adapter('binance', Changed('binance')).collect('BTC')
        self.assertIsNone(s.interval_seconds)

    def test_failure_does_not_reuse_old_values(self):
        class Failed(DemoHTTP):
            def get(self, url, params=None):
                e = super().get(url, params)
                e.update(error='HTTP_503', data=None)
                return e
        s = Adapter('gate', Failed('gate')).collect('BTC')
        self.assertEqual(s.connection, 'DISCONNECTED')
        self.assertIsNone(s.current_rate)
        self.assertTrue(all(e['error'] for e in s.raw.values()))

    def test_invalid_book_and_nan(self):
        with self.assertRaises(ValueError):
            book({'bids': [['10', '1']], 'asks': [['9', '1']]})
        with self.assertRaises(ValueError):
            number('NaN')
        with self.assertRaises(ValueError):
            book({}, None)


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load(ROOT / 'config.example.json')['screen']
        self.s = Adapter('bybit', DemoHTTP('bybit')).collect('BTC')

    def test_ready_and_cost_accounting(self):
        d = evaluate(self.s, self.cfg, self.s.collected_at_ms)
        self.assertEqual(d.state, State.READY)
        self.assertAlmostEqual(d.estimated_pnl, 4.72, places=5)
        self.assertAlmostEqual(d.net_bps, 23.6, places=5)

    def test_reject_negative_and_costs(self):
        for rate in (-.001, .0001):
            self.s.predicted_rate = rate
            self.assertEqual(evaluate(self.s, self.cfg, self.s.collected_at_ms).state, State.REJECT)

    def test_unknown_forecast_and_wrong_event_wait(self):
        self.s.predicted_rate_time_ms += 3600000
        self.assertEqual(evaluate(self.s, self.cfg, self.s.collected_at_ms).state, State.WAIT)

    def test_depth_not_top_of_book_only(self):
        self.s.spot_book.asks = [[100001, .001]]
        self.assertEqual(evaluate(self.s, self.cfg, self.s.collected_at_ms).state, State.REJECT)
        self.assertAlmostEqual(vwap([[10, 1], [20, 2]], 2), 15)

    def test_stale_and_latency_emergency(self):
        self.assertEqual(evaluate(self.s, self.cfg, self.s.collected_at_ms + 60000).state, State.EMERGENCY)
        self.s.raw['funding']['latency_ms'] = 9000
        self.assertEqual(evaluate(self.s, self.cfg, self.s.collected_at_ms).state, State.EMERGENCY)

    def test_guard_and_outside_window(self):
        for delta in (10000, 10000000):
            self.s.next_funding_time_ms = self.s.predicted_rate_time_ms = self.s.collected_at_ms + delta
            self.assertEqual(evaluate(self.s, self.cfg, self.s.collected_at_ms).state, State.WAIT)

    def test_no_position_states_fabricated_and_output_dedup(self):
        d = evaluate(self.s, self.cfg, self.s.collected_at_ms)
        p = Presenter()
        self.assertIn('READY', p.render([self.s], [d]))
        self.assertEqual(p.render([self.s], [d]), '')
        self.assertEqual(set(State), {State.WAIT, State.READY, State.HOLD, State.EXIT, State.REJECT, State.EMERGENCY})


class StorageTests(unittest.TestCase):
    def test_restart_rollover_and_no_lookahead(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'db.sqlite')
            store = Store(path)
            pre = FundingSnapshot('okx', 'BTC/USDT', 'BTC-USDT-SWAP', collected_at_ms=90000,
                                  next_funding_time_ms=100000, predicted_rate=.01)
            store.save(pre, 60, 60)
            event_id = 'okx:BTC/USDT:100000'
            store.close()
            store = Store(path)
            post = FundingSnapshot('okx', 'BTC/USDT', 'BTC-USDT-SWAP', collected_at_ms=110000,
                                   next_funding_time_ms=3700000)
            store.save(post, 60, 60)
            later = FundingSnapshot('okx', 'BTC/USDT', 'BTC-USDT-SWAP', collected_at_ms=120000,
                history=[FundingEvent('okx', 'BTC/USDT', 100000, .005, 120000, 'history', settlement_state='published')])
            store.save(later, 60, 60)
            rows = store.db.execute('SELECT phase,offset_ms FROM event_snapshots WHERE event_id=? ORDER BY offset_ms', (event_id,)).fetchall()
            self.assertEqual(rows, [('pre', -10000), ('post', 10000), ('post', 20000)])
            buf = io.StringIO()
            store.export(buf, event_id)
            data = [json.loads(x) for x in buf.getvalue().splitlines()]
            self.assertIsNone(data[0]['realized_rate'])
            self.assertEqual(data[0]['predicted_rate'], .01)
            observed = store.db.execute('SELECT observed_ms FROM event_observations WHERE event_id=? ORDER BY observed_ms', (event_id,)).fetchall()
            self.assertEqual(observed, [(90000,), (120000,)])
            store.close()


class TransportTests(unittest.TestCase):
    def test_rate_limit_cooldown_no_retry_storm(self):
        error = HTTPError('https://example.com', 429, 'limit', {'Retry-After': '120'}, io.BytesIO(b'{"message":"limited"}'))
        with patch('funding_collector.transport.urlopen', side_effect=error) as request:
            http = PublicHTTP(min_gap=0)
            a = http.get('https://example.com')
            b = http.get('https://example.com')
            self.assertEqual(a['error'], 'HTTP_429')
            self.assertEqual(b['error'], 'RATE_LIMIT_COOLDOWN')
            self.assertEqual(request.call_count, 1)

    def test_network_retry_bounded(self):
        with patch('funding_collector.transport.urlopen', side_effect=URLError('offline')) as request, patch('funding_collector.transport.time.sleep'):
            e = PublicHTTP(retries=1, min_gap=0).get('https://example.com')
            self.assertEqual(request.call_count, 2)
            self.assertEqual(len(e['attempts']), 2)
            self.assertIn('NETWORK', e['error'])

    def test_api_error_with_http_success(self):
        response = io.BytesIO(b'{"retCode":10006,"retMsg":"Too many visits"}')
        response.status = 200
        with patch('funding_collector.transport.urlopen', return_value=response):
            e = PublicHTTP(min_gap=0).get('https://example.com')
        self.assertIn('API_ERROR 10006', e['error'])


class ConfigTests(unittest.TestCase):
    def test_bad_config(self):
        cfg = load(ROOT / 'config.example.json')
        cfg['event_poll_seconds'] = 0
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'bad.json'
            p.write_text(json.dumps(cfg))
            with self.assertRaises(ValueError):
                load(p)


if __name__ == '__main__':
    unittest.main()
