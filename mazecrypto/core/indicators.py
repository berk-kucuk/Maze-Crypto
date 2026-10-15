"""Technical indicators.

Every function takes a sequence of closes and returns a float array of the same
length; positions where the indicator is not defined yet are NaN, so results
line up index-for-index with the candles they were computed from.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

Series = Sequence[float] | np.ndarray


def _arr(values: Series) -> np.ndarray:
    return np.asarray(values, dtype=float)


def sma(values: Series, period: int) -> np.ndarray:
    x = _arr(values)
    out = np.full(x.shape, np.nan)
    if period <= 0 or len(x) < period:
        return out
    c = np.cumsum(np.insert(x, 0, 0.0))
    out[period - 1:] = (c[period:] - c[:-period]) / period
    return out


def ema(values: Series, period: int) -> np.ndarray:
    """Exponential moving average seeded with the SMA of the first ``period`` values."""
    x = _arr(values)
    out = np.full(x.shape, np.nan)
    if period <= 0 or len(x) < period:
        return out
    k = 2.0 / (period + 1)
    out[period - 1] = x[:period].mean()
    for i in range(period, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def rsi(values: Series, period: int = 14) -> np.ndarray:
    """Wilder's RSI."""
    x = _arr(values)
    out = np.full(x.shape, np.nan)
    if len(x) <= period:
        return out
    d = np.diff(x)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    avg_g = gain[:period].mean()
    avg_l = loss[:period].mean()

    def value(g: float, lo: float) -> float:
        if lo == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + g / lo)

    out[period] = value(avg_g, avg_l)
    for i in range(period + 1, len(x)):
        avg_g = (avg_g * (period - 1) + gain[i - 1]) / period
        avg_l = (avg_l * (period - 1) + loss[i - 1]) / period
        out[i] = value(avg_g, avg_l)
    return out


@dataclass(frozen=True)
class Macd:
    macd: np.ndarray
    signal: np.ndarray
    hist: np.ndarray


def macd(values: Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Macd:
    x = _arr(values)
    line = ema(x, fast) - ema(x, slow)
    sig = np.full(x.shape, np.nan)
    valid = ~np.isnan(line)
    if valid.sum() >= signal:
        start = int(np.argmax(valid))
        sig[start:] = ema(line[start:], signal)
    return Macd(line, sig, line - sig)


def returns(values: Series) -> np.ndarray:
    """Simple period-over-period returns (length n-1)."""
    x = _arr(values)
    if len(x) < 2:
        return np.array([])
    return x[1:] / x[:-1] - 1.0


def correlation(a: Series, b: Series) -> float | None:
    """Pearson correlation of the *returns* of two aligned price series.

    Prices themselves trend together almost always; returns are what tells
    whether two coins actually move together.
    """
    n = min(len(a), len(b))
    if n < 3:
        return None
    ra, rb = returns(_arr(a)[-n:]), returns(_arr(b)[-n:])
    if np.std(ra) == 0 or np.std(rb) == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def volatility(values: Series, periods_per_year: int = 365) -> float | None:
    """Annualised standard deviation of returns."""
    r = returns(values)
    if len(r) < 2:
        return None
    return float(np.std(r, ddof=1) * np.sqrt(periods_per_year))


def pct_change(values: Series, lookback: int) -> float | None:
    x = _arr(values)
    if len(x) <= lookback or x[-1 - lookback] == 0:
        return None
    return float((x[-1] / x[-1 - lookback] - 1.0) * 100.0)


def last(arr: np.ndarray) -> float | None:
    """Last defined value of an indicator array."""
    if len(arr) == 0 or np.isnan(arr[-1]):
        return None
    return float(arr[-1])
