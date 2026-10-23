import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from funding_collector.dashboard import Dashboard, restore
from funding_collector.config import load
from funding_collector.schema import now_ms
from funding_collector.storage import Store
from funding_collector.adapters import Adapter
from funding_collector.demo import DemoHTTP

ROOT = Path(__file__).resolve().parents[1]


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = load(ROOT / 'config.example.json')
        self.cfg['database'] = str(Path(self.tmp.name) / 'ui.sqlite')
        self.app = Dashboard(self.cfg, demo=True)

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def test_empty_dashboard_does_not_invent_live_data(self):
        s = self.app.state()
        self.assertFalse(s['running'])
        self.assertEqual(s['total'], 0)
        self.assertTrue(all(m['decision'] is None for m in s['markets']))

    def test_start_stop_save_and_no_duplicate_worker(self):
        self.assertTrue(self.app.start({'exchanges': ['bybit'], 'assets': ['BTC']}))
        self.assertFalse(self.app.start())
        deadline = time.monotonic() + 5
        while self.app.state()['cycles'] == 0 and time.monotonic() < deadline:
            time.sleep(.02)
        self.app.close()
        s = self.app.state()
        self.assertFalse(s['running'])
        self.assertEqual(s['total'], 1)
        self.assertEqual(s['markets'][0]['decision']['state'], 'READY')
        reloaded = Dashboard(self.cfg, demo=True)
        self.assertEqual(reloaded.state()['total'], 1)
        self.assertEqual(reloaded.detail('bybit:BTC/USDT')['snapshot']['mode'], 'synthetic')

    def test_invalid_selection_rejected(self):
        for selection in ({'exchanges': [], 'assets': ['BTC']},
                          {'exchanges': ['unknown'], 'assets': ['BTC']},
                          {'exchanges': ['bybit'], 'assets': ['NO_SUCH_ASSET']}):
            with self.assertRaises(ValueError):
                self.app.start(selection)
        self.assertFalse(self.app.state()['running'])

    def test_restore_preserves_raw_and_derived_book_fields(self):
        s = Adapter('gate', DemoHTTP('gate')).collect('BTC')
        data = s.to_dict()
        self.assertEqual(restore(data).to_dict(), data)

    def test_cached_data_is_not_presented_as_fresh_candidate(self):
        s = Adapter('bybit', DemoHTTP('bybit')).collect('BTC')
        s.collected_at_ms = now_ms() - 120000
        self.app.latest['bybit:BTC/USDT'] = s
        data = self.app.detail('bybit:BTC/USDT')
        self.assertEqual(data['decision']['state'], 'EMERGENCY')
        self.assertIn('snapshot stale', data['decision']['reasons'])


if __name__ == '__main__':
    unittest.main()
