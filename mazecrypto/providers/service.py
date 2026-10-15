"""MarketService: one refresh = fresh data where possible, cached data otherwise.

Fallback chains
---------------
* Fiat price:  user's exchanges (in their order) listing SYM/<fiat>
               -> USD price x exchange rate.
* USD price:   user's exchanges listing SYM/USD|USDT|USDC -> CoinGecko.
* Candles:     user's exchanges -> other exchanges with a candle API -> CoinGecko.
* Anything that fails falls back to its last cached value, flagged stale.

An exchange price that disagrees with CoinGecko by more than 25 % is ignored:
that is almost always two different tokens sharing a ticker symbol.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from mazecrypto.core import validate as v
from mazecrypto.core.candles import closes as close_list
from mazecrypto.core.indicators import correlation
from mazecrypto.core.models import Candle, Coin, CoinRow, Interval, MarketInfo
from mazecrypto.core.settings import Settings
from mazecrypto.core.store import Cache
from mazecrypto.providers import fx
from mazecrypto.providers.exchanges import EXCHANGES, USD_QUOTES, ExchangeProvider, NotSupported, Pair
from mazecrypto.providers.http import Http, NetError
from mazecrypto.providers.market import coingecko

SANITY = 0.25
TTL_FX = 3600
TTL_DAILY = 6 * 3600
TTL_SEARCH = 86400
BTC_ID = "bitcoin"


def _plausible(candles: list[Candle], reference: float | None) -> bool:
    """False for frozen or mismatched series (delisted pairs, ticker collisions)."""
    if len(candles) < 2:
        return False
    if reference and abs(candles[-1].close / reference - 1) > SANITY:
        return False
    return len({c.close for c in candles[-10:]}) > 1


def allowed_hosts() -> dict[str, float]:
    hosts = dict(coingecko.HOSTS) | dict(fx.HOSTS)
    for cls in EXCHANGES.values():
        hosts |= cls.hosts
    return hosts


@dataclass(slots=True)
class _Track:
    """Per-call bookkeeping: what failed and how old the oldest cached fallback was."""

    errors: list[str] = field(default_factory=list)
    oldest: float | None = None
    fresh_any: bool = False
    cache_only: bool = False


@dataclass(slots=True)
class Snapshot:
    rows: list[CoinRow]
    fx: dict[str, float]
    ts: float
    stale_age: float | None = None          # age of the oldest cached part used, None = all fresh
    offline: bool = False                   # nothing at all came from the network
    errors: list[str] = field(default_factory=list)

    def row(self, coin_id: str) -> CoinRow | None:
        return next((r for r in self.rows if r.coin.id == coin_id), None)


@dataclass(slots=True)
class CandleSet:
    candles: list[Candle]
    source: str
    quote: str
    stale_age: float | None = None
    converted: bool = False                 # USD candles multiplied by an exchange rate


class MarketService:
    def __init__(self, settings: Settings, cache: Cache, http: Http | None = None):
        self.settings = settings
        self.cache = cache
        self.http = http or Http(allowed_hosts(), settings.proxy.url if settings.proxy.enabled else None)
        self.exchanges: list[ExchangeProvider] = [EXCHANGES[i]() for i in settings.exchanges if i in EXCHANGES]
        mine = {e.id for e in self.exchanges}
        self.chart_sources = [e for e in self.exchanges if e.has_klines] + [
            cls() for cid, cls in EXCHANGES.items() if cid not in mine and cls.has_klines]
        self.tickers: dict[str, dict[Pair, float]] = {}
        self.fx: dict[str, float] = {"USD": 1.0}
        self._no_pair: set[str] = set()

    async def close(self) -> None:
        await self.http.close()

    # ── caching ───────────────────────────────────────────────────────────────
    async def _cached(self, key: str, ttl: float, fetch: Callable[[], Awaitable[Any]], trk: _Track) -> Any:
        hit = self.cache.fresh(key, ttl)
        if hit is not None:
            trk.fresh_any = True
            return hit
        if trk.cache_only:
            stale = self.cache.stale(key)
            if stale is None:
                raise NetError("not cached")
            age = time.time() - stale[0]
            trk.oldest = age if trk.oldest is None else max(trk.oldest, age)
            return stale[1]
        try:
            value = await fetch()
        except (NetError, v.ValidationError, NotSupported, KeyError, IndexError, TypeError) as e:
            stale = self.cache.stale(key)
            if stale is None:
                raise
            trk.errors.append(str(e) or type(e).__name__)
            age = time.time() - stale[0]
            trk.oldest = age if trk.oldest is None else max(trk.oldest, age)
            return stale[1]
        self.cache.put(key, value)
        trk.fresh_any = True
        return value

    # ── pieces ────────────────────────────────────────────────────────────────
    async def _fx(self, trk: _Track) -> dict[str, float]:
        wanted = {self.settings.fiat} | ({self.settings.secondary_fiat} - {""})
        key = "fx:" + ",".join(sorted(wanted))

        async def fetch() -> dict[str, float]:
            rates, errs = await fx.fetch_rates(self.http, wanted)
            if wanted - rates.keys():
                raise NetError("; ".join(errs) or "exchange rate unavailable")
            return rates

        self.fx = {**self.fx, **await self._cached(key, TTL_FX, fetch, trk)}
        return self.fx

    async def _markets(self, ids: list[str], trk: _Track) -> dict[str, MarketInfo]:
        key = "cg:markets:" + ",".join(sorted(ids))

        async def fetch() -> dict[str, dict]:
            return {k: asdict(m) for k, m in (await coingecko.markets(self.http, ids)).items()}

        raw = await self._cached(key, max(30, self.settings.refresh_seconds - 5), fetch, trk)
        return {k: MarketInfo(**d) for k, d in raw.items()}

    async def _tickers(self, ex: ExchangeProvider, trk: _Track) -> dict[Pair, float]:
        async def fetch() -> dict[str, float]:
            return {f"{b}/{q}": p for (b, q), p in (await ex.tickers(self.http)).items()}

        raw = await self._cached(f"tk:{ex.id}", max(15, self.settings.refresh_seconds - 5), fetch, trk)
        out = {tuple(k.split("/", 1)): float(p) for k, p in raw.items()}
        self.tickers[ex.id] = out  # type: ignore[assignment]
        return out  # type: ignore[return-value]

    def _pick(self, sym: str, quotes: tuple[str, ...], reference: float | None) -> tuple[float, str] | None:
        for ex in self.exchanges:
            book = self.tickers.get(ex.id, {})
            for q in quotes:
                p = book.get((sym, q))
                if p is None or sym == q:
                    continue
                if reference and abs(p / reference - 1) > SANITY:
                    continue
                return p, ex.name
        return None

    # ── refresh ───────────────────────────────────────────────────────────────
    async def refresh(self, watchlist: list[Coin], cache_only: bool = False) -> Snapshot:
        """Prices and market data for the watchlist. Fast: a handful of requests in parallel.

        ``cache_only`` builds the snapshot from the cache without touching the
        network, so the window has numbers to show the moment it opens.
        """
        trk = _Track(cache_only=cache_only)
        fiat = self.settings.fiat
        results = await asyncio.gather(
            self._fx(trk), self._markets([c.id for c in watchlist], trk),
            *(self._tickers(ex, trk) for ex in self.exchanges), return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException):
                if not isinstance(r, Exception):
                    raise r
                trk.errors.append(str(r) or type(r).__name__)
        markets: dict[str, MarketInfo] = results[1] if isinstance(results[1], dict) else {}
        rate = self.fx.get(fiat)
        rows = []
        for coin in watchlist:
            m = markets.get(coin.id)
            row = CoinRow(coin, updated=time.time())
            ref_usd = m.price_usd if m else None
            usd = self._pick(coin.symbol, USD_QUOTES, ref_usd)
            if usd:
                row.price_usd, row.usd_source = usd
            elif m:
                row.price_usd, row.usd_source = m.price_usd, "CoinGecko"
            if fiat == "USD":
                row.price_fiat, row.fiat_source = row.price_usd, row.usd_source
            else:
                ref_fiat = ref_usd * rate if ref_usd and rate else None
                hit = self._pick(coin.symbol, (fiat,), ref_fiat)
                if hit:
                    row.price_fiat, row.fiat_source = hit
                elif row.price_usd is not None and rate:
                    row.price_fiat, row.fiat_source = row.price_usd * rate, f"{row.usd_source} × FX"
            if m:
                row.change_24h, row.change_7d, row.change_30d = m.change_24h, m.change_7d, m.change_30d
                row.ath_change_pct, row.market_cap = m.ath_change_pct, m.market_cap
                if m.ath_usd and row.price_usd:
                    # Recompute against the live price so ATH distance matches the shown price.
                    row.ath_change_pct = (row.price_usd / m.ath_usd - 1) * 100
            row.stale = trk.oldest is not None
            rows.append(row)
        return Snapshot(rows, dict(self.fx), time.time(), trk.oldest, not trk.fresh_any, trk.errors)

    async def daily(self, coin: Coin, days: int = 31, trk: _Track | None = None,
                    ref_usd: float | None = None) -> list[Candle]:
        """Daily USD closes: Binance first (fast), CoinGecko otherwise. Cached for hours."""
        async def fetch() -> list[list[float]]:
            try:
                if f"binance:{coin.symbol}USDT" in self._no_pair:
                    raise NotSupported(coin.symbol)
                cs = await EXCHANGES["binance"]().klines(self.http, coin.symbol, "USDT", Interval.D1, days)
                if not _plausible(cs, ref_usd):
                    raise NotSupported(f"{coin.symbol}USDT looks delisted or mismatched")
            except (NetError, v.ValidationError, NotSupported):
                self._no_pair.add(f"binance:{coin.symbol}USDT")
                cs = await coingecko.daily_closes(self.http, coin.id, days)
            return [[c.ts, c.open, c.high, c.low, c.close, c.volume] for c in cs]

        raw = await self._cached(f"daily:{coin.id}:{days}", TTL_DAILY, fetch, trk or _Track())
        # Align to UTC midnight so series from different sources match day by day.
        return v.candles([Candle(int(r[0]) // 86400 * 86400, *map(float, r[1:])) for r in raw])

    async def enrich(self, snap: Snapshot, max_parallel: int = 3) -> Snapshot:
        """Second pass: 30-day correlation with BTC and a sparkline per coin."""
        trk = _Track()
        btc = next((r.coin for r in snap.rows if r.coin.id == BTC_ID), Coin(BTC_ID, "BTC", "Bitcoin"))
        sem = asyncio.Semaphore(max_parallel)

        ref = {r.coin.id: r.price_usd for r in snap.rows}

        async def one(c: Coin) -> list[Candle] | None:
            async with sem:
                try:
                    return await self.daily(c, trk=trk, ref_usd=ref.get(c.id))
                except (NetError, v.ValidationError, NotSupported) as e:
                    trk.errors.append(f"{c.symbol}: {e}")
                    return None

        async def skip() -> None:
            return None

        btc_daily = await one(btc)
        # Stablecoins: a flat line correlates with nothing; save the request.
        series = await asyncio.gather(*(skip() if r.coin.is_stable else one(r.coin) for r in snap.rows))
        for row, cs in zip(snap.rows, series, strict=True):
            if not cs:
                continue
            row.spark = close_list(cs)
            if row.coin.id == BTC_ID:
                row.corr_btc_30d = 1.0
            elif btc_daily:
                a = {c.ts: c.close for c in cs}
                b = {c.ts: c.close for c in btc_daily}
                common = sorted(a.keys() & b.keys())[-31:]
                row.corr_btc_30d = correlation([a[t] for t in common], [b[t] for t in common])
        snap.errors += trk.errors
        return snap

    async def candles(self, coin: Coin, quote_mode: str, interval: Interval, limit: int = 500,
                      ref_usd: float | None = None) -> CandleSet:
        """Chart candles in the portfolio currency (``fiat``) or in USD (``usd``).

        ``ref_usd`` (the current USD price) lets frozen or mismatched pairs be skipped.
        """
        trk = _Track()
        fiat = self.settings.fiat
        quotes = (fiat,) if quote_mode == "fiat" and fiat != "USD" else ("USDT", "USD", "USDC")
        ttl = max(30, min(interval.seconds // 6, 600))
        for ex in self.chart_sources:
            book = self.tickers.get(ex.id)
            for q in quotes:
                tag = f"{ex.id}:{coin.symbol}{q}:{interval.value}"
                if tag in self._no_pair or (book is not None and (coin.symbol, q) not in book):
                    continue

                ref = ref_usd if q in USD_QUOTES else (ref_usd * self.fx[q] if ref_usd and q in self.fx else None)

                async def fetch(ex: ExchangeProvider = ex, q: str = q, ref: float | None = ref) -> list[list[float]]:
                    cs = await ex.klines(self.http, coin.symbol, q, interval, limit)
                    if not _plausible(cs, ref):
                        raise v.ValidationError(f"{ex.name} {coin.symbol}{q}: implausible candles")
                    return [[c.ts, c.open, c.high, c.low, c.close, c.volume] for c in cs]

                try:
                    raw = await self._cached(f"k:{tag}:{limit}", ttl, fetch, trk)
                except (NetError, v.ValidationError, NotSupported, KeyError, IndexError, TypeError):
                    self._no_pair.add(tag)
                    continue
                cs = [Candle(int(r[0]), *map(float, r[1:])) for r in raw]
                return CandleSet(cs, ex.name, "USD" if q in USD_QUOTES else q, trk.oldest)
        if quote_mode == "fiat" and fiat != "USD":
            usd = await self.candles(coin, "usd", interval, limit, ref_usd)
            rate = self.fx.get(fiat)
            if rate:
                conv = [Candle(c.ts, c.open * rate, c.high * rate, c.low * rate, c.close * rate, c.volume)
                        for c in usd.candles]
                return CandleSet(conv, usd.source, fiat, usd.stale_age, converted=True)
            return usd

        async def gecko() -> list[list[float]]:
            cs = await coingecko.ohlc(self.http, coin.id, interval)
            return [[c.ts, c.open, c.high, c.low, c.close, c.volume] for c in cs]

        raw = await self._cached(f"cg:ohlc:{coin.id}:{interval.value}", ttl, gecko, trk)
        return CandleSet([Candle(int(r[0]), *map(float, r[1:])) for r in raw], "CoinGecko", "USD", trk.oldest)

    async def search(self, query: str) -> list[tuple[Coin, int | None]]:
        q = query.strip().lower()

        async def fetch() -> list[list]:
            return [[c.id, c.symbol, c.name, rank] for c, rank in await coingecko.search(self.http, q)]

        raw = await self._cached(f"search:{q}", TTL_SEARCH, fetch, _Track())
        return [(Coin(r[0], r[1], r[2]), r[3]) for r in raw]
