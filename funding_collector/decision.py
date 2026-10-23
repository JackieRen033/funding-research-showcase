"""Research screening only, independent of collection, storage and rendering."""
from dataclasses import dataclass, field
from .schema import State
from .timing import timing
from .fees import fee_legs


@dataclass
class Decision:
    exchange: str
    symbol: str
    state: State
    reasons: list
    net_bps: float = None
    estimated_pnl: float = None
    evidence: dict = field(default_factory=dict)
    anomaly: bool = False


def vwap(levels, quantity):
    cost, remaining = 0.0, quantity
    for price, size in levels:
        take = min(remaining, size)
        cost += price * take
        remaining -= take
        if remaining <= 1e-12:
            return cost / quantity
    return None


def evaluate(s, cfg, now):
    reasons = list(s.issues)
    max_latency = max((e.get('latency_ms', 0) for e in s.raw.values()), default=0)
    if max_latency > cfg['max_latency_ms']:
        reasons.append('API latency exceeds limit')
    if now - s.collected_at_ms > cfg['max_age_seconds'] * 1000:
        reasons.append('snapshot stale')
    for key in ('funding', 'perp_book', 'spot_book', 'mark', 'index', 'oi'):
        envelope = s.raw.get(key, {})
        source_time = envelope.get('exchange_time_ms')
        if key in ('mark', 'index', 'oi') and source_time is not None and (now - source_time > cfg['max_age_seconds'] * 1000 or source_time - now > cfg['max_clock_skew_ms']):
            reasons.append(key + ' source timestamp stale / clock skew')
        received = envelope.get('received_at_ms')
        if received and now - received > cfg['max_age_seconds'] * 1000:
            reasons.append(key + ' response stale')
    for label, t in [('funding', s.exchange_time_ms), ('perp book', s.perp_book.exchange_time_ms),
                     ('spot book', s.spot_book.exchange_time_ms)]:
        if t is not None and (now - t > cfg['max_age_seconds'] * 1000 or t - now > cfg['max_clock_skew_ms']):
            reasons.append(label + ' timestamp stale / clock skew')
    if reasons:
        return Decision(s.exchange, s.symbol, State.EMERGENCY, reasons[:3], anomaly=True,
                        evidence={'all_issues': reasons, 'max_latency_ms': max_latency})
    if not s.next_funding_time_ms or s.next_funding_time_ms <= now:
        return Decision(s.exchange, s.symbol, State.WAIT, ['waiting for updated funding schedule'])
    time_evidence = timing(s.next_funding_time_ms, now, cfg) if cfg.get('time_cost') else {}
    if time_evidence and not time_evidence['entry_feasible']:
        return Decision(s.exchange, s.symbol, State.REJECT, ['insufficient time for entry and settlement buffer'], evidence=time_evidence)
    if s.predicted_rate is None or s.predicted_rate_time_ms != s.next_funding_time_ms:
        return Decision(s.exchange, s.symbol, State.WAIT, ['no unambiguous forecast for target event'])
    if s.predicted_rate <= 0:
        return Decision(s.exchange, s.symbol, State.REJECT, ['spot-long / perp-short requires positive funding'])
    # Same base quantity on both legs. Round-trip book costs are a scenario, not a future fill forecast.
    quantity = cfg['notional_usdt'] / s.spot
    buys = [vwap(s.spot_book.asks, quantity), vwap(s.perp_book.asks, quantity)]
    sells = [vwap(s.spot_book.bids, quantity), vwap(s.perp_book.bids, quantity)]
    if any(x is None for x in buys + sells):
        return Decision(s.exchange, s.symbol, State.REJECT, ['insufficient sampled depth'], anomaly=True)
    funding = s.predicted_rate * s.mark * quantity
    spread_cost = sum(buys) * quantity - sum(sells) * quantity
    try:
        legs=fee_legs(cfg,s.exchange,s.symbol,time_evidence.get('planned_entry_ms',now),
                      time_evidence.get('planned_exit_ms',s.next_funding_time_ms+cfg['settlement_guard_seconds']*1000))
    except ValueError as exc:
        return Decision(s.exchange,s.symbol,State.WAIT,[str(exc)])
    if not legs:
        return Decision(s.exchange,s.symbol,State.WAIT,['fee assumptions not configured'])
    amounts={'spot_entry':buys[0]*quantity,'spot_exit':sells[0]*quantity,
             'perp_entry':sells[1]*quantity,'perp_exit':buys[1]*quantity}
    for key,leg in legs.items():
        leg['notional_usdt']=amounts[key]
        leg['cost_usdt']=amounts[key]*leg['bps']/10000
    fee_cost=sum(leg['cost_usdt'] for leg in legs.values())
    stress_cost = cfg['notional_usdt'] * cfg['extra_cost_bps'] / 10000
    before_time_cost = funding - spread_cost - fee_cost - stress_cost
    net = before_time_cost - time_evidence.get("capital_time_cost_usdt", 0)
    bps = net / cfg['notional_usdt'] * 10000
    seconds = (s.next_funding_time_ms - now) / 1000
    evidence = {'strategy': 'same-venue spot-long / perp-short', 'target_time_ms': s.next_funding_time_ms,
        'seconds_to_event': seconds, 'base_quantity': quantity, 'leg_notional_usdt': cfg['notional_usdt'],
        'funding_estimate_usdt': funding, 'round_trip_book_cost_usdt': spread_cost,
        'fee_legs': legs, 'execution_assumption': 'taker / taker; maker fills not modelled',
        'assumed_fee_cost_usdt': fee_cost, 'extra_cost_usdt': stress_cost, 'forecast_rate': s.predicted_rate,
        'basis_bps': (s.mark / s.spot - 1) * 10000, 'interval_seconds': s.interval_seconds,
        'interval_source': s.interval_source, 'max_latency_ms': max_latency,
        'limitations': 'unmodelled basis change, margin, liquidation, account eligibility and future liquidity'}
    evidence.update(time_evidence)
    evidence['net_before_time_cost_usdt'] = before_time_cost
    evidence['forecast_uncertainty'] = 'current forecast may change before settlement'
    if bps < cfg['min_net_bps']:
        state, why = State.REJECT, 'estimated net below research threshold'
    elif seconds <= cfg['settlement_guard_seconds']:
        state, why = State.WAIT, 'inside research settlement guard; eligibility unverified'
    elif seconds > cfg['candidate_window_seconds']:
        state, why = State.WAIT, 'positive estimate; outside research observation window'
    else:
        state, why = State.READY, 'research candidate only; execution risk checks not implemented'
    return Decision(s.exchange, s.symbol, state, [why], bps, net, evidence)
