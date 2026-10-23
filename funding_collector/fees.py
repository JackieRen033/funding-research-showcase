"""Explicit per-order fee overrides. Unknown eligibility never grants discounts.
The current execution model is taker-only; maker offers remain reference data.
"""
import math


def fee_legs(cfg, exchange, symbol, entry_ms, exit_ms):
    base=cfg.get('fees_bps',{}).get(exchange)
    if not base:return None
    legs={}
    for market in ('spot','perp'):
        for phase,at in [('entry',entry_ms),('exit',exit_ms)]:
            key=market+'_'+phase
            value={'bps':base[market],'liquidity':'taker','source':'configured baseline; account rate unverified',
                   'rule_id':None,'at_ms':at,'market':market,'phase':phase}
            matches=[]
            for r in cfg.get('fee_rules',[]):
                if not r.get('enabled') or r.get('eligibility_confirmed') is not True:continue
                if (r['exchange']!=exchange or r['symbol']!=symbol or r['market']!=market or
                    r['liquidity']!='taker' or r['phase'] not in (phase,'both')):continue
                if not r['valid_from_ms']<=at<r['valid_until_ms']:continue
                matches.append(r)
            # Never silently stack rebates or choose the most optimistic conflicting rule.
            if len(matches)>1:
                raise ValueError('conflicting fee rules: '+exchange+' '+symbol+' '+key)
            if matches:
                r=matches[0];value.update(bps=r['bps'],source=r['source'],rule_id=r['id'])
            legs[key]=value
    return legs


def validate_rules(rules):
    if not isinstance(rules,list):raise ValueError('fee_rules must be a list')
    ids=set()
    for r in rules:
        for k in ('id','exchange','symbol','market','phase','liquidity','bps','source','valid_from_ms','valid_until_ms','enabled','eligibility_confirmed'):
            if k not in r:raise ValueError('fee rule missing '+k)
        if r['id'] in ids:raise ValueError('duplicate fee rule id')
        ids.add(r['id'])
        if r['exchange'] not in ('binance','okx','bybit','gate') or r['market'] not in ('spot','perp') or r['liquidity'] not in ('maker','taker') or r['phase'] not in ('entry','exit','both'):
            raise ValueError('invalid fee rule scope')
        if not isinstance(r['symbol'],str) or len(r['symbol'].split('/'))!=2 or not r['source']:
            raise ValueError('exact symbol and source required')
        for k in ('bps','valid_from_ms','valid_until_ms'):
            if isinstance(r[k],bool) or not isinstance(r[k],(int,float)) or not math.isfinite(r[k]):raise ValueError('invalid '+k)
        if not -100<=r['bps']<=100 or r['valid_until_ms']<=r['valid_from_ms']:
            raise ValueError('invalid fee rate / validity window')
        if type(r['enabled']) is not bool or type(r['eligibility_confirmed']) is not bool:
            raise ValueError('eligibility flags must be booleans')
