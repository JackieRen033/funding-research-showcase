import json
import math
import re
from pathlib import Path
from .adapters import BASES
from .fees import validate_rules


def load(path):
    cfg = json.loads(Path(path).read_text())
    for field in ('exchanges', 'assets'):
        values = cfg.get(field)
        if not isinstance(values, list) or not values or len(set(values)) != len(values):
            raise ValueError(field + ' must be a nonempty unique list')
    if not set(cfg['exchanges']).issubset(BASES):
        raise ValueError('unknown exchange')
    if any(not isinstance(a, str) or not re.fullmatch('[A-Z0-9]{2,16}', a) for a in cfg['assets']):
        raise ValueError('assets must be uppercase base-asset symbols')
    if len(cfg['assets']) > 10:
        raise ValueError('V0.1 supports at most 10 assets; scale only with a weighted rate limiter')
    def positive(obj, key, minimum=0, inclusive=False):
        v = obj[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(key + ' must be a finite number')
        if v < minimum or (v == minimum and not inclusive):
            raise ValueError(key + ' outside supported range')
    for key in ('poll_seconds', 'event_poll_seconds'):
        positive(cfg, key, 15, True)
    for key in ('pre_event_seconds', 'post_event_seconds', 'timeout_seconds', 'request_gap_seconds'):
        positive(cfg, key)
    if cfg['request_gap_seconds'] < 0.25:
        raise ValueError('request_gap_seconds must be >= 0.25')
    if type(cfg['retries']) is not int or not 0 <= cfg['retries'] <= 3:
        raise ValueError('retries must be integer 0..3')
    if not isinstance(cfg['database'], str) or not cfg['database']:
        raise ValueError('database path required')
    screen = cfg['screen']
    for key in ('notional_usdt', 'max_latency_ms', 'max_age_seconds', 'max_clock_skew_ms',
                'candidate_window_seconds', 'settlement_guard_seconds'):
        positive(screen, key)
    for key in ('min_net_bps', 'extra_cost_bps'):
        positive(screen, key, inclusive=True)
    if screen['candidate_window_seconds'] <= screen['settlement_guard_seconds']:
        raise ValueError('candidate window must exceed settlement guard')
    for fees in screen['fees_bps'].values():
        for key in ('spot', 'perp'):
            positive(fees, key, inclusive=True)
    validate_rules(screen.get("fee_rules", []))
    return cfg
