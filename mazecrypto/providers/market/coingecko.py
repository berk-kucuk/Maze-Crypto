"""CoinGecko public API: market data, coin search, daily history."""
from __future__ import annotations

from mazecrypto.core import validate as v
from mazecrypto.core.models import Candle, Coin, Interval, MarketInfo
from mazecrypto.providers.http import Http

API = "https://api.coingecko.com/api/v3"
# The keyless tier allows only a few calls a minute in practice; space them out.
# Everything from CoinGecko is cached, so this mostly matters on first start.
HOSTS = {"api.coingecko.com": 4.0}


def _market(row: dict) -> MarketInfo:
    o = v.optional_number
    return MarketInfo(
        id=v.coin_id(row["id"]), symbol=v.symbol(row["symbol"]), name=str(row.get("name", ""))[:60],
        price_usd=v.price(row["current_price"]),
        market_cap=o(row.get("market_cap")), circulating_supply=o(row.get("circulating_supply")),
        total_supply=o(row.get("total_supply")), max_supply=o(row.get("max_supply")),
        ath_usd=o(row.get("ath")), ath_change_pct=o(row.get("ath_change_percentage"), allow_negative=True),
        change_24h=o(row.get("price_change_percentage_24h_in_currency"), allow_negative=True),
        change_7d=o(row.get("price_change_percentage_7d_in_currency"), allow_negative=True),
        change_30d=o(row.get("price_change_percentage_30d_in_currency"), allow_negative=True),
        volume_24h=o(row.get("total_volume")),
    )


async def markets(http: Http, ids: list[str]) -> dict[str, MarketInfo]:
    out: dict[str, MarketInfo] = {}
    ids = [v.coin_id(i) for i in ids]
    for i in range(0, len(ids), 250):
        rows = v.sequence(await http.get_json(f"{API}/coins/markets", {
            "vs_currency": "usd", "ids": ",".join(ids[i:i + 250]), "per_page": 250,
            "price_change_percentage": "24h,7d,30d"}))
        for row in rows:
            try:
                m = _market(v.mapping(row))
                out[m.id] = m
            except (KeyError, v.ValidationError):
                continue
    return out


async def search(http: Http, query: str) -> list[tuple[Coin, int | None]]:
    """Coins matching ``query`` with their market-cap rank (best first)."""
    q = query.strip()[:40]
    if not q:
        return []
    data = v.mapping(await http.get_json(f"{API}/search", {"query": q}))
    out: list[tuple[Coin, int | None]] = []
    for row in v.sequence(data.get("coins", []))[:25]:
        try:
            coin = Coin(v.coin_id(row["id"]), v.symbol(row["symbol"]), str(row.get("name", ""))[:60])
        except (KeyError, v.ValidationError):
            continue
        rank = row.get("market_cap_rank")
        out.append((coin, rank if isinstance(rank, int) else None))
    return out


async def daily_closes(http: Http, coin_id: str, days: int = 31) -> list[Candle]:
    """Daily closing prices in USD as flat candles (CoinGecko has no free daily OHLC volume)."""
    data = v.mapping(await http.get_json(f"{API}/coins/{v.coin_id(coin_id)}/market_chart", {
        "vs_currency": "usd", "days": days, "interval": "daily"}))
    rows = []
    for ts, p in v.sequence(data.get("prices")):
        try:
            px = v.price(p)
            rows.append(Candle(int(v.number(ts)) // 1000 // 86400 * 86400, px, px, px, px, 0.0))
        except (TypeError, v.ValidationError):
            continue
    return v.candles(rows)


async def ohlc(http: Http, coin_id: str, interval: Interval) -> list[Candle]:
    """Last-resort chart data. CoinGecko picks candle width from ``days``."""
    days = {Interval.H1: 1, Interval.H4: 30, Interval.D1: 365, Interval.W1: 365}[interval]
    rows = v.sequence(await http.get_json(f"{API}/coins/{v.coin_id(coin_id)}/ohlc", {
        "vs_currency": "usd", "days": days}))
    return v.candles([v.candle(r[0], r[1], r[2], r[3], r[4], 0) for r in rows])
