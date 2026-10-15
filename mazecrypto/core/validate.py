"""Validation of everything that comes from the network or from the user.

Nothing from a remote API reaches the rest of the app without passing through
these helpers: wrong types, NaN/inf and negative prices are rejected here.
"""
from __future__ import annotations

import math
import re
from typing import Any

from mazecrypto.core.models import Candle

SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,15}$")
COIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
FIAT_RE = re.compile(r"^[A-Z]{3}$")


class ValidationError(ValueError):
    pass


def number(value: Any, *, name: str = "value", allow_zero: bool = True,
           allow_negative: bool = False) -> float:
    """Coerce ``value`` (number or numeric string) to a finite float."""
    if isinstance(value, bool):
        raise ValidationError(f"{name}: boolean is not a number")
    if isinstance(value, (int, float)):
        f = float(value)
    elif isinstance(value, str):
        try:
            f = float(value.strip())
        except ValueError:
            raise ValidationError(f"{name}: not a number: {value!r}") from None
    else:
        raise ValidationError(f"{name}: unexpected type {type(value).__name__}")
    if not math.isfinite(f):
        raise ValidationError(f"{name}: not finite")
    if f < 0 and not allow_negative:
        raise ValidationError(f"{name}: negative")
    if f == 0 and not allow_zero:
        raise ValidationError(f"{name}: zero")
    return f


def price(value: Any, name: str = "price") -> float:
    return number(value, name=name, allow_zero=False)


def optional_number(value: Any, *, allow_negative: bool = False) -> float | None:
    """Like :func:`number` but maps missing or invalid values to ``None``."""
    if value is None:
        return None
    try:
        return number(value, allow_negative=allow_negative)
    except ValidationError:
        return None


def mapping(value: Any, name: str = "response") -> dict:
    if not isinstance(value, dict):
        raise ValidationError(f"{name}: expected object")
    return value


def sequence(value: Any, name: str = "response") -> list:
    if not isinstance(value, list):
        raise ValidationError(f"{name}: expected array")
    return value


def symbol(value: Any) -> str:
    s = str(value).upper().strip()
    if not SYMBOL_RE.match(s):
        raise ValidationError(f"bad symbol: {value!r}")
    return s


def coin_id(value: Any) -> str:
    s = str(value).strip()
    if not COIN_ID_RE.match(s):
        raise ValidationError(f"bad coin id: {value!r}")
    return s


def fiat(value: Any) -> str:
    s = str(value).upper().strip()
    if not FIAT_RE.match(s):
        raise ValidationError(f"bad currency: {value!r}")
    return s


def candle(ts: Any, o: Any, h: Any, lo: Any, c: Any, v: Any) -> Candle:
    """Build a candle and check it is internally consistent."""
    t = int(number(ts, name="ts", allow_zero=False))
    if t > 10_000_000_000:          # milliseconds -> seconds
        t //= 1000
    op, hi, low, cl = (price(x, n) for x, n in ((o, "open"), (h, "high"), (lo, "low"), (c, "close")))
    vol = number(v, name="volume")
    if hi < max(op, cl, low) * (1 - 1e-9) or low > min(op, cl, hi) * (1 + 1e-9):
        raise ValidationError("candle high/low inconsistent")
    return Candle(t, op, hi, low, cl, vol)


def candles(rows: list[Candle]) -> list[Candle]:
    """Sort by time and drop duplicate timestamps (keeping the last one)."""
    by_ts = {c.ts: c for c in rows}
    return [by_ts[t] for t in sorted(by_ts)]
