"""Rule evaluation with edge triggering and cooldown.

A rule fires when its condition *becomes* true (false/unknown -> true), not on
every refresh while it stays true, and never twice within its cooldown. So a
price hovering around a threshold produces one notification, not one a minute.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from mazecrypto.core.rules.conditions import Condition, RuleContext, condition_from_dict


@dataclass(slots=True)
class Rule:
    name: str
    condition: Condition
    message: str = ""                # user's own note, e.g. "use half of the reserve"
    enabled: bool = True
    cooldown_hours: float = 24.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "condition": self.condition.to_dict(),
                "message": self.message, "enabled": self.enabled, "cooldown_hours": self.cooldown_hours}

    @classmethod
    def from_dict(cls, d: dict) -> Rule:
        return cls(str(d["name"]), condition_from_dict(d["condition"]), str(d.get("message", "")),
                   bool(d.get("enabled", True)), float(d.get("cooldown_hours", 24.0)),
                   str(d.get("id") or uuid.uuid4().hex[:12]))


@dataclass(slots=True)
class RuleState:
    active: bool = False
    last_fired: float = 0.0          # 0 = never fired

    def to_dict(self) -> dict:
        return {"active": self.active, "last_fired": self.last_fired}

    @classmethod
    def from_dict(cls, d: dict) -> RuleState:
        return cls(bool(d.get("active", False)), float(d.get("last_fired", 0.0)))


@dataclass(frozen=True, slots=True)
class Alert:
    rule: Rule
    value: float | None
    subject: str
    ts: float


def evaluate(rules: list[Rule], ctx: RuleContext, state: dict[str, RuleState], now: float) -> list[Alert]:
    """Evaluate all enabled rules, update ``state`` in place, return new alerts."""
    alerts: list[Alert] = []
    for rule in rules:
        st = state.setdefault(rule.id, RuleState())
        if not rule.enabled:
            st.active = False
            continue
        try:
            res = rule.condition.evaluate(ctx)
        except (ValueError, ZeroDivisionError):
            continue
        if res.active is None:
            continue
        cooled = not st.last_fired or now - st.last_fired >= rule.cooldown_hours * 3600
        if res.active and not st.active and cooled:
            st.last_fired = now
            alerts.append(Alert(rule, res.value, res.subject, now))
        st.active = res.active
    return alerts


def data_needs(rules: list[Rule]) -> set[tuple[str, str]]:
    """Every (coin_id, interval) close series the enabled rules need."""
    return {n for r in rules if r.enabled for n in r.condition.needs()}
