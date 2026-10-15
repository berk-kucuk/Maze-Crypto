"""Recurring-purchase (DCA) plans: schedule, progress and "this period" card."""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

from mazecrypto.core.portfolio import split_contribution


class Frequency(StrEnum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


@dataclass(slots=True)
class DcaExtra:
    """A one-off purchase added to one period (e.g. a first-week reserve buy)."""

    period: int
    coin_id: str
    symbol: str
    amount: float

    def to_dict(self) -> dict:
        return {"period": self.period, "coin_id": self.coin_id, "symbol": self.symbol, "amount": self.amount}

    @classmethod
    def from_dict(cls, d: dict) -> DcaExtra:
        return cls(int(d["period"]), str(d["coin_id"]), str(d["symbol"]), float(d["amount"]))


@dataclass(slots=True)
class DcaPlan:
    name: str
    start: date
    frequency: Frequency
    periods: int
    amount: float
    fiat: str
    allocation: dict[str, float]                 # coin_id -> percent of each period's amount
    symbols: dict[str, str] = field(default_factory=dict)
    extras: list[DcaExtra] = field(default_factory=list)
    # Split each period toward the portfolio's target allocation instead of
    # the fixed percentages above.
    follow_targets: bool = False

    def validate(self) -> None:
        if self.periods < 1 or self.periods > 520:
            raise ValueError("periods must be 1..520")
        if self.amount < 0:
            raise ValueError("amount must not be negative")
        if not self.follow_targets:
            total = sum(self.allocation.values())
            if self.amount > 0 and abs(total - 100.0) > 0.01:
                raise ValueError("allocation must add up to 100%")
        for e in self.extras:
            if not 0 <= e.period < self.periods or e.amount <= 0:
                raise ValueError("invalid extra purchase")

    def to_dict(self) -> dict:
        return {"name": self.name, "start": self.start.isoformat(), "frequency": self.frequency.value,
                "periods": self.periods, "amount": self.amount, "fiat": self.fiat,
                "allocation": self.allocation, "symbols": self.symbols,
                "extras": [e.to_dict() for e in self.extras], "follow_targets": self.follow_targets}

    @classmethod
    def from_dict(cls, d: dict) -> DcaPlan:
        return cls(str(d["name"]), date.fromisoformat(d["start"]), Frequency(d["frequency"]),
                   int(d["periods"]), float(d["amount"]), str(d["fiat"]),
                   {str(k): float(v) for k, v in d.get("allocation", {}).items()},
                   {str(k): str(v) for k, v in d.get("symbols", {}).items()},
                   [DcaExtra.from_dict(e) for e in d.get("extras", [])],
                   bool(d.get("follow_targets", False)))


@dataclass(frozen=True, slots=True)
class DcaItem:
    coin_id: str
    symbol: str
    amount: float


@dataclass(frozen=True, slots=True)
class Installment:
    index: int
    due: date
    items: tuple[DcaItem, ...]

    @property
    def total(self) -> float:
        return sum(i.amount for i in self.items)


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def due_date(plan: DcaPlan, index: int) -> date:
    if plan.frequency is Frequency.WEEKLY:
        return plan.start + timedelta(weeks=index)
    if plan.frequency is Frequency.BIWEEKLY:
        return plan.start + timedelta(weeks=2 * index)
    return add_months(plan.start, index)


def _items(plan: DcaPlan, index: int, values: dict[str, float] | None,
           targets: dict[str, float] | None) -> tuple[DcaItem, ...]:
    if plan.follow_targets and targets:
        split = split_contribution(plan.amount, values or {}, targets)
    else:
        split = {cid: plan.amount * pct / 100.0 for cid, pct in plan.allocation.items() if pct > 0}
    merged: dict[str, float] = dict(split)
    symbols = {e.coin_id: e.symbol for e in plan.extras} | plan.symbols
    for e in plan.extras:
        if e.period == index:
            merged[e.coin_id] = merged.get(e.coin_id, 0.0) + e.amount
    return tuple(DcaItem(cid, symbols.get(cid, cid.upper()), amt)
                 for cid, amt in sorted(merged.items(), key=lambda kv: -kv[1]) if amt > 0)


def schedule(plan: DcaPlan, values: dict[str, float] | None = None,
             targets: dict[str, float] | None = None) -> list[Installment]:
    return [Installment(i, due_date(plan, i), _items(plan, i, values, targets)) for i in range(plan.periods)]


@dataclass(frozen=True, slots=True)
class Progress:
    done: int
    total: int
    invested: float
    planned: float

    @property
    def remaining(self) -> float:
        return self.planned - self.invested


def progress(installments: list[Installment], done: set[int]) -> Progress:
    return Progress(len([i for i in installments if i.index in done]), len(installments),
                    sum(i.total for i in installments if i.index in done),
                    sum(i.total for i in installments))


def current(installments: list[Installment], done: set[int], today: date) -> tuple[Installment, str] | None:
    """The installment to show on the "this period" card and its status.

    Status is ``overdue`` (an earlier period was skipped), ``due`` (falls in the
    current period) or ``upcoming``. ``None`` means the plan is finished.
    """
    pending = [i for i in installments if i.index not in done]
    if not pending:
        return None
    first = pending[0]
    nxt = next((i for i in installments if i.index == first.index + 1), None)
    if first.due > today:
        return first, "upcoming"
    if nxt is not None and nxt.due <= today:
        return first, "overdue"
    return first, "due"
