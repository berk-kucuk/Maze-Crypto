from __future__ import annotations

import time

from mazecrypto.core import validate as v
from mazecrypto.core.models import Candle, Interval
from mazecrypto.providers.exchanges.base import ExchangeProvider, Pair
from mazecrypto.providers.http import Http

RESOLUTION = {Interval.H1: 60, Interval.H4: 240, Interval.D1: 1440, Interval.W1: 10080}


class BtcTurk(ExchangeProvider):
    id = "btcturk"
    name = "BtcTurk"
    country = "TR"
    hosts = {"api.btcturk.com": 0.5, "graph-api.btcturk.com": 0.5}

    async def tickers(self, http: Http) -> dict[Pair, float]:
        data = v.mapping(await http.get_json("https://api.btcturk.com/api/v2/ticker"))
        out: dict[Pair, float] = {}
        for row in v.sequence(data.get("data")):
            try:
                out[(v.symbol(row["numeratorSymbol"]), v.symbol(row["denominatorSymbol"]))] = v.price(row["last"])
            except (KeyError, TypeError, v.ValidationError):
                continue
        return out

    async def klines(self, http: Http, base: str, quote: str, interval: Interval, limit: int) -> list[Candle]:
        now = int(time.time())
        data = v.mapping(await http.get_json("https://graph-api.btcturk.com/v1/klines/history", {
            "symbol": f"{v.symbol(base)}{v.symbol(quote)}", "resolution": RESOLUTION[interval],
            "from": now - interval.seconds * limit, "to": now}))
        if data.get("s") != "ok":
            raise v.ValidationError("btcturk: no data")
        cols = [v.sequence(data.get(k)) for k in ("t", "o", "h", "l", "c", "v")]
        if len({len(c) for c in cols}) != 1:
            raise v.ValidationError("btcturk: ragged columns")
        return v.candles([v.candle(*row) for row in zip(*cols, strict=True)])[-limit:]
