"""Plain data types shared by every layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

STABLECOINS = frozenset({"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD"})


class Interval(StrEnum):
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"

    @property
    def seconds(self) -> int:
        return {"1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}[self.value]


@dataclass(frozen=True, slots=True)
class Coin:
    """A coin as the user tracks it. ``id`` is the CoinGecko id, ``symbol`` the ticker."""

    id: str
    symbol: str
    name: str

    def to_dict(self) -> dict:
        return {"id": self.id, "symbol": self.symbol, "name": self.name}

    @classmethod
    def from_dict(cls, d: dict) -> Coin:
        return cls(str(d["id"]), str(d["symbol"]).upper(), str(d.get("name") or d["symbol"]))

    @property
    def is_stable(self) -> bool:
        return self.symbol in STABLECOINS


@dataclass(frozen=True, slots=True)
class Candle:
    ts: int          # open time, unix seconds
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class Quote:
    """One price of ``symbol`` in ``quote`` currency from one source."""

    symbol: str
    quote: str
    price: float
    source: str
    ts: float
    change_24h: float | None = None


@dataclass(slots=True)
class MarketInfo:
    """Market-wide data for one coin (CoinGecko), always in USD."""

    id: str
    symbol: str
    name: str
    price_usd: float
    market_cap: float | None = None
    circulating_supply: float | None = None
    total_supply: float | None = None
    max_supply: float | None = None
    ath_usd: float | None = None
    ath_change_pct: float | None = None
    change_24h: float | None = None
    change_7d: float | None = None
    change_30d: float | None = None
    volume_24h: float | None = None


@dataclass(slots=True)
class Trade:
    """A manually entered buy or sell.

    ``cost`` is what was paid (or received, for a sell) in ``fiat``, excluding the
    fee. ``usd_rate`` is fiat-per-USD at the time of the trade, so the USD cost
    basis stays historical instead of moving with today's exchange rate.
    """

    ts: int
    coin_id: str
    symbol: str
    amount: float
    cost: float
    fiat: str
    fee: float = 0.0
    side: str = "buy"
    platform: str = ""
    note: str = ""
    usd_rate: float | None = None
    id: int | None = None

    @property
    def total_cost(self) -> float:
        """Fiat that left the wallet for a buy (cost + fee), or arrived for a sell (cost - fee)."""
        return self.cost + self.fee if self.side == "buy" else self.cost - self.fee


@dataclass(slots=True)
class CoinRow:
    """Everything the watchlist shows for one coin after a refresh."""

    coin: Coin
    price_usd: float | None = None
    price_fiat: float | None = None
    usd_source: str = ""
    fiat_source: str = ""
    change_24h: float | None = None
    change_7d: float | None = None
    change_30d: float | None = None
    ath_change_pct: float | None = None
    corr_btc_30d: float | None = None
    market_cap: float | None = None
    stale: bool = False
    updated: float = 0.0
    spark: list[float] = field(default_factory=list)
