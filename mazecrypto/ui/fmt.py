"""Number formatting that follows the interface language (1,234.56 vs 1.234,56)."""
from __future__ import annotations

import math
from datetime import datetime

from mazecrypto import i18n

SYMBOLS = {"USD": "$", "EUR": "€", "TRY": "₺", "GBP": "£", "JPY": "¥", "CHF": "CHF ", "KRW": "₩", "INR": "₹",
           "BRL": "R$", "PLN": "zł", "CAD": "C$", "AUD": "A$"}
DASH = "—"
MINUS = "\u2212"          # typographic minus: same width as the digits


def _sep(s: str) -> str:
    if i18n.language() == "tr":
        return s.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return s


def decimals_for(value: float) -> int:
    a = abs(value)
    if a == 0:
        return 2
    if a >= 1000:
        return 2
    if a >= 1:
        return 2 if a >= 10 else 3
    # Small prices: keep 4 significant digits (0.0001234 -> 0.0001234).
    return min(10, max(4, 3 - int(math.floor(math.log10(a)))))


def num(value: float | None, decimals: int | None = None) -> str:
    if value is None or not math.isfinite(value):
        return DASH
    d = decimals_for(value) if decimals is None else decimals
    return _sep(f"{value:,.{d}f}").replace("-", MINUS, 1)


def money(value: float | None, currency: str, decimals: int | None = None) -> str:
    if value is None or not math.isfinite(value):
        return DASH
    sym = SYMBOLS.get(currency)
    body = num(abs(value), decimals)
    sign = MINUS if value < 0 else ""
    return f"{sign}{sym}{body}" if sym else f"{sign}{body} {currency}"


def signed_money(value: float | None, currency: str, decimals: int | None = 2) -> str:
    if value is None or not math.isfinite(value):
        return DASH
    return ("+" if value > 0 else "") + money(value, currency, decimals)


def pct(value: float | None, decimals: int = 2, signed: bool = True) -> str:
    if value is None or not math.isfinite(value):
        return DASH
    body = _sep(f"{abs(value):.{decimals}f}")
    sign = MINUS if value < 0 and round(abs(value), decimals) else ("+" if signed and value > 0 else "")
    return f"{sign}%{body}" if i18n.language() == "tr" else f"{sign}{body}%"


def compact(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return DASH
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= div:
            return _sep(f"{value / div:.2f}") + suf
    return num(value)


def amount(value: float | None) -> str:
    """Coin quantities: up to 8 decimals, trailing zeros trimmed."""
    if value is None or not math.isfinite(value):
        return DASH
    s = f"{value:,.8f}".rstrip("0").rstrip(".")
    return _sep(s)


def date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y" if i18n.language() == "tr" else "%Y-%m-%d")


def age(seconds: float) -> str:
    s = int(seconds)
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def parse_number(text: str) -> float:
    """Accept both 1,234.5 and 1.234,5 (and plain 1234,5)."""
    t = text.strip().replace(" ", "").replace("_", "").replace(MINUS, "-")
    if not t:
        raise ValueError("empty")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if t.count(",") == 1 else t.replace(",", "")
    v = float(t)
    if not math.isfinite(v):
        raise ValueError("not finite")
    return v
