from mazecrypto.providers.exchanges.base import USD_QUOTES, ExchangeProvider, NotSupported, Pair
from mazecrypto.providers.exchanges.binance import Binance
from mazecrypto.providers.exchanges.btcturk import BtcTurk
from mazecrypto.providers.exchanges.coinbase import Coinbase
from mazecrypto.providers.exchanges.kraken import Kraken
from mazecrypto.providers.exchanges.paribu import Paribu

# Order here is the fallback order for chart data when the user's own
# exchanges do not list a pair.
EXCHANGES: dict[str, type[ExchangeProvider]] = {
    c.id: c for c in (Binance, Kraken, Coinbase, BtcTurk, Paribu)
}

__all__ = ["EXCHANGES", "USD_QUOTES", "ExchangeProvider", "NotSupported", "Pair"]
