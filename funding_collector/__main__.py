import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from .config import load
from .adapters import Adapter
from .transport import PublicHTTP
from .demo import DemoHTTP
from .storage import Store
from .decision import evaluate
from .presentation import Presenter
from .schema import now_ms


def main(argv=None):
    parser = argparse.ArgumentParser(description='Funding research: public data only; no trading')
    parser.add_argument('--config', default='config.example.json')
    parser.add_argument('--once', action='store_true', help='one scan; exit 2 on data anomalies')
    parser.add_argument('--cycles', type=int, default=0, help='0 = continuous')
    parser.add_argument('--demo', action='store_true', help='synthetic fixtures, separate database')
    parser.add_argument('--mode', choices=['decision', 'evidence', 'detail', 'debug'], default='decision')
    parser.add_argument('--exchange', choices=['binance', 'okx', 'bybit', 'gate'])
    parser.add_argument('--asset', help='single configured asset, e.g. BTC')
    parser.add_argument('--export', metavar='JSONL', help='export existing database, no network')
    parser.add_argument('--event-id', help='optional event filter for export')
    args = parser.parse_args(argv)
    try:
        cfg = load(args.config)
        if args.cycles < 0:
            raise ValueError('cycles must be non-negative')
        if args.exchange:
            if args.exchange not in cfg['exchanges']:
                raise ValueError('exchange is not configured')
            cfg['exchanges'] = [args.exchange]
        if args.asset:
            if args.asset not in cfg['assets']:
                raise ValueError('asset is not configured')
            cfg['assets'] = [args.asset]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    database = cfg['database'] + '.demo' if args.demo else cfg['database']
    store = Store(database)
    try:
        if args.export:
            with open(args.export, 'w', encoding='utf-8') as stream:
                store.export(stream, args.event_id)
            return 0
        adapters = {e: Adapter(e, DemoHTTP(e) if args.demo else PublicHTTP(
            cfg['timeout_seconds'], cfg['retries'], cfg['request_gap_seconds'])) for e in cfg['exchanges']}
        presenter = Presenter(args.mode)
        def collect_exchange(exchange):
            adapter = adapters[exchange]
            if args.demo:
                adapter.http.now = now_ms()
            return [adapter.collect(asset) for asset in cfg['assets']]
        cycles = 1 if args.once else args.cycles
        iteration, last_status = 0, 0
        with ThreadPoolExecutor(max_workers=len(adapters)) as pool:
            while not cycles or iteration < cycles:
                started = time.monotonic()
                snapshots = [s for batch in pool.map(collect_exchange, adapters) for s in batch]
                for s in snapshots:
                    s.mode = 'synthetic' if args.demo else 'live'
                    store.save(s, cfg['pre_event_seconds'], cfg['post_event_seconds'])
                decisions = [evaluate(s, cfg['screen'], now_ms()) for s in snapshots]
                last_status = 2 if any(d.anomaly for d in decisions) else 0
                output = presenter.render(snapshots, decisions)
                if output:
                    print(output, flush=True)
                iteration += 1
                if cycles and iteration >= cycles:
                    break
                interval = cfg['event_poll_seconds'] if store.near_event(now_ms(), cfg['pre_event_seconds'],
                    cfg['post_event_seconds']) else cfg['poll_seconds']
                # Never overlap requests or catch up with a request burst after a slow cycle.
                time.sleep(max(0.25, interval - (time.monotonic() - started)))
        return last_status
    except KeyboardInterrupt:
        print('\nCollector stopped; committed snapshots retained.', file=sys.stderr)
        return 0
    finally:
        store.close()


if __name__ == '__main__':
    sys.exit(main())
