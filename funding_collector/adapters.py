"""Official public REST mappings for USDT linear perpetual + same-venue spot."""
from .schema import FundingSnapshot, FundingEvent, Book, number as n, timestamp as ts, now_ms

BASES = {'binance': 'https://fapi.binance.com', 'okx': 'https://www.okx.com',
         'bybit': 'https://api.bybit.com', 'gate': 'https://api.gateio.ws/api/v4'}


def first(data):
    return data[0] if isinstance(data, list) and data else {}


def book(data, multiplier=1, time_key=None, scale=1, gate=False, bybit=False):
    if multiplier is None or multiplier <= 0:
        raise ValueError('unknown contract multiplier')
    def levels(key):
        result = []
        for row in data.get(key, []):
            p, q = (row['p'], row['s']) if gate else row[:2]
            p, q = n(p), n(q)
            if p is None or q is None or p <= 0 or q < 0:
                raise ValueError('invalid book level')
            if q:
                result.append([p, q * multiplier])
        return result
    bids, asks = levels('b' if bybit else 'bids'), levels('a' if bybit else 'asks')
    bids.sort(reverse=True)
    asks.sort()
    if bids and asks and bids[0][0] >= asks[0][0]:
        raise ValueError('crossed order book')
    return Book(bids, asks, ts(data.get(time_key), scale) if time_key else None)


class Adapter:
    def __init__(self, exchange, http):
        self.exchange, self.http = exchange, http

    def collect(self, asset):
        symbols = {'binance': asset + 'USDT', 'bybit': asset + 'USDT',
                   'okx': asset + '-USDT-SWAP', 'gate': asset + '_USDT'}
        s = FundingSnapshot(self.exchange, asset + '/USDT', symbols[self.exchange])
        def get(key, path, params=None, base=None):
            e = self.http.get((base or BASES[self.exchange]) + path, params)
            s.raw[key] = e
            if e['error']:
                s.issues.append(key + ': ' + e['error'])
                return [] if key in ('history', 'interval') else {}
            return e['data']
        try:
            getattr(self, '_' + self.exchange)(s, asset, get)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            s.issues.append(f'NORMALIZATION: {type(exc).__name__}: {exc}')
        s.collected_at_ms = now_ms()
        # Never infer history settlement from a wall-clock deadline alone.
        s.history = sorted([h for h in s.history if h.funding_time_ms <= s.collected_at_ms],
                           key=lambda h: h.funding_time_ms)
        if s.history:
            s.realized_time_ms = s.history[-1].funding_time_ms
            s.realized_rate = s.history[-1].realized_rate
        if s.spot_book.bid and s.spot_book.ask:
            s.spot = (s.spot_book.bid + s.spot_book.ask) / 2
        for label, value in [('funding', s.current_rate), ('next_funding', s.next_funding_time_ms),
                             ('interval', s.interval_seconds), ('mark', s.mark), ('index', s.index),
                             ('spot', s.spot), ('OI', s.open_interest_base)]:
            if value is None:
                s.issues.append('MISSING: ' + label)
        for label, value in [('mark', s.mark), ('index', s.index), ('spot', s.spot), ('interval', s.interval_seconds)]:
            if value is not None and value <= 0:
                s.issues.append('INVALID: ' + label + ' must be positive')
        if s.open_interest_base is not None and s.open_interest_base < 0:
            s.issues.append('INVALID: negative OI')
        for label, b in [('perp', s.perp_book), ('spot', s.spot_book)]:
            if not b.bids or not b.asks:
                s.issues.append('MISSING: ' + label + ' depth')
        for key, t in [('funding', s.exchange_time_ms), ('perp_book', s.perp_book.exchange_time_ms),
                       ('spot_book', s.spot_book.exchange_time_ms)]:
            if key in s.raw:
                s.raw[key]['exchange_time_ms'] = t
        failures = sum(bool(e['error']) for e in s.raw.values())
        s.connection = ('DISCONNECTED' if s.raw and failures == len(s.raw) else
                        'DEGRADED' if s.issues else 'CONNECTED')
        return s

    def _history(self, s, rows, rate_key, time_key, scale=1):
        source = s.raw['history']
        for r in rows:
            t, rate = ts(r.get(time_key), scale), n(r.get(rate_key))
            if t and rate is not None and r.get('rateType', 'Regular') == 'Regular':
                s.history.append(FundingEvent(s.exchange, s.symbol, t, rate,
                    source['received_at_ms'], source['url'], settlement_state='published'))

    def _binance(self, s, asset, get):
        p = {'symbol': s.contract}
        f = get('funding', '/fapi/v1/premiumIndex', p)
        info = get('interval', '/fapi/v1/fundingInfo')
        hist = get('history', '/fapi/v1/fundingRate', dict(p, limit=10))
        pb = get('perp_book', '/fapi/v1/depth', dict(p, limit=20))
        sb = get('spot_book', '/api/v3/depth', dict(p, limit=20), 'https://api.binance.com')
        oi = get('oi', '/fapi/v1/openInterest', p)
        s.current_rate = n(f.get('lastFundingRate'))
        # Official label says "latest"; preserve, do not silently upgrade to a forecast.
        s.sources['current_rate'] = 'premiumIndex.lastFundingRate (latest; forecast not asserted)'
        s.next_funding_time_ms = ts(f.get('nextFundingTime'))
        s.exchange_time_ms = ts(f.get('time'))
        s.mark, s.index = n(f.get('markPrice')), n(f.get('indexPrice'))
        self._history(s, hist, 'fundingRate', 'fundingTime')
        adjustment = next((r for r in info if r.get('symbol') == s.contract), {})
        interval = n(adjustment.get('fundingIntervalHours'))
        if interval:
            s.interval_seconds, s.interval_source = int(interval * 3600), 'fundingInfo.fundingIntervalHours'
        elif len(s.history) >= 2:
            h = sorted(s.history, key=lambda r: r.funding_time_ms)
            delta = h[-1].funding_time_ms - h[-2].funding_time_ms
            if delta > 0:
                s.interval_seconds, s.interval_source = delta // 1000, 'historical_observed_not_guaranteed'
        s.perp_book = book(pb, time_key='T')
        s.spot_book = book(sb)  # endpoint has no exchange timestamp
        s.open_interest_base = n(oi.get('openInterest'))
        s.sources['open_interest_base'] = 'openInterest.openInterest; USDT linear base units'
        s.raw['oi']['exchange_time_ms'] = ts(oi.get('time'))

    def _okx(self, s, asset, get):
        p = {'instId': s.contract}
        unwrap = lambda d: first(d.get('data', [])) if isinstance(d, dict) else {}
        f = unwrap(get('funding', '/api/v5/public/funding-rate', p))
        h = get('history', '/api/v5/public/funding-rate-history', dict(p, limit=10))
        inst = unwrap(get('instrument', '/api/v5/public/instruments', dict(p, instType='SWAP')))
        mark = unwrap(get('mark', '/api/v5/public/mark-price', dict(p, instType='SWAP')))
        idx = unwrap(get('index', '/api/v5/market/index-tickers', {'instId': asset + '-USDT'}))
        pb = unwrap(get('perp_book', '/api/v5/market/books', dict(p, sz=20)))
        sb = unwrap(get('spot_book', '/api/v5/market/books', {'instId': asset + '-USDT', 'sz': 20}))
        oi = unwrap(get('oi', '/api/v5/public/open-interest', dict(p, instType='SWAP')))
        s.current_rate = n(f.get('fundingRate'))
        s.current_rate_time_ms = s.next_funding_time_ms = ts(f.get('fundingTime'))
        following = ts(f.get('nextFundingTime'))
        if following and s.next_funding_time_ms and following > s.next_funding_time_ms:
            s.interval_seconds = (following - s.next_funding_time_ms) // 1000
            s.interval_source = 'nextFundingTime - fundingTime (forecast schedule)'
        if f.get('method') == 'current_period':
            s.predicted_rate, s.predicted_rate_time_ms = s.current_rate, s.current_rate_time_ms
        s.sources['predicted_rate'] = 'fundingRate current_period; nextFundingRate deprecated'
        s.exchange_time_ms = ts(f.get('ts'))
        s.settlement_state, s.settlement_rate = f.get('settState', 'unknown'), n(f.get('settFundingRate'))
        s.mark, s.index = n(mark.get('markPx')), n(idx.get('idxPx'))
        for key, payload in [('mark', mark), ('index', idx), ('oi', oi)]:
            s.raw[key]['exchange_time_ms'] = ts(payload.get('ts'))
        self._history(s, h.get('data', []) if isinstance(h, dict) else [], 'realizedRate', 'fundingTime')
        multiplier = n(inst.get('ctVal')) if inst.get('ctValCcy') == asset and inst.get('ctType') == 'linear' else None
        s.perp_book = book(pb, multiplier, 'ts')
        s.spot_book = book(sb, time_key='ts')
        s.open_interest_base = n(oi.get('oiCcy'))
        s.sources['open_interest_base'] = 'public/open-interest.oiCcy'

    def _bybit(self, s, asset, get):
        p = {'category': 'linear', 'symbol': s.contract}
        result = lambda d: d.get('result', {}) if isinstance(d, dict) else {}
        ticker = get('funding', '/v5/market/tickers', p)
        f = first(result(ticker).get('list', []))
        inst = first(result(get('instrument', '/v5/market/instruments-info', p)).get('list', []))
        h = result(get('history', '/v5/market/funding/history', dict(p, limit=10)))
        pb = result(get('perp_book', '/v5/market/orderbook', dict(p, limit=50)))
        sb = result(get('spot_book', '/v5/market/orderbook', {'category': 'spot', 'symbol': s.contract, 'limit': 50}))
        s.current_rate = s.predicted_rate = n(f.get('fundingRate'))
        s.current_rate_time_ms = s.predicted_rate_time_ms = s.next_funding_time_ms = ts(f.get('nextFundingTime'))
        s.sources['predicted_rate'] = 'tickers.fundingRate estimate for nextFundingTime'
        interval = n(f.get('fundingIntervalHour'))
        if interval:
            s.interval_seconds, s.interval_source = int(interval * 3600), 'tickers.fundingIntervalHour'
        elif n(inst.get('fundingInterval')):
            s.interval_seconds, s.interval_source = int(n(inst['fundingInterval']) * 60), 'instruments-info.fundingInterval'
        s.exchange_time_ms = ts(ticker.get('time'))
        s.mark, s.index = n(f.get('markPrice')), n(f.get('indexPrice'))
        self._history(s, h.get('list', []), 'fundingRate', 'fundingRateTimestamp')
        s.perp_book, s.spot_book = book(pb, time_key='ts', bybit=True), book(sb, time_key='ts', bybit=True)
        s.open_interest_base = n(f.get('openInterest'))
        s.sources['open_interest_base'] = 'linear tickers.openInterest (base coin)'

    def _gate(self, s, asset, get):
        p = {'contract': s.contract}
        f = get('funding', '/futures/usdt/contracts/' + s.contract)
        h = get('history', '/futures/usdt/funding_rate', dict(p, limit=10))
        pb = get('perp_book', '/futures/usdt/order_book', dict(p, limit=20, with_id='true'))
        sb = get('spot_book', '/spot/order_book', {'currency_pair': s.contract, 'limit': 20, 'with_id': 'true'})
        s.current_rate = s.predicted_rate = n(f.get('funding_rate'))
        s.current_rate_time_ms = s.predicted_rate_time_ms = s.next_funding_time_ms = ts(f.get('funding_next_apply'), 1000)
        s.sources['predicted_rate'] = 'contracts.funding_rate; deprecated indicative retained only in raw'
        interval = n(f.get('funding_interval'))
        if interval and interval > 0:
            s.interval_seconds, s.interval_source = int(interval), 'contracts.funding_interval'
        s.mark, s.index = n(f.get('mark_price')), n(f.get('index_price'))
        self._history(s, h, 'r', 't', 1000)
        multiplier = n(f.get('quanto_multiplier')) if f.get('type') == 'direct' else None
        s.perp_book = book(pb, multiplier, 'current', 1000, gate=True)
        s.spot_book = book(sb, time_key='current')  # spot current is milliseconds
        oi = n(f.get('position_size'))
        s.open_interest_base = oi * multiplier if oi is not None and multiplier else None
        s.sources['open_interest_base'] = 'contracts.position_size * quanto_multiplier'
