from __future__ import annotations

from mazecrypto.core import validate as v
from mazecrypto.core.models import Candle, Interval
from mazecrypto.providers.exchanges.base import ExchangeProvider, Pair
from mazecrypto.providers.http import Http

API = "https://api.binance.com/api/v3"
# Longest first so "FDUSD" wins over "USD"-like suffixes.
QUOTES = ("FDUSD", "USDT", "USDC", "TRY", "EUR", "BRL", "JPY", "PLN", "MXN", "ARS", "ZAR", "UAH", "COP",
          "BTC", "ETH", "BNB")


class Binance(ExchangeProvider):
    id = "binance"
    name = "Binance"
    country = "Global"
    hosts = {"api.binance.com": 0.25}

    async def tickers(self, http: Http) -> dict[Pair, float]:
        data = v.sequence(await http.get_json(f"{API}/ticker/price"))
        out: dict[Pair, float] = {}
        for row in data:
            try:
                sym = str(row["symbol"])
                quote = next(q for q in QUOTES if sym.endswith(q) and len(sym) > len(q))
                out[(sym[: -len(quote)], quote)] = v.price(row["price"])
            except (KeyError, StopIteration, TypeError, v.ValidationError):
                continue
        return out

    async def klines(self, http: Http, base: str, quote: str, interval: Interval, limit: int) -> list[Candle]:
        rows = v.sequence(await http.get_json(f"{API}/klines", {
            "symbol": f"{v.symbol(base)}{v.symbol(quote)}", "interval": interval.value, "limit": min(limit, 1000)}))
        return v.candles([v.candle(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows])
