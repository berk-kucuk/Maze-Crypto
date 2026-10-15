"""Common interface for exchange price sources.

An exchange plug-in only needs two things: all of its tickers in one request,
and (optionally) candles for one pair. Adding an exchange means adding one
module here and listing it in :data:`mazecrypto.providers.exchanges.EXCHANGES`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from mazecrypto.core.models import Candle, Interval
from mazecrypto.providers.http import Http

Pair = tuple[str, str]          # (base symbol, quote symbol), e.g. ("BTC", "TRY")

# Quotes treated as US dollars when looking for a USD price.
USD_QUOTES = ("USD", "USDT", "USDC")


class NotSupported(Exception):
    pass


class ExchangeProvider(ABC):
    id: str = ""
    name: str = ""
    country: str = ""                        # shown in the onboarding picker
    hosts: dict[str, float] = {}             # host -> minimum spacing between requests (s)
    has_klines: bool = True

    @abstractmethod
    async def tickers(self, http: Http) -> dict[Pair, float]:
        """Last price of every pair, from one request where the API allows it."""

    async def klines(self, http: Http, base: str, quote: str, interval: Interval, limit: int) -> list[Candle]:
        raise NotSupported(self.id)

    def __repr__(self) -> str:
        return f"<{type(self).__name__}>"
