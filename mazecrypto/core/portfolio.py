"""Positions, average cost, profit/loss and target-allocation drift.

Cost basis uses the average-cost method: a sell removes the sold fraction of
the basis and books the difference as realised profit/loss.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from mazecrypto.core.models import Trade

OTHER = "*other*"   # target bucket for every coin without a target of its own
EPS = 1e-12


@dataclass(slots=True)
class Position:
    coin_id: str
    symbol: str
    amount: float = 0.0
    cost_fiat: float = 0.0          # remaining basis in the portfolio currency
    cost_usd: float | None = 0.0    # None once any trade lacks a USD rate
    realized_fiat: float = 0.0
    realized_usd: float | None = 0.0
    fees_fiat: float = 0.0
    trades: int = 0

    @property
    def avg_cost_fiat(self) -> float | None:
        return self.cost_fiat / self.amount if self.amount > EPS else None

    @property
    def avg_cost_usd(self) -> float | None:
        if self.cost_usd is None or self.amount <= EPS:
            return None
        return self.cost_usd / self.amount


def _to_base(amount: float, trade: Trade, base: str, base_per_usd: float | None) -> float:
    """An amount in the trade's currency expressed in the portfolio currency."""
    if trade.fiat == base:
        return amount
    if trade.usd_rate and base_per_usd:
        # Different currency: go through USD at the trade's own rate.
        return amount / trade.usd_rate * base_per_usd
    raise ValueError(f"cannot convert {trade.fiat} trade to {base}")


def build_positions(trades: Iterable[Trade], base: str,
                    base_per_usd: float | None = None) -> dict[str, Position]:
    """Replay trades in time order into one :class:`Position` per coin."""
    out: dict[str, Position] = {}
    for t in sorted(trades, key=lambda t: (t.ts, t.id or 0)):
        p = out.setdefault(t.coin_id, Position(t.coin_id, t.symbol))
        p.trades += 1
        total = _to_base(t.total_cost, t, base, base_per_usd)
        total_usd = t.total_cost / t.usd_rate if t.usd_rate else None
        p.fees_fiat += _to_base(t.fee, t, base, base_per_usd)
        if t.side == "buy":
            p.amount += t.amount
            p.cost_fiat += total
            if p.cost_usd is not None:
                p.cost_usd = None if total_usd is None else p.cost_usd + total_usd
        else:
            if p.amount <= EPS or t.amount > p.amount * (1 + 1e-9) + EPS:
                raise ValueError(f"sell of {t.symbol} exceeds the position")
            qty = min(t.amount, p.amount)
            frac = qty / p.amount
            basis = p.cost_fiat * frac
            p.realized_fiat += total - basis
            p.cost_fiat -= basis
            if p.cost_usd is not None and total_usd is not None and p.realized_usd is not None:
                basis_usd = p.cost_usd * frac
                p.realized_usd += total_usd - basis_usd
                p.cost_usd -= basis_usd
            else:
                p.cost_usd = p.realized_usd = None
            p.amount -= qty
            if p.amount <= EPS:
                p.amount, p.cost_fiat = 0.0, 0.0
                if p.cost_usd is not None:
                    p.cost_usd = 0.0
    return out


@dataclass(slots=True)
class Holding:
    position: Position
    price_fiat: float | None
    price_usd: float | None

    @property
    def value_fiat(self) -> float | None:
        return None if self.price_fiat is None else self.position.amount * self.price_fiat

    @property
    def value_usd(self) -> float | None:
        return None if self.price_usd is None else self.position.amount * self.price_usd

    @property
    def pnl_fiat(self) -> float | None:
        v = self.value_fiat
        return None if v is None else v - self.position.cost_fiat

    @property
    def pnl_usd(self) -> float | None:
        v, c = self.value_usd, self.position.cost_usd
        return None if v is None or c is None else v - c

    @property
    def pnl_pct(self) -> float | None:
        p, c = self.pnl_fiat, self.position.cost_fiat
        return None if p is None or c <= EPS else p / c * 100.0

    @property
    def below_cost_pct(self) -> float | None:
        """How far the price is under the average cost, in percent (negative = above)."""
        avg = self.position.avg_cost_fiat
        if avg is None or self.price_fiat is None:
            return None
        return (1.0 - self.price_fiat / avg) * 100.0


@dataclass(slots=True)
class Summary:
    holdings: list[Holding]
    value_fiat: float
    cost_fiat: float
    value_usd: float | None
    cost_usd: float | None
    realized_fiat: float
    weights: dict[str, float] = field(default_factory=dict)   # coin_id -> percent of value

    @property
    def pnl_fiat(self) -> float:
        return self.value_fiat - self.cost_fiat

    @property
    def pnl_pct(self) -> float | None:
        return self.pnl_fiat / self.cost_fiat * 100.0 if self.cost_fiat > EPS else None

    @property
    def pnl_usd(self) -> float | None:
        if self.value_usd is None or self.cost_usd is None:
            return None
        return self.value_usd - self.cost_usd


def summarize(positions: Mapping[str, Position], prices_fiat: Mapping[str, float],
              prices_usd: Mapping[str, float]) -> Summary:
    holdings = [Holding(p, prices_fiat.get(cid), prices_usd.get(cid))
                for cid, p in positions.items() if p.amount > EPS]
    value = sum(h.value_fiat or 0.0 for h in holdings)
    cost = sum(h.position.cost_fiat for h in holdings)
    usd_vals = [h.value_usd for h in holdings]
    usd_costs = [h.position.cost_usd for h in holdings]
    value_usd = None if any(v is None for v in usd_vals) else sum(usd_vals)  # type: ignore[arg-type]
    cost_usd = None if any(c is None for c in usd_costs) else sum(usd_costs)  # type: ignore[arg-type]
    weights = {h.position.coin_id: (h.value_fiat or 0.0) / value * 100.0 for h in holdings} if value > 0 else {}
    holdings.sort(key=lambda h: h.value_fiat or 0.0, reverse=True)
    return Summary(holdings, value, cost, value_usd, cost_usd,
                   sum(p.realized_fiat for p in positions.values()), weights)


# ── target allocation ──────────────────────────────────────────────────────


def validate_targets(targets: Mapping[str, float]) -> dict[str, float]:
    clean = {k: float(v) for k, v in targets.items() if float(v) > 0}
    if any(v > 100 for v in clean.values()):
        raise ValueError("a target above 100%")
    if sum(clean.values()) > 100.0 + 1e-6:
        raise ValueError("targets add up to more than 100%")
    return clean


def bucket_weights(weights: Mapping[str, float], targets: Mapping[str, float]) -> dict[str, float]:
    """Fold coins without their own target into the OTHER bucket."""
    out: dict[str, float] = dict.fromkeys(targets, 0.0)
    for cid, w in weights.items():
        key = cid if cid in targets else OTHER
        out[key] = out.get(key, 0.0) + w
    return out


@dataclass(frozen=True, slots=True)
class Drift:
    bucket: str
    target_pct: float
    actual_pct: float
    rebalance_fiat: float          # >0: buy this much to hit target, <0: over by this much

    @property
    def diff_pct(self) -> float:
        return self.actual_pct - self.target_pct


def drift(weights: Mapping[str, float], targets: Mapping[str, float], total_value: float) -> list[Drift]:
    """Actual vs target per bucket, largest absolute deviation first."""
    if not targets:
        return []
    actual = bucket_weights(weights, targets)
    keys = set(targets) | set(actual)
    rows = [Drift(k, targets.get(k, 0.0), actual.get(k, 0.0),
                  (targets.get(k, 0.0) - actual.get(k, 0.0)) / 100.0 * total_value) for k in keys]
    rows.sort(key=lambda d: (-abs(d.diff_pct), d.bucket))      # stable order on ties
    return rows


def split_contribution(amount: float, values: Mapping[str, float],
                       targets: Mapping[str, float]) -> dict[str, float]:
    """Split new money so the portfolio moves toward its targets without selling.

    Underweight buckets get money in proportion to how far they are below
    target after the contribution; if every bucket is already at target the
    amount is split by target weight. Pure arithmetic, not a recommendation.
    """
    if amount <= 0 or not targets:
        return {}
    bucketed: dict[str, float] = dict.fromkeys(targets, 0.0)
    for cid, v in values.items():
        key = cid if cid in targets else OTHER
        bucketed[key] = bucketed.get(key, 0.0) + v
    total_after = sum(bucketed.values()) + amount
    tsum = sum(targets.values())
    deficit = {k: max(0.0, targets[k] / tsum * total_after - bucketed.get(k, 0.0)) for k in targets}
    dsum = sum(deficit.values())
    if dsum <= EPS:
        return {k: amount * targets[k] / tsum for k in targets}
    return {k: amount * d / dsum for k, d in deficit.items() if d > EPS}
