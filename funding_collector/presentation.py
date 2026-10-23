"""No transport, persistence or strategy calculations in this layer."""
import json
from dataclasses import asdict
from .schema import State


class Presenter:
    def __init__(self, mode='decision', top=5):
        self.mode, self.top, self.previous = mode, top, None

    def render(self, snapshots, decisions):
        if self.mode == 'debug':
            return '\n'.join(json.dumps(s.to_dict(), ensure_ascii=False) for s in snapshots)
        if self.mode == 'detail':
            return json.dumps([{'decision': asdict(d), 'snapshot': s.to_dict()}
                               for s, d in zip(snapshots, decisions)], indent=2, ensure_ascii=False)
        if self.mode == 'evidence':
            return json.dumps([asdict(d) for d in decisions], indent=2, ensure_ascii=False)
        qualified = [d for d in decisions if d.state == State.READY or
            (d.state == State.WAIT and d.net_bps is not None and d.net_bps > 0)]
        candidates = sorted([d for d in decisions if d.state == State.READY or
            (d.state == State.WAIT and d.net_bps is not None and d.net_bps > 0)],
            key=lambda d: d.net_bps or 0, reverse=True)[:self.top]
        anomalies = [d for d in decisions if d.anomaly]
        # Stable output: don't print every minor rate/countdown fluctuation.
        signature = (tuple((d.exchange, d.symbol, d.state, tuple(d.reasons)) for d in decisions),
                     tuple((d.exchange, d.symbol) for d in candidates))
        if signature == self.previous:
            return ''
        self.previous = signature
        lines = ['Funding Research Collector V0.1 | READ ONLY | ' + snapshots[0].mode.upper(),
                 f'{len(snapshots)} markets | {len(qualified)} qualified | showing {len(candidates)} | {len(anomalies)} anomalies']
        for d in candidates + anomalies[:self.top]:
            estimate = f' | scenario net {d.net_bps:+.2f} bps / {d.estimated_pnl:+.2f} USDT' if d.net_bps is not None else ''
            seconds = d.evidence.get('seconds_to_event')
            timing = f' | funding in {seconds / 60:.1f}m' if seconds is not None else ''
            lines.append(f'{d.exchange} {d.symbol} | {d.state.value}{estimate}{timing}')
            lines.extend('  ' + reason for reason in d.reasons[:3])
        if not candidates and not anomalies:
            lines.append('No high-value research candidates. Collection continues.')
        if len(anomalies) > self.top:
            lines.append(f'{len(anomalies) - self.top} more anomalies; use --mode evidence')
        return '\n'.join(lines)
