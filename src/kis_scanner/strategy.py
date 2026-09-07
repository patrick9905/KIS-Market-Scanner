from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Bar:
    day: str
    open: float
    high: float
    low: float
    close: float
    volume: int

    def __post_init__(self):
        date.fromisoformat(self.day)
        if not all(math.isfinite(x) and x > 0 for x in (self.open, self.high, self.low, self.close)):
            raise ValueError('Invalid OHLC price')
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.volume < 0:
            raise ValueError('Invalid bar range or volume')


@dataclass(frozen=True)
class Decision:
    enter: bool
    exit: bool
    atr: float


def decide(bars: list[Bar]) -> Decision:
    """Caller supplies completed bars only, before the execution date."""
    if any(a.day >= b.day for a, b in zip(bars, bars[1:])):
        raise ValueError('Bars must be unique and chronological')
    if len(bars) < 61:
        return Decision(False, False, 0)
    last = bars[-1]
    sma20 = sum(b.close for b in bars[-20:]) / 20
    sma60 = sum(b.close for b in bars[-60:]) / 60
    prior20 = sum(b.close for b in bars[-21:-1]) / 20
    atr = sum(max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
              for a, b in zip(bars[-15:-1], bars[-14:])) / 14
    volume = sum(b.volume for b in bars[-21:-1]) / 20
    liquid = sum(b.close * b.volume for b in bars[-20:]) / 20 >= 5_000_000_000
    enter = (last.close > max(b.high for b in bars[-21:-1])
             and last.close > sma20 > sma60 and sma20 > prior20
             and last.volume >= volume * 1.5 and liquid
             and 0.005 <= atr / last.close <= 0.05)
    return Decision(enter, last.close < sma20, atr)
