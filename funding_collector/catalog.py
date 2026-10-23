"""Discover exact-unit USDT spot/perpetual pairs from public instrument metadata."""
import re
from .adapters import BASES


def discover(exchange, http):
    def get(path, params=None, base=None):
        response = http.get((base or BASES[exchange]) + path, params)
        if response['error']:
            raise ValueError(response['error'])
        return response['data']

    if exchange == 'binance':
        spot = get('/api/v3/exchangeInfo', base='https://api.binance.com')['symbols']
        perp = get('/fapi/v1/exchangeInfo')['symbols']
        spots = {r['baseAsset'] for r in spot if r['status'] == 'TRADING' and
                 r['quoteAsset'] == 'USDT' and r.get('isSpotTradingAllowed', False) and
                 r['symbol'] == r['baseAsset'] + 'USDT'}
        perps = {r['baseAsset'] for r in perp if r['status'] == 'TRADING' and
                 r['contractType'] == 'PERPETUAL' and r['quoteAsset'] == 'USDT' and
                 r['marginAsset'] == 'USDT' and r['symbol'] == r['baseAsset'] + 'USDT'}
    elif exchange == 'bybit':
        spot = get('/v5/market/instruments-info', {'category': 'spot'})['result']['list']
        perp, cursor, seen = [], '', set()
        for _ in range(100):
            params = {'category': 'linear', 'limit': 1000}
            if cursor:
                params['cursor'] = cursor
            result = get('/v5/market/instruments-info', params)['result']
            perp.extend(result['list'])
            cursor = result.get('nextPageCursor', '')
            if not cursor:
                break
            if cursor in seen:
                raise ValueError('Repeated instrument pagination cursor')
            seen.add(cursor)
        else:
            raise ValueError('Instrument pagination limit exceeded')
        spots = {r['baseCoin'] for r in spot if r['status'] == 'Trading' and r['quoteCoin'] == 'USDT'
                 and r['symbol'] == r['baseCoin'] + 'USDT'}
        perps = {r['baseCoin'] for r in perp if r['status'] == 'Trading' and
                 r['contractType'] == 'LinearPerpetual' and r['quoteCoin'] == r['settleCoin'] == 'USDT'
                 and r['symbol'] == r['baseCoin'] + 'USDT'}
    elif exchange == 'okx':
        spot = get('/api/v5/public/instruments', {'instType': 'SPOT'})['data']
        perp = get('/api/v5/public/instruments', {'instType': 'SWAP'})['data']
        spots = {r['baseCcy'] for r in spot if r['state'] == 'live' and r['quoteCcy'] == 'USDT'
                 and r['instId'] == r['baseCcy'] + '-USDT'}
        perps = {r['ctValCcy'] for r in perp if r['state'] == 'live' and
                 r['ctType'] == 'linear' and r['settleCcy'] == 'USDT' and
                 r['instId'] == r['ctValCcy'] + '-USDT-SWAP'}
    else:
        spot = get('/spot/currency_pairs')
        perp = get('/futures/usdt/contracts')
        spots = {r['base'] for r in spot if r['trade_status'] == 'tradable' and r['quote'] == 'USDT'
                 and r['id'] == r['base'] + '_USDT'}
        perps = {r['name'][:-5] for r in perp if r['name'].endswith('_USDT') and
                 not r.get('in_delisting', False) and r.get('type') == 'direct'}
    return sorted(a for a in spots & perps if re.fullmatch(r'[A-Z0-9]{2,16}', a))
