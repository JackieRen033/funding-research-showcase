"""Loopback-only local UI. Reuses the existing collector and decision engine."""
import argparse
import copy
import json
import math
import queue
import secrets
import sqlite3
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .adapters import Adapter
from .config import load
from .catalog import discover
from .scanner import scan, ranked
from .timing import preliminary
from .decision import evaluate
from .demo import DemoHTTP
from .schema import FundingSnapshot, FundingEvent, Book, now_ms
from .storage import Store
from .transport import PublicHTTP

WEB = Path(__file__).with_name('web')


def restore(payload):
    data = dict(payload)
    for key in ('perp_book', 'spot_book'):
        allowed = {f.name for f in fields(Book)}
        data[key] = Book(**{k: v for k, v in data[key].items() if k in allowed})
    data['history'] = [FundingEvent(**e) for e in data['history']]
    allowed = {f.name for f in fields(FundingSnapshot)}
    return FundingSnapshot(**{k: v for k, v in data.items() if k in allowed})


class Dashboard:
    def __init__(self, cfg, demo=False):
        self.cfg, self.demo = copy.deepcopy(cfg), demo
        self.database = cfg['database'] + '.demo' if demo else cfg['database']
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.running = self.stopping = False
        self.latest, self.activity = {}, []
        self.error = None
        self.total = self.cycles = 0
        self.last_cycle_ms = None
        self.selected = {'exchanges': cfg['exchanges'][:], 'assets': cfg['assets'][:]}
        self.auto = False
        self.cfg['screen']['time_cost'] = {'capital_budget_usdt':2*cfg['screen']['notional_usdt'], 'annual_cost_pct':5, 'entry_execution_seconds':60, 'exit_buffer_seconds':120}
        self.auto_options = {'threshold_pct': 0, 'basis': 'net', 'limit': 10, **self.cfg['screen']['time_cost']}
        self.scan_rows, self.scan_errors = [], {}
        self.scan_updated = None
        self.auto_pairs = []
        self.scan_status = {}
        self.catalog = {}
        self.catalog_errors = {}
        self.catalog_loading = False
        self.catalog_time = None
        self.catalog_thread = None
        self._load_existing()

    def _load_existing(self):
        if not Path(self.database).exists():
            return
        with sqlite3.connect(self.database) as db:
            self.total = db.execute('SELECT count(*) FROM snapshots').fetchone()[0]
            for exchange in self.cfg['exchanges']:
                for asset in self.cfg['assets']:
                    row = db.execute('SELECT payload FROM snapshots WHERE exchange=? AND symbol=? '
                        'ORDER BY observed_ms DESC LIMIT 1', (exchange, asset + '/USDT')).fetchone()
                    if row:
                        s = restore(json.loads(row[0]))
                        self.latest[exchange + ':' + s.symbol] = s

    def supports(self, exchange, asset):
        return asset in self.catalog.get(exchange, self.cfg['assets'])

    def refresh_catalog(self):
        with self.lock:
            if self.running or self.catalog_loading:
                raise ValueError('请先停止采集，或等待目录刷新完成')
            self.catalog_loading = True
            self.catalog_thread = threading.Thread(target=self._discover, daemon=True)
            self.catalog_thread.start()

    def _discover(self):
        results, errors = {}, {}
        def fetch(e):
            if self.demo:
                return ['BTC', 'ETH', 'SOL', 'SUI', 'ARB', 'WIF']
            return discover(e, PublicHTTP(self.cfg['timeout_seconds'], self.cfg['retries'],
                                          self.cfg['request_gap_seconds']))
        with ThreadPoolExecutor(max_workers=len(self.cfg['exchanges'])) as pool:
            futures = {pool.submit(fetch, e): e for e in self.cfg['exchanges']}
            for future in as_completed(futures):
                e = futures[future]
                try:
                    results[e] = future.result()
                except Exception as exc:
                    errors[e] = str(exc)
        with self.lock:
            self.catalog = results
            self.catalog_errors = errors
            self.catalog_time = now_ms()
            self.catalog_loading = False

    def start(self, selection=None):
        with self.lock:
            if self.catalog_loading:
                raise ValueError('请等待目录刷新完成')
            if self.running:
                return False
            selection = selection or self.selected
            for key in ('exchanges', 'assets'):
                values = selection.get(key)
                if not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values):
                    raise ValueError('请至少选择一个交易所和一个资产')
                allowed = self.cfg['exchanges'] if key == 'exchanges' else set(self.cfg['assets']).union(*self.catalog.values())
                if len(set(values)) != len(values) or not set(values).issubset(allowed):
                    raise ValueError('选择超出已配置的观察范围')
            if len(selection['assets']) > 10:
                raise ValueError('单次最多观察 10 个资产')
            if any(not any(self.supports(e, a) for e in selection['exchanges']) for a in selection['assets']):
                raise ValueError('部分资产在所选交易所没有匹配的现货与永续合约')
            self.auto = False
            self.selected = {k: selection[k][:] for k in ('exchanges', 'assets')}
            self.stop_event.clear()
            self.running, self.stopping, self.error = True, False, None
            self.thread = threading.Thread(target=self._run, name='funding-dashboard-collector', daemon=True)
            self.thread.start()
            return True

    def start_auto(self, options):
        threshold = float(options.get('threshold_pct', 0))
        basis = options.get('basis', 'net')
        limit = int(options.get('limit', 10))
        if not math.isfinite(threshold) or not 0 <= threshold <= 100 or basis not in ('net', 'event', 'hour') or not 1 <= limit <= 10:
            raise ValueError('阈值须为 0–100%，深入采集数量须为 1–10')
        assumptions = {}
        for key, default in self.cfg['screen']['time_cost'].items():
            value=float(options.get(key, default))
            if not math.isfinite(value) or value < 0 or (key=='capital_budget_usdt' and value==0):
                raise ValueError('资金及时间假设必须为有效非负数，资金预算须大于零')
            assumptions[key]=value
        if assumptions['entry_execution_seconds']+self.cfg['screen']['settlement_guard_seconds'] >= self.cfg['screen']['candidate_window_seconds']:
            raise ValueError('入场耗时加结算保护时间必须小于入场窗口')
        with self.lock:
            if self.running or self.catalog_loading:
                raise ValueError('请先停止当前采集或等待目录刷新')
            if self.demo:
                raise ValueError('自动扫描当前仅支持真实公开行情')
            self.auto = True
            self.auto_options = dict(threshold_pct=threshold, basis=basis, limit=limit, **assumptions)
            self.cfg['screen']['time_cost']=assumptions
            self.cfg['screen']['notional_usdt']=assumptions['capital_budget_usdt']/2
            self.scan_rows, self.scan_errors, self.auto_pairs = [], {}, []
            self.scan_updated = None
            self.scan_status = {}
            self.stop_event.clear()
            self.running, self.stopping, self.error = True, False, None
            self.thread = threading.Thread(target=self._run_auto, daemon=True)
            self.thread.start()

    def _scan_venue(self, exchange, client, updates):
        assets, catalog_at = [], 0
        while not self.stop_event.is_set():
            try:
                with self.lock:
                    self.scan_status[exchange] = 'scanning'
                if not assets or time.monotonic() - catalog_at >= 900:
                    assets = discover(exchange, client)
                    catalog_at = time.monotonic()
                    with self.lock:
                        self.catalog[exchange] = assets
                        self.catalog_errors.pop(exchange, None)
                        self.catalog_time = now_ms()
                rows, errors = scan(exchange, assets, client, self.stop_event)
                if self.stop_event.is_set():
                    break
                updates.put((exchange, rows, errors))
            except Exception as exc:
                updates.put((exchange, [], [str(exc)]))
            self.stop_event.wait(60)

    def _run_auto(self):
        store, workers = None, []
        updates = queue.Queue()
        venue_rows = {}
        try:
            store = Store(self.database)
            clients = {e: PublicHTTP(self.cfg['timeout_seconds'], self.cfg['retries'],
                                     self.cfg['request_gap_seconds']) for e in self.cfg['exchanges']}
            for e, client in clients.items():
                worker = threading.Thread(target=self._scan_venue, args=(e, client, updates), daemon=True)
                workers.append(worker)
                worker.start()
            next_refresh = 0
            while not self.stop_event.is_set():
                while True:
                    try:
                        e, rows, errors = updates.get_nowait()
                    except queue.Empty:
                        break
                    venue_rows[e] = rows
                    with self.lock:
                        self.scan_rows = [r for batch in venue_rows.values() for r in batch]
                        if errors:
                            self.scan_errors[e] = '; '.join(errors[:5])
                        else:
                            self.scan_errors.pop(e, None)
                        self.scan_status[e] = 'error' if errors else 'ready'
                        self.scan_updated = now_ms()
                    with open(self.database + '.scan.jsonl', 'a') as out:
                        out.write(json.dumps({'at_ms': now_ms(), 'exchange': e,
                            'options': self.auto_options, 'rows': rows, 'errors': errors}) + '\n')
                pool = ranked(self.scan_rows, self.auto_options['threshold_pct'],
                              'event' if self.auto_options['basis'] == 'net' else self.auto_options['basis'])
                winners = (preliminary(pool, self.cfg['screen'], now_ms())
                           if self.auto_options['basis'] == 'net' else pool)[:self.auto_options['limit']]
                pairs = [(r['exchange'], r['asset']) for r in winners]
                with self.lock:
                    changed = pairs != self.auto_pairs
                    self.auto_pairs = pairs
                if pairs and (changed or time.monotonic() >= next_refresh):
                    started = time.monotonic()
                    def collect_venue(e):
                        batch = []
                        for venue, asset in pairs:
                            if self.stop_event.is_set():
                                break
                            if venue == e:
                                batch.append(Adapter(e, clients[e]).collect(asset))
                        return batch
                    with ThreadPoolExecutor(max_workers=len(clients)) as executor:
                        futures = [executor.submit(collect_venue, e) for e in set(e for e, _ in pairs)]
                        for future in as_completed(futures):
                            for snapshot in future.result():
                                store.save(snapshot, self.cfg['pre_event_seconds'], self.cfg['post_event_seconds'])
                                with self.lock:
                                    self.latest[snapshot.exchange + ':' + snapshot.symbol] = snapshot
                                    self.total += 1
                    with self.lock:
                        self.cycles += 1
                        self.last_cycle_ms = now_ms()
                    next_refresh = max(started + self.cfg['event_poll_seconds'], time.monotonic() + .25)
                self.stop_event.wait(.2)
        except Exception as exc:
            with self.lock:
                self.error = type(exc).__name__ + ': ' + str(exc)
        finally:
            self.stop_event.set()
            for worker in workers:
                worker.join()
            if store:
                store.close()
            with self.lock:
                self.running = self.stopping = False

    def stop(self):
        with self.lock:
            if self.running:
                self.stopping = True
                self.stop_event.set()

    def _run(self):
        store = None
        try:
            store = Store(self.database)
            adapters = {e: Adapter(e, DemoHTTP(e) if self.demo else PublicHTTP(
                self.cfg['timeout_seconds'], self.cfg['retries'], self.cfg['request_gap_seconds']))
                for e in self.selected['exchanges']}
            def collect(exchange):
                adapter = adapters[exchange]
                if self.demo:
                    adapter.http.now = now_ms()
                batch = []
                for asset in self.selected['assets']:
                    if self.stop_event.is_set():
                        break
                    if self.supports(exchange, asset):
                        batch.append(adapter.collect(asset))
                return batch
            with ThreadPoolExecutor(max_workers=len(adapters)) as pool:
                while not self.stop_event.is_set():
                    started = time.monotonic()
                    futures = [pool.submit(collect, e) for e in adapters]
                    for future in as_completed(futures):
                        for s in future.result():
                            s.mode = 'synthetic' if self.demo else 'live'
                            store.save(s, self.cfg['pre_event_seconds'], self.cfg['post_event_seconds'])
                            with self.lock:
                                self.latest[s.exchange + ':' + s.symbol] = s
                                self.total += 1
                    with self.lock:
                        self.cycles += 1
                        self.last_cycle_ms = now_ms()
                        self.activity.insert(0, {'time': self.last_cycle_ms, 'message': '本轮采集完成，快照已保存'})
                        self.activity = self.activity[:8]
                    interval = self.cfg['event_poll_seconds'] if store.near_event(
                        now_ms(), self.cfg['pre_event_seconds'], self.cfg['post_event_seconds']) else self.cfg['poll_seconds']
                    self.stop_event.wait(max(.25, interval - (time.monotonic() - started)))
        except Exception as exc:
            # A failed background worker must be visible, not silently disappear.
            with self.lock:
                self.error = type(exc).__name__ + ': ' + str(exc)
        finally:
            if store:
                store.close()
            with self.lock:
                self.running = self.stopping = False

    def ranked_results(self, now):
        if self.auto_options['basis'] != 'net':
            return [{k:v for k,v in r.items() if k!='raw'} for r in ranked(self.scan_rows,self.auto_options['threshold_pct'],self.auto_options['basis'],now)]
        results=[]
        for exchange,asset in self.auto_pairs:
            s=self.latest.get(exchange+':'+asset+'/USDT')
            if not s:continue
            d=evaluate(s,self.cfg['screen'],now)
            if d.state not in ('READY','WAIT') or d.estimated_pnl is None or d.estimated_pnl<=0 or not d.evidence.get('entry_feasible'):continue
            results.append(dict(exchange=exchange,asset=asset,symbol=s.symbol,rate=s.predicted_rate,
                interval_seconds=s.interval_seconds,next_funding_time_ms=s.next_funding_time_ms,
                observed_ms=s.collected_at_ms,score=d.estimated_pnl,net_usdt=d.estimated_pnl,
                evidence=d.evidence,decision=asdict(d)))
        return sorted(results,key=lambda r:(-r['net_usdt'],r['exchange'],r['asset']))

    def state(self):
        with self.lock:
            now = now_ms()
            markets = []
            for exchange in (self.cfg['exchanges'] if self.auto else self.selected['exchanges']):
                for asset in ([a for e,a in self.auto_pairs if e==exchange] if self.auto else self.selected['assets']):
                    if not self.supports(exchange, asset):
                        continue
                    key = exchange + ':' + asset + '/USDT'
                    s = self.latest.get(key)
                    if not s:
                        markets.append({'key': key, 'exchange': exchange, 'symbol': asset + '/USDT',
                                        'connection': 'UNKNOWN', 'decision': None})
                        continue
                    d = asdict(evaluate(s, self.cfg['screen'], now))
                    markets.append({'key': key, 'exchange': exchange, 'symbol': s.symbol,
                        'connection': s.connection, 'decision': d, 'collected_at_ms': s.collected_at_ms,
                        'age_seconds': max(0, (now - s.collected_at_ms) / 1000),
                        'current_rate': s.current_rate, 'next_funding_time_ms': s.next_funding_time_ms,
                        'interval_seconds': s.interval_seconds,
                        'latency_ms': max((e.get('latency_ms', 0) for e in s.raw.values()), default=0)})
            return {'running': self.running, 'stopping': self.stopping, 'demo': self.demo,
                'auto': self.auto, 'scanner': {'options':dict(self.auto_options), 'updated_at_ms':self.scan_updated,
                    'errors':dict(self.scan_errors), 'venues':dict(self.scan_status), 'observed':len(self.scan_rows),
                    'evaluated':len(self.auto_pairs), 'rows':self.ranked_results(now)},
                'now_ms': now, 'error': self.error, 'cycles': self.cycles, 'total': self.total,
                'last_cycle_ms': self.last_cycle_ms, 'markets': markets, 'activity': self.activity[:],
                'selected': copy.deepcopy(self.selected),
                'available': {'exchanges': self.cfg['exchanges'],
                              'assets': sorted(set(self.cfg['assets']).union(*self.catalog.values()))},
                'configured_assets': self.cfg['assets'],
                'catalog': {'venues': copy.deepcopy(self.catalog), 'errors': dict(self.catalog_errors),
                            'loading': self.catalog_loading, 'updated_at_ms': self.catalog_time},
                'settings': {'poll_seconds': self.cfg['poll_seconds'],
                    'event_poll_seconds': self.cfg['event_poll_seconds'], **self.cfg['screen']}}

    def detail(self, key):
        with self.lock:
            s = self.latest.get(key)
            if not s:
                return None
            return {'snapshot': s.to_dict(), 'decision': asdict(evaluate(s, self.cfg['screen'], now_ms()))}

    def close(self):
        self.stop()
        if self.catalog_thread:
            self.catalog_thread.join()
        if self.thread:
            self.thread.join()  # finite transport timeouts; preserve completed requests


def make_handler(app, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, data, status=200, content_type='application/json; charset=utf-8'):
            encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if isinstance(data, (dict, list)) else data
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(encoded)

        def allowed_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def do_GET(self):
            if not self.allowed_host():
                return self.respond({'error': 'Invalid host'}, 403)
            parsed = urlparse(self.path)
            if parsed.path == '/api/state':
                return self.respond(app.state())
            if parsed.path == '/api/detail':
                result = app.detail(parse_qs(parsed.query).get('key', [''])[0])
                return self.respond(result or {'error': '尚无该市场的数据'}, 200 if result else 404)
            if parsed.path == '/api/export':
                if not Path(app.database).exists():
                    return self.respond({'error': '尚无已保存的快照'}, 404)
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.send_header('Content-Disposition', 'attachment; filename="funding-snapshots.jsonl"')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                try:
                    with sqlite3.connect(app.database) as db:
                        for row in db.execute('SELECT payload FROM snapshots ORDER BY observed_ms'):
                            self.wfile.write(row[0].encode() + b'\n')
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            files = {'/': ('index.html', 'text/html; charset=utf-8'),
                     '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if parsed.path in files:
                name, mime = files[parsed.path]
                data = (WEB / name).read_bytes()
                if name == 'index.html':
                    data = data.replace(b'__TOKEN__', token.encode())
                return self.respond(data, content_type=mime)
            self.respond({'error': 'Not found'}, 404)

        def do_POST(self):
            origin = self.headers.get('Origin')
            allowed_origins = (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')
            if not self.allowed_host() or (origin and origin not in allowed_origins) or not secrets.compare_digest(self.headers.get('X-Session-Token', ''), token):
                return self.respond({'error': 'Forbidden'}, 403)
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 <= size <= 8192:
                    raise ValueError('Request too large')
                data = json.loads(self.rfile.read(size) or b'{}')
                if not isinstance(data, dict):
                    raise ValueError('Invalid request')
                if self.path == '/api/auto/start':
                    app.start_auto(data)
                    return self.respond({'started': True})
                if self.path == '/api/catalog/refresh':
                    app.refresh_catalog()
                    return self.respond({'loading': True})
                if self.path == '/api/start':
                    started = app.start(data or None)
                    return self.respond({'started': started})
                if self.path == '/api/stop':
                    app.stop()
                    return self.respond({'stopping': True})
                self.respond({'error': 'Not found'}, 404)
            except (ValueError, TypeError) as exc:
                self.respond({'error': str(exc)}, 400)
    return Handler


def main():
    parser = argparse.ArgumentParser(description='Local funding research dashboard')
    parser.add_argument('--config', default='config.example.json')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--open', action='store_true', help='open the local workspace in your browser')
    args = parser.parse_args()
    try:
        if not 1024 <= args.port <= 65535:
            raise ValueError('port must be 1024..65535')
        app = Dashboard(load(args.config), args.demo)
        server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(app, secrets.token_hex(24)))
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(f'Funding Research UI: http://127.0.0.1:{args.port} | {"DEMO" if args.demo else "LIVE"} | press Ctrl+C to exit', flush=True)
    if args.open:
        webbrowser.open(f'http://127.0.0.1:{args.port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping collector and saving completed requests…', flush=True)
    finally:
        server.server_close()
        app.close()


if __name__ == '__main__':
    main()
