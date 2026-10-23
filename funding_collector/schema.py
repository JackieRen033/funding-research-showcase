from dataclasses import dataclass, field, asdict
from typing import Optional, Any
from enum import Enum
import math
import time
import uuid


def now_ms():
    return time.time_ns() // 1_000_000


def number(value):
    if value is None or value == '':
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('non-finite numeric value')
    return result


def timestamp(value, scale=1):
    value = number(value)
    return int(value * scale) if value and value > 0 else None


class State(str, Enum):
    WAIT = 'WAIT'
    READY = 'READY'
    HOLD = 'HOLD'
    EXIT = 'EXIT'
    REJECT = 'REJECT'
    EMERGENCY = 'EMERGENCY'


@dataclass
class FundingEvent:
    exchange: str
    symbol: str
    funding_time_ms: int
    realized_rate: Optional[float] = None
    observed_at_ms: int = 0
    source: str = 'schedule'
    funding_model: str = 'DISCRETE'
    # Public history confirms a market event, never a particular account payment.
    settlement_state: str = 'scheduled'

    @property
    def event_id(self):
        return f'{self.exchange}:{self.symbol}:{self.funding_time_ms}'


@dataclass
class Book:
    bids: list = field(default_factory=list)  # [price USDT, quantity base asset]
    asks: list = field(default_factory=list)
    exchange_time_ms: Optional[int] = None
    quantity_unit: str = 'base'

    @property
    def bid(self):
        return self.bids[0][0] if self.bids else None

    @property
    def ask(self):
        return self.asks[0][0] if self.asks else None


@dataclass
class FundingSnapshot:
    exchange: str
    symbol: str
    contract: str
    collected_at_ms: int = field(default_factory=now_ms)
    snapshot_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: str = '0.1'
    current_rate: Optional[float] = None
    current_rate_time_ms: Optional[int] = None
    predicted_rate: Optional[float] = None
    predicted_rate_time_ms: Optional[int] = None
    realized_rate: Optional[float] = None
    realized_time_ms: Optional[int] = None
    interval_seconds: Optional[int] = None
    interval_source: str = 'unknown'
    next_funding_time_ms: Optional[int] = None
    exchange_time_ms: Optional[int] = None
    settlement_state: str = 'unknown'
    settlement_rate: Optional[float] = None
    mark: Optional[float] = None
    index: Optional[float] = None
    spot: Optional[float] = None  # contemporaneous spot book midpoint
    open_interest_base: Optional[float] = None
    perp_book: Book = field(default_factory=Book)
    spot_book: Book = field(default_factory=Book)
    history: list = field(default_factory=list)
    sources: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)  # endpoint envelopes, including timing/errors
    issues: list = field(default_factory=list)
    connection: str = 'CONNECTED'
    mode: str = 'live'

    def to_dict(self):
        data = asdict(self)
        for key in ('perp_book', 'spot_book'):
            b = getattr(self, key)
            data[key]['best_bid'] = b.bid
            data[key]['best_ask'] = b.ask
        return data
