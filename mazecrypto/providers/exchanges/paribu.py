from __future__ import annotations

from mazecrypto.core import validate as v
from mazecrypto.providers.exchanges.base import ExchangeProvider, Pair
from mazecrypto.providers.http import Http

QUOTE_ALIASES = {"TL": "TRY"}


class Paribu(ExchangeProvider):
    id = "paribu"
    name = "Paribu"
    country = "TR"
    hosts = {"www.paribu.com": 1.0}
    has_klines = False

    async def tickers(self, http: Http) -> dict[Pair, float]:
        data = v.mapping(await http.get_json("https://www.paribu.com/ticker"))
        out: dict[Pair, float] = {}
        for name, row in data.items():
            if "_" not in name or not isinstance(row, dict):
                continue
            b, q = name.split("_", 1)
            try:
                out[(v.symbol(b), v.symbol(QUOTE_ALIASES.get(q, q)))] = v.price(row["last"])
            except (KeyError, v.ValidationError):
                continue
        return out
