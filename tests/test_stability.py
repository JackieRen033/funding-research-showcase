import io
import unittest
from http.client import IncompleteRead
from unittest.mock import patch
from pathlib import Path
from funding_collector.transport import PublicHTTP
from funding_collector.adapters import Adapter, book
from funding_collector.demo import DemoHTTP
from funding_collector.decision import evaluate
from funding_collector.config import load
from funding_collector.schema import State


class StabilityTests(unittest.TestCase):
    def test_truncated_http_response_retries_and_recovers(self):
        response = io.BytesIO(b'{"retCode":0,"result":{}}')
        response.status = 200
        with patch('funding_collector.transport.urlopen', side_effect=[IncompleteRead(b'partial', 100), response]) as req, patch('funding_collector.transport.time.sleep'):
            e = PublicHTTP(min_gap=0, retries=1).get('https://example.com')
        self.assertIsNone(e['error'])
        self.assertEqual(req.call_count, 2)
        self.assertEqual(len(e['attempts']), 2)

    def test_stale_mark_blocks_research_candidate(self):
        class Stale(DemoHTTP):
            def get(self, url, params=None):
                e = super().get(url, params)
                if url.endswith('/mark-price'):
                    e['data']['data'][0]['ts'] = str(self.now - 120000)
                return e
        s = Adapter('okx', Stale('okx')).collect('BTC')
        cfg = load(Path(__file__).resolve().parents[1] / 'config.example.json')['screen']
        self.assertEqual(evaluate(s, cfg, s.collected_at_ms).state, State.EMERGENCY)

    def test_stale_oi_blocks_research_candidate(self):
        class Stale(DemoHTTP):
            def get(self, url, params=None):
                e = super().get(url, params)
                if url.endswith('/open-interest'):
                    e['data']['data'][0]['ts'] = str(self.now - 120000)
                return e
        s = Adapter('okx', Stale('okx')).collect('BTC')
        cfg = load(Path(__file__).resolve().parents[1] / 'config.example.json')['screen']
        self.assertEqual(evaluate(s, cfg, s.collected_at_ms).state, State.EMERGENCY)

    def test_zero_size_levels_removed_without_changing_prices(self):
        b = book({'bids': [['99','1'],['98','0']], 'asks': [['101','0'],['102','2']]})
        self.assertEqual(b.bid, 99)
        self.assertEqual(b.ask, 102)
        self.assertEqual(len(b.bids), 1)

    def test_disconnect_then_recovery_does_not_keep_old_errors(self):
        class Failing(DemoHTTP):
            failed = True
            def get(self, url, params=None):
                e = super().get(url, params)
                if self.failed:
                    e.update(data=None, error='NETWORK: simulated interruption')
                return e
        http = Failing('gate')
        a = Adapter('gate', http)
        first = a.collect('BTC')
        http.failed = False
        second = a.collect('BTC')
        self.assertEqual(first.connection, 'DISCONNECTED')
        self.assertEqual(second.connection, 'CONNECTED')
        self.assertEqual(second.issues, [])
        self.assertIsNone(first.mark)
        self.assertEqual(second.mark, 100000)

class LifecycleTests(unittest.TestCase):
    def test_cli_interrupt_restart_and_database_integrity(self):
        import json
        import os
        import signal
        import sqlite3
        import subprocess
        import sys
        import tempfile
        import time
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = json.loads((root / 'config.example.json').read_text())
            cfg['database'] = str(Path(tmp) / 'lifecycle.sqlite')
            cfg['poll_seconds'] = cfg['event_poll_seconds'] = 15
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps(cfg))
            command = [sys.executable, '-m', 'funding_collector', '--config', str(path), '--demo']
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            proc = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            db_path = cfg['database'] + '.demo'
            try:
                deadline = time.monotonic() + 10
                rows = 0
                while time.monotonic() < deadline:
                    if Path(db_path).exists():
                        try:
                            with sqlite3.connect(db_path) as db:
                                rows = db.execute('SELECT count(*) FROM snapshots').fetchone()[0]
                        except sqlite3.OperationalError:
                            pass
                    if rows == 8:
                        break
                    time.sleep(.05)
                self.assertEqual(rows, 8)
                proc.send_signal(signal.SIGINT)
                proc.communicate(timeout=10)
                self.assertEqual(proc.returncode, 0)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
            restarted = subprocess.run(command + ['--once'], cwd=root, env=env, capture_output=True, timeout=10)
            self.assertEqual(restarted.returncode, 0, restarted.stderr)
            with sqlite3.connect(db_path) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM snapshots').fetchone()[0], 16)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
