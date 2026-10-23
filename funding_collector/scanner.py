"""Lightweight public funding screen. Rates are observations, not net returns."""
import math
from .adapters import BASES
from .schema import now_ms


def scan(exchange, assets, http, stop):
    rows, errors = [], []
    def get(path, params=None):
        r = http.get(BASES[exchange] + path, params)
        if r['error']:
            raise ValueError(r['error'])
        return r['data']
    def add(asset, rate, interval, target, raw, source_time=None):
        try:
            rate = float(rate)
            interval = float(interval) if interval else None
            target = int(target)
            if not math.isfinite(rate) or (interval is not None and (not math.isfinite(interval) or interval <= 0)):
                raise ValueError('nonfinite rate / invalid interval')
            rows.append(dict(exchange=exchange, asset=asset, symbol=asset+'/USDT', rate=rate,
                             interval_seconds=interval, next_funding_time_ms=target,
                             observed_ms=now_ms(), source_ms=int(source_time) if source_time else None,
                             semantics='latest_unconfirmed' if exchange=='binance' else 'current_forecast', raw=raw))
        except (ValueError, TypeError) as exc:
            errors.append(asset+': '+str(exc))
    if exchange == 'binance':
        intervals={r['symbol']:r.get('fundingIntervalHours') for r in get('/fapi/v1/fundingInfo')}
        for r in get('/fapi/v1/premiumIndex'):
            a=r['symbol'][:-4]
            if r['symbol']==a+'USDT' and a in assets:
                h=intervals.get(r['symbol'])
                add(a,r['lastFundingRate'],float(h)*3600 if h else None,r['nextFundingTime'],r,r.get('time'))
    elif exchange == 'bybit':
        for r in get('/v5/market/tickers',{'category':'linear'})['result']['list']:
            a=r['symbol'][:-4]
            if r['symbol']==a+'USDT' and a in assets:
                h=r.get('fundingIntervalHour')
                add(a,r.get('fundingRate'),float(h)*3600 if h else None,r.get('nextFundingTime'),r)
    elif exchange == 'gate':
        for r in get('/futures/usdt/contracts'):
            a=r['name'][:-5]
            if a in assets:
                add(a,r.get('funding_rate'),r.get('funding_interval'),float(r['funding_next_apply'])*1000,r)
    else:
        for a in assets:
            if stop.is_set():
                break
            try:
                r=get('/api/v5/public/funding-rate',{'instId':a+'-USDT-SWAP'})['data'][0]
                interval=(int(r['nextFundingTime'])-int(r['fundingTime']))/1000
                add(a,r['fundingRate'],interval,r['fundingTime'],r,r.get('ts'))
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                errors.append(a+': '+str(exc))
                if '429' in str(exc) or 'COOLDOWN' in str(exc):
                    break
    return rows, errors


def ranked(rows, threshold_pct, basis='event', now=None, max_age_ms=180000):
    now = now_ms() if now is None else now
    result=[]
    for r in rows:
        if now-r['observed_ms'] > max_age_ms or r['observed_ms'] > now+5000 or r['next_funding_time_ms'] <= now:
            continue
        if r.get('source_ms') and (now-r['source_ms'] > max_age_ms or r['source_ms'] > now+5000):
            continue
        value=r['rate']
        if basis=='hour':
            if not r['interval_seconds']:
                continue
            value *= 3600/r['interval_seconds']
        if value > threshold_pct/100:
            result.append({**r, 'score':value})
    return sorted(result,key=lambda r:(-r['score'],r['exchange'],r['asset']))
