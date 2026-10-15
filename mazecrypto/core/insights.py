"""Descriptive analytics: what the market and the portfolio look like *now*.

Every output is a measurement (a number or a state such as "RSI above 70"),
never an instruction. The UI turns the ``state`` keys into translated text.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from mazecrypto.core import indicators as ind


def correlation_matrix(closes: Mapping[str, Sequence[float]]) -> tuple[list[str], np.ndarray]:
    """Pairwise return correlation of aligned daily closes. NaN where undefined."""
    keys = [k for k, c in closes.items() if len(c) >= 3]
    m = np.full((len(keys), len(keys)), np.nan)
    for i, a in enumerate(keys):
        for j, b in enumerate(keys):
            if j < i:
                m[i, j] = m[j, i]
                continue
            r = 1.0 if i == j else ind.correlation(closes[a], closes[b])
            m[i, j] = np.nan if r is None else r
    return keys, m


@dataclass(frozen=True, slots=True)
class TechState:
    rsi: float | None
    rsi_state: str            # "overbought" | "oversold" | "neutral" | ""
    trend: str                # "above_both" | "below_both" | "mixed" | ""  (price vs SMA50/200)
    macd_state: str           # "bullish" | "bearish" | ""
    volatility: float | None  # annualised, percent


def tech_state(closes: Sequence[float]) -> TechState:
    x = np.asarray(closes, dtype=float)
    r = ind.last(ind.rsi(x)) if len(x) > 15 else None
    rsi_state = "" if r is None else "overbought" if r >= 70 else "oversold" if r <= 30 else "neutral"
    s50, s200 = ind.last(ind.sma(x, 50)), ind.last(ind.sma(x, 200))
    trend = ""
    if len(x) and s50 is not None and s200 is not None:
        p = x[-1]
        trend = "above_both" if p > s50 and p > s200 else "below_both" if p < s50 and p < s200 else "mixed"
    m = ind.macd(x)
    h = ind.last(m.hist)
    macd_state = "" if h is None else "bullish" if h > 0 else "bearish"
    vol = ind.volatility(x[-91:]) if len(x) > 10 else None
    return TechState(r, rsi_state, trend, macd_state, None if vol is None else vol * 100)


def max_drawdown(closes: Sequence[float]) -> float | None:
    """Largest peak-to-trough fall, in percent (negative)."""
    x = np.asarray(closes, dtype=float)
    if len(x) < 2:
        return None
    peak = np.maximum.accumulate(x)
    return float(((x / peak) - 1.0).min() * 100.0)
