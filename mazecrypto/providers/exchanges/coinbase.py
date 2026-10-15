from __future__ import annotations

from mazecrypto.core import validate as v
from mazecrypto.core.candles import resample
from mazecrypto.core.models import Candle, Interval
from mazecrypto.providers.exchanges.base import ExchangeProvider, Pair
from mazecrypto.providers.http import Http

# Coinbase's reference rates exist for every fiat, but only these are real
# order-book markets; the rest would be an FX conversion in disguise.
QUOTES = ("USD", "EUR", "GBP")
NATIVE = {Interval.H1: 3600, Interval.D1: 86400}
SOURCE = {Interval.H1: Interval.H1, Interval.H4: Interval.H1, Interval.D1: Interval.D1, Interval.W1: Interval.D1}


class Coinbase(ExchangeProvider):
    id = "coinbase"
    name = "Coinbase"
    country = "US · EU · UK"
    hosts = {"api.coinbase.com": 0.5, "api.exchange.coinbase.com": 0.4}

    async def tickers(self, http: Http) -> dict[Pair, float]:
        out: dict[Pair, float] = {}
        for q in QUOTES:
            data = v.mapping(await http.get_json("https://api.coinbase.com/v2/exchange-rates", {"currency": q}))
            rates = v.mapping(v.mapping(data.get("data")).get("rates"))
            for sym, rate in rates.items():
                try:
                    r = v.price(rate)
                    out[(v.symbol(sym), q)] = 1.0 / r
                except (v.ValidationError, ZeroDivisionError):
                    continue
        return out

    async def klines(self, http: Http, base: str, quote: str, interval: Interval, limit: int) -> list[Candle]:
        src = SOURCE[interval]
        rows = v.sequence(await http.get_json(
            f"https://api.exchange.coinbase.com/products/{v.symbol(base)}-{v.symbol(quote)}/candles",
            {"granularity": NATIVE[src]}))
        # Coinbase rows: [time, low, high, open, close, volume], newest first.
        candles = v.candles([v.candle(r[0], r[3], r[2], r[1], r[4], r[5]) for r in rows])
        if src is not interval:
            candles = resample(candles, interval.seconds)
        return candles[-limit:]
