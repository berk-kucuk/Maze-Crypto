"""Rule conditions.

A condition looks at a :class:`RuleContext` and answers *is it true right
now?* — ``True``/``False``, or ``None`` when the data it needs is missing (the
engine then leaves the rule's state untouched instead of guessing). Every
condition round-trips through a plain dict so rules can be stored as JSON.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import ClassVar

from mazecrypto.core import indicators as ind


@dataclass(slots=True)
class RuleContext:
    prices_fiat: dict[str, float] = field(default_factory=dict)      # coin_id -> price
    prices_usd: dict[str, float] = field(default_factory=dict)
    avg_cost_fiat: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)          # coin_id -> % of portfolio
    targets: dict[str, float] = field(default_factory=dict)
    closes: dict[tuple[str, str], list[float]] = field(default_factory=dict)   # (coin_id, interval)
    fiat: str = "USD"


@dataclass(frozen=True, slots=True)
class Eval:
    active: bool | None
    value: float | None = None       # the measured number, shown in the alert
    subject: str = ""                # extra detail, e.g. which bucket drifted


@dataclass(slots=True)
class Condition:
    kind: ClassVar[str] = ""
    # Data a condition needs beyond prices: (coin_id, interval) pairs of closes.
    def needs(self) -> list[tuple[str, str]]:
        return []

    def evaluate(self, ctx: RuleContext) -> Eval:  # pragma: no cover - abstract
        raise NotImplementedError

    def to_dict(self) -> dict:
        return {"kind": self.kind, **asdict(self)}


def _cmp(value: float, op: str, threshold: float) -> bool:
    if op == "above":
        return value >= threshold
    if op == "below":
        return value <= threshold
    raise ValueError(f"unknown operator {op!r}")


@dataclass(slots=True)
class PriceCondition(Condition):
    """Price crosses a fixed level."""

    kind: ClassVar[str] = "price"
    coin_id: str = ""
    symbol: str = ""
    op: str = "below"
    value: float = 0.0
    currency: str = "fiat"           # "fiat" (portfolio currency) or "usd"

    def evaluate(self, ctx: RuleContext) -> Eval:
        src = ctx.prices_usd if self.currency == "usd" else ctx.prices_fiat
        p = src.get(self.coin_id)
        return Eval(None) if p is None else Eval(_cmp(p, self.op, self.value), p)


@dataclass(slots=True)
class CostChangeCondition(Condition):
    """Price relative to the user's average cost: -15 = 15 % under cost, +50 = 50 % over."""

    kind: ClassVar[str] = "cost_change"
    coin_id: str = ""
    symbol: str = ""
    pct: float = -15.0

    def evaluate(self, ctx: RuleContext) -> Eval:
        p, avg = ctx.prices_fiat.get(self.coin_id), ctx.avg_cost_fiat.get(self.coin_id)
        if p is None or not avg:
            return Eval(None)
        change = (p / avg - 1.0) * 100.0
        active = change <= self.pct if self.pct < 0 else change >= self.pct
        return Eval(active, change)


@dataclass(slots=True)
class DriftCondition(Condition):
    """A target bucket is more than ``pct`` percentage points off its target."""

    kind: ClassVar[str] = "drift"
    pct: float = 5.0
    coin_id: str = ""                # empty = any bucket

    def evaluate(self, ctx: RuleContext) -> Eval:
        from mazecrypto.core.portfolio import bucket_weights

        if not ctx.targets or not ctx.weights:
            return Eval(None)
        actual = bucket_weights(ctx.weights, ctx.targets)
        diffs = {k: actual.get(k, 0.0) - ctx.targets.get(k, 0.0) for k in set(actual) | set(ctx.targets)}
        if self.coin_id:
            diffs = {self.coin_id: diffs.get(self.coin_id, 0.0)}
        worst = max(diffs, key=lambda k: abs(diffs[k]))
        return Eval(abs(diffs[worst]) > self.pct, diffs[worst], worst)


@dataclass(slots=True)
class RsiCondition(Condition):
    kind: ClassVar[str] = "rsi"
    coin_id: str = ""
    symbol: str = ""
    interval: str = "1d"
    op: str = "below"
    value: float = 30.0
    period: int = 14

    def needs(self) -> list[tuple[str, str]]:
        return [(self.coin_id, self.interval)]

    def evaluate(self, ctx: RuleContext) -> Eval:
        closes = ctx.closes.get((self.coin_id, self.interval))
        if not closes:
            return Eval(None)
        r = ind.last(ind.rsi(closes, self.period))
        return Eval(None) if r is None else Eval(_cmp(r, self.op, self.value), r)


@dataclass(slots=True)
class SmaCrossCondition(Condition):
    """Fast SMA above slow SMA (``golden``) or below it (``death``).

    The engine fires on the transition, which is exactly the cross.
    """

    kind: ClassVar[str] = "sma_cross"
    coin_id: str = ""
    symbol: str = ""
    interval: str = "1d"
    fast: int = 50
    slow: int = 200
    direction: str = "golden"

    def needs(self) -> list[tuple[str, str]]:
        return [(self.coin_id, self.interval)]

    def evaluate(self, ctx: RuleContext) -> Eval:
        closes = ctx.closes.get((self.coin_id, self.interval))
        if not closes:
            return Eval(None)
        f, s = ind.last(ind.sma(closes, self.fast)), ind.last(ind.sma(closes, self.slow))
        if f is None or s is None:
            return Eval(None)
        above = f > s
        return Eval(above if self.direction == "golden" else not above, (f / s - 1) * 100)


@dataclass(slots=True)
class MoveCondition(Condition):
    """Price moved at least ``pct`` percent within the last ``hours`` hours."""

    kind: ClassVar[str] = "move"
    coin_id: str = ""
    symbol: str = ""
    hours: int = 24
    pct: float = 10.0
    direction: str = "any"           # any / up / down

    def needs(self) -> list[tuple[str, str]]:
        return [(self.coin_id, "1h")]

    def evaluate(self, ctx: RuleContext) -> Eval:
        closes = ctx.closes.get((self.coin_id, "1h"))
        if not closes:
            return Eval(None)
        ch = ind.pct_change(closes, self.hours)
        if ch is None:
            return Eval(None)
        if self.direction == "up":
            return Eval(ch >= self.pct, ch)
        if self.direction == "down":
            return Eval(ch <= -self.pct, ch)
        return Eval(abs(ch) >= self.pct, ch)


CONDITIONS: dict[str, type[Condition]] = {c.kind: c for c in (
    PriceCondition, CostChangeCondition, DriftCondition, RsiCondition, SmaCrossCondition, MoveCondition)}


def condition_from_dict(d: dict) -> Condition:
    d = dict(d)
    cls = CONDITIONS.get(d.pop("kind", ""))
    if cls is None:
        raise ValueError("unknown condition kind")
    fields = cls.__dataclass_fields__
    return cls(**{k: type(fields[k].default)(v) for k, v in d.items() if k in fields})
