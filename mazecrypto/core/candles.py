"""Candle helpers."""
from __future__ import annotations

from mazecrypto.core.models import Candle

WEEK_OFFSET = 4 * 86400   # unix epoch was a Thursday; weeks start on Monday


def resample(candles: list[Candle], seconds: int) -> list[Candle]:
    """Merge candles into ``seconds``-wide buckets (e.g. 1h -> 4h, 1d -> 1w)."""
    out: list[Candle] = []
    offset = WEEK_OFFSET if seconds == 604800 else 0
    for c in candles:
        start = (c.ts - offset) // seconds * seconds + offset
        if out and out[-1].ts == start:
            p = out[-1]
            out[-1] = Candle(start, p.open, max(p.high, c.high), min(p.low, c.low), c.close, p.volume + c.volume)
        else:
            out.append(Candle(start, c.open, c.high, c.low, c.close, c.volume))
    return out


def closes(candles: list[Candle]) -> list[float]:
    return [c.close for c in candles]
