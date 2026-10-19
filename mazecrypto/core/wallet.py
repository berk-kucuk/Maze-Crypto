"""Watched wallet addresses, read-only, and how they feed the portfolio.

Nothing here can sign or move funds: there is no key material anywhere in
the app. Two chains have no public balance lookup and take the amount from
the user instead: ``monero`` (balances are private by design) and ``manual``
(any coin — an exchange account, a coin without a supported explorer).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from mazecrypto.core.portfolio import Position

MANUAL_CHAINS = ("monero", "manual")


@dataclass(slots=True)
class Wallet:
    id: str
    chain: str
    address: str
    label: str = ""
    coin_id: str = ""  # manual / monero: the coin
    symbol: str = ""
    name: str = ""
    amount: float | None = None  # manual / monero: the entered balance
    in_portfolio: bool = True  # False = watch only (someone else's address …)
    created: int = 0

    @property
    def manual(self) -> bool:
        return self.chain in MANUAL_CHAINS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Wallet:
        amount = d.get("amount")
        return cls(
            str(d["id"]),
            str(d["chain"]),
            str(d.get("address", "")),
            str(d.get("label", "")),
            str(d.get("coin_id", "")),
            str(d.get("symbol", "")),
            str(d.get("name", "")),
            None if amount is None else float(amount),
            bool(d.get("in_portfolio", True)),
            int(d.get("created", 0)),
        )


def is_synthetic(coin_id: str) -> bool:
    """Tokens only a chain's indexer can price get ids starting with ``x-``: never sent to CoinGecko."""
    return coin_id.startswith("x-")


@dataclass(slots=True)
class Asset:
    coin_id: str
    symbol: str
    name: str
    amount: float
    price_usd: float | None = None  # only for synthetic ids
    contract: str = ""
    pending: float = 0.0  # unconfirmed part of ``amount`` (UTXO mempool)


@dataclass(slots=True)
class WalletBalance:
    assets: list[Asset]
    ts: float
    tx_count: int | None = None
    ignored: int = 0  # tokens left out: unknown, unpriced, scam, no real market, dust

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> WalletBalance:
        return cls([Asset(**a) for a in d["assets"]], float(d["ts"]), d.get("tx_count"), int(d.get("ignored", 0)))


@dataclass(slots=True)
class Leg:
    """How one transaction changed one coin's balance: positive = received."""

    coin_id: str
    symbol: str
    amount: float


@dataclass(slots=True)
class Transfer:
    """A transaction as seen from the user's addresses.

    ``ts`` is 0 while unconfirmed. ``fee`` is what the user paid (only when one
    of their addresses sent it). ``fee_included``: the legs already contain the
    fee (UTXO chains report the net change of the address).
    """

    chain: str
    txid: str
    ts: int
    status: str  # ok, failed, pending
    legs: list[Leg]
    fee: float | None = None
    fee_symbol: str = ""
    fee_included: bool = False
    counterparty: str = ""
    wallet_ids: set[str] = field(default_factory=set)

    @property
    def direction(self) -> str:
        """in, out, self (between the user's own addresses, or only a fee) or swap."""
        if not self.legs:
            return "self"
        ins = any(leg.amount > 0 for leg in self.legs)
        outs = any(leg.amount < 0 for leg in self.legs)
        if ins and outs:
            return "swap"
        if (
            outs
            and self.fee_included
            and self.fee is not None
            and len(self.legs) == 1
            and abs(self.legs[0].amount) <= self.fee * 1.000001
        ):
            return "self"
        return "in" if ins else "out"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["wallet_ids"] = sorted(self.wallet_ids)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Transfer:
        return cls(
            d["chain"],
            d["txid"],
            int(d["ts"]),
            d["status"],
            [Leg(**leg) for leg in d["legs"]],
            d.get("fee"),
            d.get("fee_symbol", ""),
            bool(d.get("fee_included")),
            d.get("counterparty", ""),
            set(d.get("wallet_ids") or ()),
        )


def merge_activity(lists: Iterable[Iterable[Transfer]], limit: int = 100) -> list[Transfer]:
    """One feed from several addresses.

    A transaction touching two of the user's addresses on the same chain shows
    once, with the changes summed — moving coins between one's own wallets is a
    transfer to self, not a send plus a receive. Newest first, unconfirmed on top.
    """
    by_tx: dict[str, Transfer] = {}
    for lst in lists:
        for t in lst:
            k = f"{t.chain}:{t.txid}"
            prev = by_tx.get(k)
            if prev is None:
                by_tx[k] = replace(t, legs=list(t.legs), wallet_ids=set(t.wallet_ids))
                continue
            sums: dict[str, Leg] = {}
            for leg in prev.legs + t.legs:
                s = sums.get(leg.coin_id)
                sums[leg.coin_id] = Leg(leg.coin_id, leg.symbol, leg.amount + (s.amount if s else 0.0))
            by_tx[k] = replace(
                prev,
                legs=[leg for leg in sums.values() if abs(leg.amount) > 1e-12],
                fee=prev.fee if prev.fee is not None else t.fee,
                counterparty=prev.counterparty if any(leg.amount < 0 for leg in prev.legs) else t.counterparty,
                wallet_ids=prev.wallet_ids | t.wallet_ids,
                status="pending" if "pending" in (prev.status, t.status) else prev.status,
            )
    out = sorted(by_tx.values(), key=lambda t: (t.ts != 0, -t.ts))
    return out[:limit]


def merge_wallets(trades: Mapping[str, Position], held: Mapping[str, tuple[str, float]]) -> dict[str, Position]:
    """Wallet balances become the portfolio's amounts.

    For every coin held in a wallet the amount is the on-chain (or entered)
    total. Its cost basis comes from the trades, if any: their average cost
    times the wallet amount — recorded buys still give profit/loss, while a coin
    never recorded shows its value with an unknown cost instead of a made-up
    profit. Coins only in trades stay as they are.
    """
    out = {k: p for k, p in trades.items() if k not in held}
    for cid, (sym, amount) in held.items():
        t = trades.get(cid)
        p = Position(cid, t.symbol if t else sym, amount=amount, source="wallet")
        avg = t.avg_cost_fiat if t else None
        if t is not None and avg is not None:
            p.cost_fiat = avg * amount
            p.cost_usd = None if t.avg_cost_usd is None else t.avg_cost_usd * amount
        else:
            p.cost_fiat, p.cost_usd, p.cost_known = 0.0, None, False
        if t is not None:
            p.realized_fiat, p.realized_usd, p.fees_fiat, p.trades = (
                t.realized_fiat,
                t.realized_usd,
                t.fees_fiat,
                t.trades,
            )
        out[cid] = p
    return out
