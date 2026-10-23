"""Explicit capital and entry/exit assumptions; no annualized return forecast."""

def timing(target_ms, now, cfg):
    t=cfg.get('time_cost', {})
    remaining=(target_ms-now)/1000
    window=cfg['candidate_window_seconds']
    guard=cfg['settlement_guard_seconds']
    entry=t.get('entry_execution_seconds',60)
    exit_buffer=t.get('exit_buffer_seconds',120)
    wait=max(0,remaining-window)
    occupied=min(remaining,window)+exit_buffer
    capital=t.get('capital_budget_usdt',2*cfg['notional_usdt'])
    annual=t.get('annual_cost_pct',0)
    return {'entry_wait_seconds':wait,'occupied_seconds':max(0,occupied),
            'planned_entry_ms':now+wait*1000, 'planned_exit_ms':target_ms+exit_buffer*1000,
            'entry_feasible':remaining>entry+guard,
            'capital_budget_usdt':capital,'annual_cost_pct':annual,
            'entry_execution_seconds':entry,'exit_buffer_seconds':exit_buffer,
            'capital_time_cost_usdt':capital*annual/100*max(0,occupied)/(365*86400)}


def preliminary(rows, cfg, now):
    """Choose a bounded investigation pool, not a claim of executable profit."""
    from .fees import fee_legs
    result=[]
    for r in rows:
        if r.get('semantics')=='latest_unconfirmed':continue
        t=timing(r['next_funding_time_ms'],now,cfg)
        try:
            fees=fee_legs(cfg,r['exchange'],r.get('symbol',r['asset']+'/USDT'),t['planned_entry_ms'],t['planned_exit_ms'])
        except ValueError:
            continue
        if not t['entry_feasible'] or not fees:continue
        net=cfg['notional_usdt']*(r['rate']-(sum(v['bps'] for v in fees.values())+cfg['extra_cost_bps'])/10000)-t['capital_time_cost_usdt']
        result.append({**r,'preliminary_net_usdt':net})
    return sorted(result,key=lambda r:(-r['preliminary_net_usdt'],r['exchange'],r['asset']))
