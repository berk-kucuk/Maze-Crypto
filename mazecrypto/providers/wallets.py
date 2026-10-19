"""Balances and history of public addresses, from public indexers that need no
account or API key.

Every request goes through :class:`Http` — the host allow-list, HTTPS only,
the optional Tor/SOCKS proxy, per-host spacing — and every number that comes
back is validated before it is shown or summed. Each chain has several
providers, tried in turn until one answers: a provider may be blocked where
the user is (country, VPN exit, ISP) or simply down.

Tokens: a token counts when its contract is in :data:`TOKENS` (it then joins
the coin it represents), or when the chain's indexer prices it, does not flag
it, and it trades in a real market. Everything else — the airdropped spam
every busy address collects — is left out and counted in
:attr:`WalletBalance.ignored`. Zero-value token transfers (address-poisoning
bait) never appear in the history.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, TypeVar
from urllib.parse import urlparse

from mazecrypto.core import address as addr
from mazecrypto.core import validate as v
from mazecrypto.core.models import Coin
from mazecrypto.core.store import Cache
from mazecrypto.core.wallet import Asset, Leg, Transfer, Wallet, WalletBalance
from mazecrypto.providers.exchanges import NotSupported
from mazecrypto.providers.http import Http, HttpError, NetError

T = TypeVar("T")

BTC = Coin("bitcoin", "BTC", "Bitcoin")
ETH = Coin("ethereum", "ETH", "Ethereum")
USDT = Coin("tether", "USDT", "Tether")
USDC = Coin("usd-coin", "USDC", "USDC")
HYPE = Coin("hyperliquid", "HYPE", "Hyperliquid")
SOL = Coin("solana", "SOL", "Solana")


@dataclass(frozen=True, slots=True)
class ChainInfo:
    """A network the app can watch addresses on. URL templates take {a} (address) or {t} (transaction)."""

    id: str
    name: str
    native: Coin | None
    explorer_address: str = ""
    explorer_tx: str = ""
    activity: bool = False  # transfer history readable in the app (else: balances, history in the explorer)
    manual: bool = False  # no public balance: the user enters the amount


CHAINS: tuple[ChainInfo, ...] = (
    ChainInfo(
        "bitcoin", "Bitcoin", BTC, "https://blockstream.info/address/{a}", "https://blockstream.info/tx/{t}", True
    ),
    ChainInfo("ethereum", "Ethereum", ETH, "https://etherscan.io/address/{a}", "https://etherscan.io/tx/{t}", True),
    ChainInfo("solana", "Solana", SOL, "https://solscan.io/account/{a}", "https://solscan.io/tx/{t}", True),
    ChainInfo(
        "hyperliquid",
        "Hyperliquid",
        HYPE,
        "https://app.hyperliquid.xyz/explorer/address/{a}",
        "https://app.hyperliquid.xyz/explorer/tx/{t}",
        True,
    ),
    ChainInfo(
        "tron",
        "TRON",
        Coin("tron", "TRX", "TRON"),
        "https://tronscan.org/#/address/{a}",
        "https://tronscan.org/#/transaction/{t}",
        True,
    ),
    ChainInfo(
        "litecoin",
        "Litecoin",
        Coin("litecoin", "LTC", "Litecoin"),
        "https://litecoinspace.org/address/{a}",
        "https://litecoinspace.org/tx/{t}",
        True,
    ),
    ChainInfo("base", "Base", ETH, "https://basescan.org/address/{a}", "https://basescan.org/tx/{t}"),
    ChainInfo("arbitrum", "Arbitrum One", ETH, "https://arbiscan.io/address/{a}", "https://arbiscan.io/tx/{t}"),
    ChainInfo(
        "optimism",
        "OP Mainnet",
        ETH,
        "https://optimistic.etherscan.io/address/{a}",
        "https://optimistic.etherscan.io/tx/{t}",
    ),
    ChainInfo(
        "polygon",
        "Polygon",
        Coin("polygon-ecosystem-token", "POL", "POL (ex-MATIC)"),
        "https://polygonscan.com/address/{a}",
        "https://polygonscan.com/tx/{t}",
    ),
    # Monero balances cannot be read from an address — that is the point of Monero.
    ChainInfo("monero", "Monero", Coin("monero", "XMR", "Monero"), manual=True),
    ChainInfo("manual", "Manual", None, manual=True),
)
_BY_ID = {c.id: c for c in CHAINS}


def chain(chain_id: str) -> ChainInfo | None:
    return _BY_ID.get(chain_id)


@dataclass(frozen=True, slots=True)
class KnownToken:
    chain: str
    contract: str  # lower-case for EVM, exact for base58
    coin: Coin
    decimals: int


# Contracts checked against the chains themselves.
TOKENS: tuple[KnownToken, ...] = (
    KnownToken("ethereum", "0xdac17f958d2ee523a2206206994597c13d831ec7", USDT, 6),
    KnownToken("ethereum", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", USDC, 6),
    KnownToken("ethereum", "0x6b175474e89094c44da98b954eedeac495271d0f", Coin("dai", "DAI", "Dai"), 18),
    KnownToken(
        "ethereum", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", Coin("wrapped-bitcoin", "WBTC", "Wrapped Bitcoin"), 8
    ),
    KnownToken("ethereum", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", Coin("weth", "WETH", "WETH"), 18),
    KnownToken("ethereum", "0x514910771af9ca656af840dff83e8264ecf986ca", Coin("chainlink", "LINK", "Chainlink"), 18),
    KnownToken("ethereum", "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", Coin("uniswap", "UNI", "Uniswap"), 18),
    KnownToken("base", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", USDC, 6),
    KnownToken("arbitrum", "0xaf88d065e77c8cc2239327c5edb3a432268e5831", USDC, 6),
    KnownToken("arbitrum", "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9", USDT, 6),
    KnownToken("optimism", "0x0b2c639c533813f4aa9d7837caf62653d097ff85", USDC, 6),
    KnownToken("polygon", "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359", USDC, 6),
    KnownToken("polygon", "0xc2132d05d31c914a87c6611c10748aeb04b58e8f", USDT, 6),
    KnownToken("solana", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", USDC, 6),
    KnownToken("solana", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", USDT, 6),
    KnownToken(
        "solana", "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", Coin("jupiter-exchange-solana", "JUP", "Jupiter"), 6
    ),
    KnownToken("solana", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", Coin("bonk", "BONK", "Bonk"), 5),
    KnownToken("tron", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", USDT, 6),
    KnownToken("tron", "TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8", USDC, 6),
)
_TOKEN_INDEX = {f"{t.chain}:{t.contract}": t for t in TOKENS}

# Hyperliquid spot tokens that stand for a coin (Unit-bridged BTC/ETH/SOL, USDT0).
HL_SPOT = {"HYPE": HYPE, "USDC": USDC, "USDT0": USDT, "UBTC": BTC, "UETH": ETH, "USOL": SOL}


def token(chain_id: str, contract: str) -> KnownToken | None:
    return _TOKEN_INDEX.get(f"{chain_id}:{contract}") or _TOKEN_INDEX.get(f"{chain_id}:{contract.lower()}")


def chains_for_coin(coin_id: str) -> list[str]:
    """Where an address for ``coin_id`` can live, most common first; ["manual"] when none can be read."""
    native = [c.id for c in CHAINS if not c.manual and c.native and c.native.id == coin_id]
    tokens = [t.chain for t in TOKENS if t.coin.id == coin_id]
    xmr = ["monero"] if coin_id == "monero" else []
    out = list(dict.fromkeys(native + tokens + xmr))
    return out or ["manual"]


def coins_of(chain_id: str) -> list[Coin]:
    """Every coin a wallet on ``chain_id`` may report, so prices load before the first balance."""
    c = chain(chain_id)
    return ([c.native] if c and c.native else []) + [t.coin for t in TOKENS if t.chain == chain_id]


def explorer_url(chain_id: str, address: str) -> str | None:
    c = chain(chain_id)
    return c.explorer_address.replace("{a}", address) if c and c.explorer_address else None


def tx_url(chain_id: str, txid: str) -> str | None:
    c = chain(chain_id)
    return c.explorer_tx.replace("{t}", txid) if c and c.explorer_tx else None


# ── providers ───────────────────────────────────────────────────────────────

ESPLORA = {
    # Blockstream first: mempool.space is blocked on some networks. Both serve the same API.
    "bitcoin": ("https://blockstream.info/api", "https://mempool.space/api"),
    "litecoin": ("https://litecoinspace.org/api",),
}
BLOCKSCOUT = "https://eth.blockscout.com/api/v2"
EVM_RPC = {
    "ethereum": ("https://ethereum-rpc.publicnode.com", "https://1rpc.io/eth", "https://eth.drpc.org"),
    "base": ("https://base-rpc.publicnode.com", "https://mainnet.base.org", "https://base.drpc.org"),
    "arbitrum": (
        "https://arbitrum-one-rpc.publicnode.com",
        "https://arb1.arbitrum.io/rpc",
        "https://arbitrum.drpc.org",
    ),
    "optimism": ("https://optimism-rpc.publicnode.com", "https://mainnet.optimism.io", "https://optimism.drpc.org"),
    "polygon": ("https://polygon-bor-rpc.publicnode.com", "https://polygon.drpc.org"),
}
EVM_CHAIN_ID = {"ethereum": 1, "base": 8453, "arbitrum": 42161, "optimism": 10, "polygon": 137}
SOLANA_RPC = ("https://api.mainnet-beta.solana.com", "https://solana-rpc.publicnode.com")
SPL_PROGRAMS = ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
JUPITER = "https://lite-api.jup.ag"
TRONGRID = "https://api.trongrid.io"
HL_INFO = "https://api.hyperliquid.xyz/info"
HYPER_EVM = "https://rpc.hyperliquid.xyz/evm"

# Hosts and the minimum gap between two requests to each, in seconds.
HOSTS = {
    "blockstream.info": 0.5,
    "mempool.space": 0.5,
    "litecoinspace.org": 0.5,
    "eth.blockscout.com": 0.3,
    "ethereum-rpc.publicnode.com": 0.2,
    "base-rpc.publicnode.com": 0.2,
    "arbitrum-one-rpc.publicnode.com": 0.2,
    "optimism-rpc.publicnode.com": 0.2,
    "polygon-bor-rpc.publicnode.com": 0.2,
    "1rpc.io": 0.3,
    "mainnet.base.org": 0.3,
    "arb1.arbitrum.io": 0.3,
    "mainnet.optimism.io": 0.3,
    "eth.drpc.org": 0.3,
    "base.drpc.org": 0.3,
    "arbitrum.drpc.org": 0.3,
    "optimism.drpc.org": 0.3,
    "polygon.drpc.org": 0.3,
    "api.mainnet-beta.solana.com": 0.35,
    "solana-rpc.publicnode.com": 0.35,
    "lite-api.jup.ag": 1.0,
    "api.trongrid.io": 0.5,
    "api.hyperliquid.xyz": 0.3,
    "rpc.hyperliquid.xyz": 0.3,
}

TTL_ACTIVITY = 300
DUST_USD = 0.01
MAX_TOKEN_LOOKUPS = 100
MIN_MARKET_CAP_USD = 1_000_000.0
MIN_VOLUME_USD = 10_000.0
MAX_SHARE_OF_CAP = 0.01
MIN_LIQUIDITY_USD = 50_000.0
MAX_WALLETS = 60

_RETRYABLE = (NetError, v.ValidationError, NotSupported, KeyError, IndexError, TypeError, ValueError, AttributeError)


@dataclass(slots=True)
class Fetched:
    """A value plus where it came from: ``stale_age`` seconds when served from the cache after a failure."""

    value: Any
    stale_age: float | None = None
    error: str | None = None


class WrongChain(NetError):
    pass


def units(raw: Any, decimals: int) -> float:
    """An integer amount in base units (string or number, decimal or 0x-hex) scaled by ``decimals``."""
    if raw is None:
        raise v.ValidationError("amount missing")
    s = str(raw).strip()
    if s.startswith("0x"):
        n = int(s[2:] or "0", 16)
    elif s.isdigit() and len(s) <= 80:
        n = int(s)
    else:
        raise v.ValidationError("amount: not an integer")
    if not 0 <= decimals <= 36:
        raise v.ValidationError("decimals")
    return float(Decimal(n).scaleb(-decimals))


def _decimals(raw: Any) -> int:
    d = int(v.number(raw, name="decimals"))
    if not 0 <= d <= 36:
        raise v.ValidationError("decimals")
    return d


def synthetic_id(chain_id: str, contract: str) -> str:
    """A stable id for a token only the chain's indexer can price; never sent to CoinGecko."""
    return f"x-{chain_id}-{hashlib.sha1(f'{chain_id}:{contract}'.encode()).hexdigest()[:16]}"


def _symbol(raw: Any) -> str:
    s = "".join(c for c in str(raw or "").strip().upper() if c.isalnum())[:12]
    return s or "?"


def _name(raw: Any, fallback: str) -> str:
    s = "".join(c for c in str(raw or "").strip() if c.isprintable())[:40]
    return s or fallback


def _iso(ts: Any) -> int:
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return 0


def _tradable(value_usd: float, market_cap: float | None, volume: float | None) -> bool:
    """Whether a token priced only by an indexer may count.

    Airdropped meme tokens often carry a "price" from one thin pool; billions of
    them would make the portfolio look rich. So: a real market (cap and volume),
    and a holding small enough against it for the price to mean anything.
    """
    if value_usd < DUST_USD or not market_cap or market_cap < MIN_MARKET_CAP_USD:
        return False
    if not volume or volume < MIN_VOLUME_USD:
        return False
    return value_usd <= market_cap * MAX_SHARE_OF_CAP


def _merge(assets: list[Asset]) -> list[Asset]:
    """One row per coin: the same coin can come from the native balance and a token list."""
    out: dict[str, Asset] = {}
    for a in assets:
        p = out.get(a.coin_id)
        if p is None:
            out[a.coin_id] = a
        else:
            p.amount += a.amount
            p.pending += a.pending
    return list(out.values())


def _rpc(id_: int, method: str, params: list) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}


class WalletService:
    def __init__(self, http: Http, cache: Cache):
        self.http = http
        self.cache = cache

    # ── cache ────────────────────────────────────────────────────────────────
    async def _cached(self, key: str, ttl: float, cache_only: bool, fetch: Callable[[], Awaitable[Any]]) -> Fetched:
        hit = self.cache.fresh(key, ttl)
        if hit is not None:
            return Fetched(hit)
        if cache_only:
            stale = self.cache.stale(key)
            if stale is None:
                raise NetError("not cached")
            return Fetched(stale[1], time.time() - stale[0])
        try:
            value = await fetch()
        except _RETRYABLE as e:
            stale = self.cache.stale(key)
            if stale is None:
                raise
            return Fetched(stale[1], time.time() - stale[0], str(e) or type(e).__name__)
        self.cache.put(key, value)
        return Fetched(value)

    async def balance(self, w: Wallet, ttl: float, cache_only: bool = False) -> Fetched:
        async def fetch() -> dict:
            return (await self._fetch_balance(w)).to_dict()

        f = await self._cached(f"w:bal:{addr.key(w.chain, w.address)}", ttl, cache_only, fetch)
        return Fetched(WalletBalance.from_dict(f.value), f.stale_age, f.error)

    async def activity(self, w: Wallet, cache_only: bool = False) -> Fetched:
        info = chain(w.chain)
        if info is None or not info.activity:
            return Fetched([])

        async def fetch() -> list[dict]:
            return [t.to_dict() for t in await self._fetch_activity(w)]

        f = await self._cached(f"w:tx:{addr.key(w.chain, w.address)}", TTL_ACTIVITY, cache_only, fetch)
        out = []
        for d in f.value:
            t = Transfer.from_dict(d)
            t.wallet_ids = {w.id}
            out.append(t)
        return Fetched(out, f.stale_age, f.error)

    async def _fetch_balance(self, w: Wallet) -> WalletBalance:
        c = w.chain
        if c in ESPLORA:
            return await self._esplora_balance(w)
        if c == "ethereum":
            try:
                return await self._blockscout_balance(w)
            except _RETRYABLE:
                return await self._rpc_balance(w)  # indexer down or throttled: native + known tokens from a node
        if c in EVM_RPC:
            return await self._rpc_balance(w)
        if c == "solana":
            return await self._solana_balance(w)
        if c == "tron":
            return await self._tron_balance(w)
        if c == "hyperliquid":
            return await self._hl_balance(w)
        raise NotSupported(c)

    async def _fetch_activity(self, w: Wallet) -> list[Transfer]:
        c = w.chain
        if c in ESPLORA:
            return await self._esplora_activity(w)
        if c == "ethereum":
            return await self._blockscout_activity(w)
        if c == "solana":
            return await self._solana_activity(w)
        if c == "tron":
            return await self._tron_activity(w)
        if c == "hyperliquid":
            return await self._hl_activity(w)
        return []

    @staticmethod
    def _native(w: Wallet) -> Coin:
        c = chain(w.chain)
        if c is None or c.native is None:
            raise NotSupported(w.chain)
        return c.native

    async def _first_ok(self, urls: tuple[str, ...], call: Callable[[str], Awaitable[T]]) -> T:
        """Try each provider in turn until one answers; the next refresh tries them all again."""
        last: Exception | None = None
        for url in urls:
            try:
                return await call(url)
            except HttpError as e:
                if e.code == 400:  # the address itself was refused: another mirror will say the same
                    raise v.ValidationError("address rejected by the explorer") from e
                last = e
            except (NetError, KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
                last = e
        raise last or NetError("no provider")

    # ── Bitcoin / Litecoin: Esplora ─────────────────────────────────────────
    async def _esplora_balance(self, w: Wallet) -> WalletBalance:
        coin = self._native(w)

        async def call(base: str) -> WalletBalance:
            o = v.mapping(await self.http.get_json(f"{base}/address/{w.address}"))
            ch, mem = o["chain_stats"], o.get("mempool_stats") or {}

            def net(s: dict) -> float:
                return v.number(s.get("funded_txo_sum", 0)) - v.number(s.get("spent_txo_sum", 0))

            confirmed, pending = net(ch) / 1e8, net(mem) / 1e8
            return WalletBalance(
                [Asset(coin.id, coin.symbol, coin.name, max(0.0, confirmed + pending), pending=pending)],
                time.time(),
                int(ch.get("tx_count", 0)) + int(mem.get("tx_count", 0)),
            )

        return await self._first_ok(ESPLORA[w.chain], call)

    async def _esplora_activity(self, w: Wallet) -> list[Transfer]:
        coin = self._native(w)

        async def call(base: str) -> list[Transfer]:
            out = []
            for tx in (await self.http.get_json(f"{base}/address/{w.address}/txs"))[:25]:
                try:
                    received = spent = 0
                    frm = to = ""
                    for vin in tx.get("vin") or []:
                        prev = vin.get("prevout") or {}
                        a = prev.get("scriptpubkey_address", "")
                        if a == w.address:
                            spent += int(v.number(prev.get("value")))
                        elif not frm:
                            frm = a
                    for o in tx.get("vout") or []:
                        a = o.get("scriptpubkey_address", "")
                        if a == w.address:
                            received += int(v.number(o.get("value")))
                        elif not to and a:
                            to = a
                    st = tx.get("status") or {}
                    ok = bool(st.get("confirmed"))
                    delta = (received - spent) / 1e8
                    out.append(
                        Transfer(
                            w.chain,
                            str(tx["txid"])[:80],
                            int(st.get("block_time", 0)) if ok else 0,
                            "ok" if ok else "pending",
                            [Leg(coin.id, coin.symbol, delta)],
                            v.number(tx.get("fee", 0)) / 1e8 if spent else None,
                            coin.symbol,
                            True,
                            to if delta < 0 else frm,
                        )
                    )
                except _RETRYABLE:
                    continue
            return out

        return await self._first_ok(ESPLORA[w.chain], call)

    # ── Ethereum: Blockscout ────────────────────────────────────────────────
    async def _blockscout_balance(self, w: Wallet) -> WalletBalance:
        coin = self._native(w)
        base = f"{BLOCKSCOUT}/addresses/{w.address}"
        try:
            info = v.mapping(await self.http.get_json(base))
        except HttpError as e:
            if e.code == 404:  # never seen by the indexer: an unused address, empty
                return WalletBalance([Asset(coin.id, coin.symbol, coin.name, 0.0)], time.time(), 0)
            raise
        assets = [Asset(coin.id, coin.symbol, coin.name, units(info.get("coin_balance") or "0", 18))]
        ignored = 0
        params: dict[str, Any] = {"type": "ERC-20"}
        for _page in range(2):
            o = v.mapping(await self.http.get_json(f"{base}/tokens", params))
            for item in o.get("items") or []:
                tok = item.get("token") or {}
                contract = str(tok.get("address_hash", "")).lower()
                try:
                    amount = units(item.get("value"), _decimals(tok.get("decimals")))
                    known = token("ethereum", contract)
                    if known:
                        a = Asset(known.coin.id, known.coin.symbol, known.coin.name, amount, contract=contract)
                    else:
                        rate = v.optional_number(tok.get("exchange_rate"))
                        flagged = tok.get("reputation") not in (None, "", "ok")
                        if (
                            not rate
                            or flagged
                            or not _tradable(
                                amount * rate,
                                v.optional_number(tok.get("circulating_market_cap")),
                                v.optional_number(tok.get("volume_24h")),
                            )
                        ):
                            ignored += 1
                            continue
                        sym = _symbol(tok.get("symbol"))
                        a = Asset(
                            synthetic_id("ethereum", contract), sym, _name(tok.get("name"), sym), amount, rate, contract
                        )
                except _RETRYABLE:
                    ignored += 1
                    continue
                if a.amount > 0:
                    assets.append(a)
            nxt = o.get("next_page_params")
            if not nxt:
                break
            params = {"type": "ERC-20"} | {k: str(val) for k, val in nxt.items() if val is not None}
        tx_count = v.optional_number(info.get("transactions_count"))
        return WalletBalance(_merge(assets), time.time(), int(tx_count) if tx_count is not None else None, ignored)

    async def _blockscout_activity(self, w: Wallet) -> list[Transfer]:
        coin = self._native(w)
        me = w.address.lower()
        base = f"{BLOCKSCOUT}/addresses/{w.address}"

        async def items(url: str, params: dict | None = None) -> list[dict]:
            try:
                return v.mapping(await self.http.get_json(url, params)).get("items") or []
            except HttpError as e:
                if e.code == 404:
                    return []
                raise

        by_hash: dict[str, Transfer] = {}
        for tx in (await items(f"{base}/transactions"))[:25]:
            try:
                h = str(tx["hash"])[:80]
                frm = str((tx.get("from") or {}).get("hash", "")).lower()
                to = str((tx.get("to") or {}).get("hash", "")).lower()
                value = units(tx.get("value") or "0", 18)
                legs = []
                if value > 0 and frm == me and to != me:
                    legs.append(Leg(coin.id, coin.symbol, -value))
                elif value > 0 and to == me and frm != me:
                    legs.append(Leg(coin.id, coin.symbol, value))
                pending = tx.get("block_number") is None or tx.get("result") == "pending"
                fee = units((tx.get("fee") or {}).get("value"), 18) if frm == me else None
                cp = (tx.get("to") or {}).get("hash", "") if frm == me else (tx.get("from") or {}).get("hash", "")
                by_hash[h] = Transfer(
                    w.chain,
                    h,
                    0 if pending else _iso(tx.get("timestamp")),
                    "pending" if pending else "failed" if tx.get("status") == "error" else "ok",
                    legs,
                    fee,
                    coin.symbol,
                    counterparty=str(cp or ""),
                )
            except _RETRYABLE:
                continue
        for tt in (await items(f"{base}/token-transfers", {"type": "ERC-20"}))[:40]:
            try:
                tok = tt.get("token") or {}
                contract = str(tok.get("address_hash", "")).lower()
                known = token("ethereum", contract)
                rate = v.optional_number(tok.get("exchange_rate"))
                if known is None and (not rate or tok.get("reputation") not in (None, "", "ok")):
                    continue
                total = tt.get("total") or {}
                amount = units(total.get("value"), _decimals(total.get("decimals") or tok.get("decimals")))
                if amount <= 0:
                    continue  # zero-value transfers: address-poisoning bait
                frm = str((tt.get("from") or {}).get("hash", "")).lower()
                to = str((tt.get("to") or {}).get("hash", "")).lower()
                if frm == me and to != me:
                    sign = -1.0
                elif to == me and frm != me:
                    sign = 1.0
                else:
                    continue
                leg = (
                    Leg(known.coin.id, known.coin.symbol, sign * amount)
                    if known
                    else Leg(synthetic_id("ethereum", contract), _symbol(tok.get("symbol")), sign * amount)
                )
                h = str(tt.get("transaction_hash", ""))[:80]
                cp = str(((tt.get("to") if sign < 0 else tt.get("from")) or {}).get("hash", ""))
                prev = by_hash.get(h)
                if prev:
                    prev.legs.append(leg)
                    prev.counterparty = prev.counterparty or cp
                else:
                    by_hash[h] = Transfer(w.chain, h, _iso(tt.get("timestamp")), "ok", [leg], counterparty=cp)
            except _RETRYABLE:
                continue
        # Calls that moved nothing the user holds (approvals, failed swaps …) still show: they cost a fee.
        out = [t for t in by_hash.values() if t.legs or t.fee is not None]
        return sorted(out, key=lambda t: (t.ts != 0, -t.ts))

    # ── EVM networks: JSON-RPC (native balance + known tokens) ──────────────
    async def _rpc_balance(self, w: Wallet) -> WalletBalance:
        coin = self._native(w)
        toks = [t for t in TOKENS if t.chain == w.chain]
        hexaddr = w.address.lower().removeprefix("0x")
        batch = [_rpc(0, "eth_getBalance", [w.address, "latest"])]
        batch += [
            _rpc(i + 1, "eth_call", [{"to": t.contract, "data": "0x70a08231" + "0" * 24 + hexaddr}, "latest"])
            for i, t in enumerate(toks)
        ]
        # The node must say which chain it is: a mislabelled fallback must not report another chain's balance.
        batch.append(_rpc(9999, "eth_chainId", []))
        want = EVM_CHAIN_ID[w.chain]

        async def call(url: str) -> dict[int, dict]:
            res = await self.http.post_json(url, batch)
            if not isinstance(res, list):
                raise NetError("rpc: expected a batch reply")
            m = {int(r.get("id", -1)): r for r in res if isinstance(r, dict)}
            if int(str(m.get(9999, {}).get("result", "0x0")), 16) != want:
                raise WrongChain(f"wrong chain from {urlparse(url).hostname}")
            if "result" not in m.get(0, {}):
                raise NetError("rpc: no balance")
            return m

        m = await self._first_ok(EVM_RPC[w.chain], call)
        assets = [Asset(coin.id, coin.symbol, coin.name, units(m[0]["result"], 18))]
        for i, t in enumerate(toks):
            raw = m.get(i + 1, {}).get("result")
            if not raw or "error" in m.get(i + 1, {}):
                continue
            try:
                amount = units(raw if raw != "0x" else "0x0", t.decimals)
            except _RETRYABLE:
                continue
            if amount > 0:
                assets.append(Asset(t.coin.id, t.coin.symbol, t.coin.name, amount, contract=t.contract))
        return WalletBalance(_merge(assets), time.time())

    # ── Solana: JSON-RPC + Jupiter ──────────────────────────────────────────
    async def _sol(self, method: str, params: list) -> Any:
        async def call(url: str) -> Any:
            o = await self.http.post_json(url, _rpc(1, method, params))
            if not isinstance(o, dict):
                raise NetError("rpc: expected object")
            if o.get("error"):
                raise NetError(f"solana: {str(o['error'].get('message', ''))[:80]}")
            return o.get("result")

        return await self._first_ok(SOLANA_RPC, call)

    async def _solana_balance(self, w: Wallet) -> WalletBalance:
        coin = self._native(w)
        bal = await self._sol("getBalance", [w.address])
        assets = [Asset(coin.id, coin.symbol, coin.name, units(bal["value"], 9))]
        mints: dict[str, tuple[int, int]] = {}
        for program in SPL_PROGRAMS:
            res = await self._sol(
                "getTokenAccountsByOwner", [w.address, {"programId": program}, {"encoding": "jsonParsed"}]
            )
            for acc in (res or {}).get("value") or []:
                try:
                    info = acc["account"]["data"]["parsed"]["info"]
                    ta = info["tokenAmount"]
                    raw = int(ta["amount"])
                    if raw <= 0:
                        continue
                    mint = str(info["mint"])[:50]
                    prev = mints.get(mint, (0, _decimals(ta["decimals"])))
                    mints[mint] = (prev[0] + raw, prev[1])
                except _RETRYABLE:
                    continue
        ignored = 0
        unknown = []
        for mint, (raw, dec) in mints.items():
            known = token("solana", mint)
            if known:
                assets.append(Asset(known.coin.id, known.coin.symbol, known.coin.name, units(raw, dec), contract=mint))
            else:
                unknown.append(mint)
        # Only the first mints are looked up: thousands of dust tokens must not cost thousands of requests.
        lookup = unknown[:MAX_TOKEN_LOOKUPS]
        ignored += len(unknown) - len(lookup)
        meta: dict[str, dict] = {}
        for i in range(0, len(lookup), 20):
            try:
                for m in await self.http.get_json(
                    f"{JUPITER}/tokens/v2/search", {"query": ",".join(lookup[i : i + 20])}
                ):
                    meta[str(m.get("id"))] = m
            except _RETRYABLE:
                continue
        for mint in lookup:
            m = meta.get(mint)
            raw, dec = mints[mint]
            amount = units(raw, dec)
            price = v.optional_number(m.get("usdPrice")) if m else None
            liquidity = v.optional_number(m.get("liquidity")) if m else None
            # A price is only worth something if the holding could be sold against the pools behind it.
            if (
                not m
                or not m.get("isVerified")
                or not price
                or amount * price < DUST_USD
                or not liquidity
                or liquidity < MIN_LIQUIDITY_USD
                or amount * price > liquidity
            ):
                ignored += 1
                continue
            sym = _symbol(m.get("symbol"))
            assets.append(Asset(synthetic_id("solana", mint), sym, _name(m.get("name"), sym), amount, price, mint))
        return WalletBalance(_merge(assets), time.time(), ignored=ignored)

    async def _solana_activity(self, w: Wallet) -> list[Transfer]:
        coin = self._native(w)
        sigs = await self._sol("getSignaturesForAddress", [w.address, {"limit": 15}]) or []
        out = []
        for s in sigs:
            sig = str(s.get("signature", ""))[:100]
            if not sig:
                continue
            finalized = s.get("confirmationStatus") == "finalized"
            key = f"sol:tx:{w.address}:{sig}"
            cached = self.cache.stale(key)  # a finalized transaction never changes: fetch it once, ever
            if cached:
                out.append(Transfer.from_dict(cached[1]))
                continue
            try:
                detail = await self._solana_tx(w, coin, sig)
            except _RETRYABLE:
                detail = None
            tr = detail or Transfer(w.chain, sig, 0, "ok", [])
            tr.ts = int(s.get("blockTime") or 0)
            tr.status = "failed" if s.get("err") is not None else "ok" if finalized else "pending"
            if detail and finalized:
                self.cache.put(key, tr.to_dict())
            out.append(tr)
        return out

    async def _solana_tx(self, w: Wallet, coin: Coin, sig: str) -> Transfer | None:
        tx = await self._sol("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if not tx:
            return None
        meta = tx["meta"]
        keys = [k.get("pubkey", "") for k in tx["transaction"]["message"]["accountKeys"]]
        legs = []
        fee = int(v.number(meta.get("fee", 0)))
        payer = bool(keys) and keys[0] == w.address
        other = ""
        if w.address in keys:
            i = keys.index(w.address)
            # The fee payer's change includes the fee; show the transfer and the fee apart.
            delta = meta["postBalances"][i] - meta["preBalances"][i] + (fee if payer else 0)
            if delta:
                legs.append(Leg(coin.id, coin.symbol, delta / 1e9))
            other = next((k for k in keys if k and k != w.address), "")

        def tokens(arr: list | None) -> dict[str, Decimal]:
            m: dict[str, Decimal] = {}
            for b in arr or []:
                if b.get("owner") != w.address:
                    continue
                ui = b.get("uiTokenAmount") or {}
                m[b["mint"]] = m.get(b["mint"], Decimal(0)) + Decimal(ui["amount"]).scaleb(-int(ui["decimals"]))
            return m

        pre, post = tokens(meta.get("preTokenBalances")), tokens(meta.get("postTokenBalances"))
        for mint in pre.keys() | post.keys():
            d = post.get(mint, Decimal(0)) - pre.get(mint, Decimal(0))
            if not d:
                continue
            known = token("solana", mint)
            legs.append(
                Leg(known.coin.id, known.coin.symbol, float(d))
                if known
                else Leg(synthetic_id("solana", mint), mint[:4], float(d))
            )
        return Transfer(
            w.chain,
            sig,
            int(tx.get("blockTime") or 0),
            "ok",
            legs,
            fee / 1e9 if payer else None,
            coin.symbol,
            counterparty=other,
        )

    # ── TRON: TronGrid ──────────────────────────────────────────────────────
    async def _tron_balance(self, w: Wallet) -> WalletBalance:
        coin = self._native(w)
        data = v.mapping(await self.http.get_json(f"{TRONGRID}/v1/accounts/{w.address}")).get("data") or []
        if not data:  # never received anything: not "activated", no record yet
            return WalletBalance([Asset(coin.id, coin.symbol, coin.name, 0.0)], time.time(), 0)
        acc = data[0]
        assets = [Asset(coin.id, coin.symbol, coin.name, units(acc.get("balance", 0), 6))]
        ignored = 0
        for entry in acc.get("trc20") or []:
            for contract, raw in entry.items():
                known = token("tron", contract)
                try:
                    amount = units(raw, known.decimals if known else 0)
                except _RETRYABLE:
                    continue
                if known is None:
                    ignored += amount > 0
                elif amount > 0:
                    assets.append(Asset(known.coin.id, known.coin.symbol, known.coin.name, amount, contract=contract))
        return WalletBalance(_merge(assets), time.time(), ignored=ignored)

    async def _tron_activity(self, w: Wallet) -> list[Transfer]:
        coin = self._native(w)
        out: dict[str, Transfer] = {}
        q = {"limit": 30, "only_confirmed": "true"}
        trc = v.mapping(await self.http.get_json(f"{TRONGRID}/v1/accounts/{w.address}/transactions/trc20", q))
        for t in trc.get("data") or []:
            try:
                known = token("tron", (t.get("token_info") or {}).get("address", ""))
                if known is None:
                    continue
                amount = units(t.get("value"), known.decimals)
                frm, to = t.get("from"), t.get("to")
                if amount <= 0 or frm == to or w.address not in (frm, to):
                    continue  # zero value: address-poisoning bait
                sign = -1.0 if frm == w.address else 1.0
                tid = str(t.get("transaction_id", ""))[:80]
                out[tid] = Transfer(
                    w.chain,
                    tid,
                    int(t.get("block_timestamp", 0)) // 1000,
                    "ok",
                    [Leg(known.coin.id, known.coin.symbol, sign * amount)],
                    counterparty=str(to if sign < 0 else frm),
                )
            except _RETRYABLE:
                continue
        txs = v.mapping(await self.http.get_json(f"{TRONGRID}/v1/accounts/{w.address}/transactions", q))
        for t in txs.get("data") or []:
            try:
                c = t["raw_data"]["contract"][0]
                if c.get("type") != "TransferContract":
                    continue
                val = c["parameter"]["value"]
                frm, to = (
                    addr.tron_from_hex(val.get("owner_address", "")),
                    addr.tron_from_hex(val.get("to_address", "")),
                )
                amount = units(val.get("amount"), 6)
                if not frm or not to or amount <= 0 or frm == to or w.address not in (frm, to):
                    continue
                sign = -1.0 if frm == w.address else 1.0
                ok = ((t.get("ret") or [{}])[0].get("contractRet", "SUCCESS")) == "SUCCESS"
                tid = str(t.get("txID", ""))[:80]
                out[tid] = Transfer(
                    w.chain,
                    tid,
                    int(t.get("block_timestamp", 0)) // 1000,
                    "ok" if ok else "failed",
                    [Leg(coin.id, coin.symbol, sign * amount)],
                    counterparty=to if sign < 0 else frm,
                )
            except _RETRYABLE:
                continue
        return sorted(out.values(), key=lambda t: -t.ts)

    # ── Hyperliquid: info API (HyperCore) + HyperEVM RPC ────────────────────
    async def _hl(self, kind: str, user: str, **extra: Any) -> Any:
        return await self.http.post_json(HL_INFO, {"type": kind, "user": user, **extra})

    async def _hl_balance(self, w: Wallet) -> WalletBalance:
        """Spot, perp margin and staked HYPE on HyperCore plus HYPE on HyperEVM, as one balance."""
        coin = self._native(w)
        user = w.address.lower()
        sums: dict[str, list] = {}

        def add(c: Coin, a: float | None) -> None:
            if a and a > 0:
                sums.setdefault(c.id, [c, 0.0])[1] += a

        ignored = 0
        # Spot balances: the main lookup — if this fails, the whole balance does.
        spot = v.mapping(await self._hl("spotClearinghouseState", user))
        for b in spot.get("balances") or []:
            a = v.optional_number(b.get("total"))
            c = HL_SPOT.get(str(b.get("coin")))
            if c is None:
                ignored += bool(a)
            else:
                add(c, a)

        async def perp() -> None:  # perp account value: USDC margin plus open profit/loss
            p = v.mapping(await self._hl("clearinghouseState", user))
            add(USDC, v.optional_number((p.get("marginSummary") or {}).get("accountValue")))

        async def staked() -> None:
            s = v.mapping(await self._hl("delegatorSummary", user))
            add(
                coin,
                sum(v.optional_number(s.get(k)) or 0.0 for k in ("delegated", "undelegated", "totalPendingWithdrawal")),
            )

        async def evm() -> None:  # the same address on the HyperEVM side
            r = await self.http.post_json(
                HYPER_EVM, [_rpc(0, "eth_getBalance", [w.address, "latest"]), _rpc(1, "eth_chainId", [])]
            )
            m = {int(x.get("id", -1)): x for x in r}
            if int(str(m.get(1, {}).get("result", "0x0")), 16) == 999:
                add(coin, units(m[0]["result"], 18))

        for part in await asyncio.gather(perp(), staked(), evm(), return_exceptions=True):
            if isinstance(part, BaseException) and not isinstance(part, Exception):
                raise part
        assets = [Asset(c.id, c.symbol, c.name, a) for c, a in sums.values()] or [
            Asset(coin.id, coin.symbol, coin.name, 0.0)
        ]
        return WalletBalance(assets, time.time(), ignored=ignored)

    async def _hl_activity(self, w: Wallet) -> list[Transfer]:
        me = w.address.lower()
        start = int((time.time() - 90 * 86400) * 1000)
        updates = await self._hl("userNonFundingLedgerUpdates", me, startTime=start)
        out = []
        for u in reversed((updates or [])[-40:]):
            try:
                d = u["delta"]
                ts, h = int(u.get("time", 0)) // 1000, str(u.get("hash", ""))[:80]
                frm, to = str(d.get("user", "")).lower(), str(d.get("destination", "")).lower()
                fee = v.optional_number(d.get("fee")) or None
                kind = d.get("type")
                if kind == "deposit":
                    out.append(
                        Transfer(
                            w.chain,
                            h,
                            ts,
                            "ok",
                            [Leg(USDC.id, USDC.symbol, v.number(d.get("usdc")))],
                            counterparty="Bridge",
                        )
                    )
                elif kind == "withdraw":
                    out.append(
                        Transfer(
                            w.chain,
                            h,
                            ts,
                            "ok",
                            [Leg(USDC.id, USDC.symbol, -v.number(d.get("usdc")))],
                            fee,
                            USDC.symbol,
                            counterparty="Bridge",
                        )
                    )
                elif kind in ("internalTransfer", "spotTransfer"):
                    c = USDC if kind == "internalTransfer" else HL_SPOT.get(str(d.get("token")))
                    amount = v.number(d.get("usdc") if "usdc" in d else d.get("amount"))
                    if c is None or amount <= 0 or frm == to or me not in (frm, to):
                        continue
                    sign = -1.0 if frm == me else 1.0
                    out.append(
                        Transfer(
                            w.chain,
                            h,
                            ts,
                            "ok",
                            [Leg(c.id, c.symbol, sign * amount)],
                            fee if sign < 0 else None,
                            c.symbol,
                            counterparty=str(d.get("destination") if sign < 0 else d.get("user")),
                        )
                    )
            except _RETRYABLE:
                continue
        return out
