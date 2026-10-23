"""Synthetic endpoint-shaped fixtures, never market observations."""
from urllib.parse import urlparse
from .schema import now_ms


class DemoHTTP:
    def __init__(self, exchange, now=None):
        self.exchange, self.now = exchange, now or now_ms()

    def get(self, url, params=None):
        params = params or {}
        t = self.now
        target = t + 300_000
        previous = target - 3_600_000
        path = urlparse(url).path
        sym = params.get('symbol', 'BTCUSDT')
        levels = {'bids': [['99999', '10']], 'asks': [['100001', '10']]}
        if self.exchange == 'binance':
            if path.endswith('premiumIndex'):
                d = dict(symbol=sym, markPrice='100000', indexPrice='100000',
                         lastFundingRate='0.006', nextFundingTime=target, time=t)
            elif path.endswith('fundingInfo'):
                d = []
            elif path.endswith('fundingRate'):
                d = [dict(fundingTime=previous - 3_600_000, fundingRate='0.001'),
                     dict(fundingTime=previous, fundingRate='0.002')]
            elif path.endswith('openInterest'):
                d = {'openInterest': '123', 'time': t}
            else:
                d = dict(levels, T=t) if path.startswith('/fapi') else levels
        elif self.exchange == 'okx':
            if path.endswith('/funding-rate'):
                row = dict(fundingRate='0.006', fundingTime=str(target), nextFundingTime=str(target + 3_600_000),
                    method='current_period', nextFundingRate='', settState='settled', settFundingRate='0.002', ts=str(t))
            elif path.endswith('/funding-rate-history'):
                row = dict(fundingRate='0.003', realizedRate='0.002', fundingTime=str(previous))
            elif path.endswith('/instruments'):
                row = {'ctVal': '0.01', 'ctValCcy': params['instId'].split('-')[0], 'ctType': 'linear'}
            elif path.endswith('/mark-price'):
                row = {'markPx': '100000', 'ts': str(t)}
            elif path.endswith('/index-tickers'):
                row = {'idxPx': '100000', 'ts': str(t)}
            elif path.endswith('/open-interest'):
                row = {'oiCcy': '123', 'ts': str(t)}
            else:
                row = dict(levels, ts=str(t))
                if params['instId'].endswith('SWAP'):
                    row = {'bids': [['99999', '1000', '0', '1']], 'asks': [['100001', '1000', '0', '1']], 'ts': str(t)}
            d = {'code': '0', 'data': [row]}
        elif self.exchange == 'bybit':
            if path.endswith('/tickers'):
                result = {'list': [dict(fundingRate='0.006', nextFundingTime=str(target),
                    markPrice='100000', indexPrice='100000', openInterest='123', fundingIntervalHour='1')]}
            elif path.endswith('/instruments-info'):
                result = {'list': [{'fundingInterval': 60}]}
            elif path.endswith('/history'):
                result = {'list': [{'fundingRate': '0.002', 'fundingRateTimestamp': str(previous)}]}
            else:
                result = {'b': levels['bids'], 'a': levels['asks'], 'ts': t, 'cts': t}
            d = {'retCode': 0, 'result': result, 'time': t}
        else:
            if '/contracts/' in path:
                d = dict(type='direct', funding_rate='0.006', funding_rate_indicative='0.009',
                    funding_interval=3600, funding_next_apply=target / 1000, mark_price='100000',
                    index_price='100000', quanto_multiplier='0.0001', position_size='1230000')
            elif path.endswith('/funding_rate'):
                d = [{'t': previous // 1000, 'r': '0.002'}]
            elif '/futures/' in path:
                d = {'current': t / 1000, 'bids': [{'p': '99999', 's': 100000}],
                     'asks': [{'p': '100001', 's': 100000}]}
            else:
                d = dict(levels, current=t)
        return {'url': url, 'requested_at_ms': t, 'received_at_ms': t, 'latency_ms': 1,
                'attempts': [{'status': 200, 'error': None}], 'data': d, 'error': None,
                'synthetic': True}
