from __future__ import annotations

import time

from mazecrypto.core import validate as v
from mazecrypto.core.models import Candle, Interval
from mazecrypto.providers.exchanges.base import ExchangeProvider, Pair
from mazecrypto.providers.http import Http

API = "https://api.kraken.com/0/public"
ALIASES = {"XBT": "BTC", "XDG": "DOGE"}
MINUTES = {Interval.H1: 60, Interval.H4: 240, Interval.D1: 1440, Interval.W1: 10080}


def _norm(sym: str) -> str:
    return ALIASES.get(sym, sym)


class Kraken(ExchangeProvider):
    id = "kraken"
    name = "Kraken"
    country = "Global · EU · US"
    hosts = {"api.kraken.com": 1.0}

    def __init__(self) -> None:
        self._pairs: dict[str, Pair] = {}       # kraken pair name -> (base, quote)
        self._names: dict[Pair, str] = {}
        self._pairs_ts = 0.0

    async def _load_pairs(self, http: Http) -> None:
        if self._pairs and time.time() - self._pairs_ts < 86400:
            return
        res = v.mapping(v.mapping(await http.get_json(f"{API}/AssetPairs")).get("result"))
        pairs: dict[str, Pair] = {}
        for name, info in res.items():
            ws = str(info.get("wsname", "")) if isinstance(info, dict) else ""
            if "/" not in ws:
                continue
            b, q = ws.split("/", 1)
            try:
                pairs[name] = (v.symbol(_norm(b)), v.symbol(_norm(q)))
            except v.ValidationError:
                continue
        self._pairs, self._pairs_ts = pairs, time.time()
        self._names = {p: n for n, p in pairs.items()}

    async def tickers(self, http: Http) -> dict[Pair, float]:
        await self._load_pairs(http)
        res = v.mapping(v.mapping(await http.get_json(f"{API}/Ticker")).get("result"))
        out: dict[Pair, float] = {}
        for name, t in res.items():
            pair = self._pairs.get(name)
            try:
                if pair:
                    out[pair] = v.price(t["c"][0])
            except (KeyError, IndexError, TypeError, v.ValidationError):
                continue
        return out

    async def klines(self, http: Http, base: str, quote: str, interval: Interval, limit: int) -> list[Candle]:
        await self._load_pairs(http)
        name = self._names.get((base, quote))
        if not name:
            raise v.ValidationError("kraken: unknown pair")
        res = v.mapping(v.mapping(await http.get_json(f"{API}/OHLC", {
            "pair": name, "interval": MINUTES[interval]})).get("result"))
        rows = v.sequence(res.get(name))
        return v.candles([v.candle(r[0], r[1], r[2], r[3], r[4], r[6]) for r in rows])[-limit:]
