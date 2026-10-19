"""The built-in assistant: plain-language tips from the user's own data.

Runs locally on every refresh, needs no network and no AI. Tips are about the
user's *own* plan, targets and holdings ("your plan says…", "you are 9 points
above your target"), explain what the numbers mean, and point to the page
where the user can act. They never say "buy X" or "sell Y" on their own and
never forecast prices: every tip is either arithmetic on the user's settings
or a description of the present, with the reason shown.

Titles and bodies are i18n keys; ``params`` fill their placeholders and are
already formatted by the caller-supplied :class:`Formatter`.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date

from mazecrypto.core.dca import Installment
from mazecrypto.core.insights import TechState
from mazecrypto.core.models import STABLECOINS, CoinRow
from mazecrypto.core.portfolio import OTHER, Summary, drift, split_contribution

LEVELS = ("action", "warn", "info", "good")          # display order / colour


@dataclass(frozen=True, slots=True)
class Tip:
    id: str
    level: str                      # action | warn | info | good
    title: str                      # i18n key
    body: str                       # i18n key
    params: dict = field(default_factory=dict)
    action: str = ""                # page key to open ("" = none)
    action_label: str = ""          # i18n key for the button
    priority: int = 50              # lower comes first
    extra: str = ""                 # optional second paragraph (i18n key)


@dataclass(slots=True)
class Formatter:
    money: Callable[[float], str] = lambda v: f"{v:,.2f}"
    pct: Callable[[float], str] = lambda v: f"{v:+.1f}%"
    num: Callable[[float], str] = lambda v: f"{v:.1f}"


@dataclass(slots=True)
class AdvisorInput:
    fiat: str
    rows: Sequence[CoinRow] = ()                       # tracked coins with prices
    watchlist: Sequence[str] = ()                      # coin ids
    summary: Summary | None = None
    trades: int = 0
    wallets: int = 0          # watched addresses: a portfolio built from wallets needs no trades to be "started"
    trades_without_rate: int = 0
    targets: dict[str, float] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)     # coin_id/bucket -> label
    plan_amount: float | None = None
    current: tuple[Installment, str] | None = None     # from dca.current()
    plan_exists: bool = False
    rules: int = 0
    tech: dict[str, TechState] = field(default_factory=dict)
    offline: bool = False
    stale_age: float | None = None
    today: date = field(default_factory=date.today)


DRIFT_PP = 5.0
CONCENTRATION = 70.0
BELOW_COST = 15.0
BIG_MOVE = 8.0
NEAR_ATH = 5.0

LEARN = ("dca", "avg_cost", "correlation", "volatility", "ath", "reserve", "fees", "rsi", "drawdown", "targets")


def tips(x: AdvisorInput, f: Formatter | None = None) -> list[Tip]:
    f = f or Formatter()
    out: list[Tip] = []
    s = x.summary
    held = {h.position.coin_id: h for h in s.holdings} if s else {}

    def name(cid: str) -> str:
        return x.names.get(cid) or next((r.coin.symbol for r in x.rows if r.coin.id == cid), cid)

    # ── data health ──────────────────────────────────────────────────────────
    if x.offline:
        out.append(Tip("data.offline", "warn", "tip.offline.t", "tip.offline.b", priority=5))
    elif x.stale_age is not None and x.stale_age > 900:
        out.append(Tip("data.stale", "info", "tip.stale.t", "tip.stale.b", priority=40))
    no_price = [h.position.symbol for h in held.values() if h.price_fiat is None]
    if no_price and not x.offline:
        out.append(Tip("data.no_price", "warn", "tip.no_price.t", "tip.no_price.b",
                       {"coins": ", ".join(no_price)}, priority=20))

    # ── getting started ──────────────────────────────────────────────────────
    if not x.watchlist:
        out.append(Tip("start.watch", "action", "tip.start_watch.t", "tip.start_watch.b", action="watchlist",
                       action_label="tip.btn.add_coin", priority=10))
    holding = bool(x.trades or x.wallets)
    if x.trades == 0 and x.wallets == 0:
        out.append(Tip("start.trades", "action", "tip.start_trades.t", "tip.start_trades.b", action="holdings:add",
                       action_label="tip.btn.add_trade", priority=12))
    elif not x.targets:
        out.append(Tip("start.targets", "info", "tip.start_targets.t", "tip.start_targets.b",
                       action="holdings:targets", action_label="tip.btn.set_targets", priority=30))
    if holding and not x.plan_exists:
        out.append(Tip("start.plan", "info", "tip.start_plan.t", "tip.start_plan.b", action="dca",
                       action_label="tip.btn.make_plan", priority=60))
    if holding and not x.rules:
        out.append(Tip("start.rules", "info", "tip.start_rules.t", "tip.start_rules.b", action="rules",
                       action_label="tip.btn.add_alert", priority=65))

    # ── the user's own plan ──────────────────────────────────────────────────
    if x.current:
        inst, state = x.current
        items = ", ".join(f"{it.symbol} {f.money(it.amount)}" for it in inst.items[:5])
        days = (inst.due - x.today).days
        if state in ("due", "overdue"):
            out.append(Tip(f"plan.{state}", "action", f"tip.plan_{state}.t", "tip.plan_due.b",
                           {"amount": f.money(inst.total), "items": items, "n": inst.index + 1,
                            "days": abs(days)}, action="dca", action_label="tip.btn.open_plan", priority=8))
        elif days <= 2:
            out.append(Tip("plan.soon", "info", "tip.plan_soon.t", "tip.plan_due.b",
                           {"amount": f.money(inst.total), "items": items, "n": inst.index + 1, "days": days},
                           action="dca", action_label="tip.btn.open_plan", priority=25))

    # ── balance against the user's targets ──────────────────────────────────
    if s and x.targets and s.value_fiat > 0:
        rows = drift(s.weights, x.targets, s.value_fiat)
        off = [d for d in rows if abs(d.diff_pct) > DRIFT_PP]
        if off:
            d = off[0]
            key = "tip.drift_over.b" if d.diff_pct > 0 else "tip.drift_under.b"
            params = {"coin": name(d.bucket) if d.bucket != OTHER else x.names.get(OTHER, "Other"),
                      "pp": f.num(abs(d.diff_pct)), "actual": f.num(d.actual_pct), "target": f.num(d.target_pct)}
            body2 = ""
            if x.plan_amount:
                values = {h.position.coin_id: h.value_fiat or 0.0 for h in held.values()}
                split = split_contribution(x.plan_amount, values, x.targets)
                if split:
                    top = sorted(split.items(), key=lambda kv: -kv[1])[:3]
                    params["amount"] = f.money(x.plan_amount)
                    params["split"] = ", ".join(f"{name(k)} {f.money(v)}" for k, v in top)
                    body2 = "tip.drift_split.b"
            out.append(Tip("balance.drift", "warn", "tip.drift.t", key, params, action="holdings:alloc",
                           action_label="tip.btn.see_alloc", priority=15, extra=body2))
        else:
            out.append(Tip("balance.ok", "good", "tip.balance_ok.t", "tip.balance_ok.b", {"pp": f.num(DRIFT_PP)},
                           priority=70))

    # ── risk descriptions ────────────────────────────────────────────────────
    if s and s.value_fiat > 0:
        risky = {cid: w for cid, w in s.weights.items() if held[cid].position.symbol not in STABLECOINS}
        if risky:
            top_id = max(risky, key=risky.get)
            if risky[top_id] >= CONCENTRATION and x.targets.get(top_id, 0.0) < CONCENTRATION:
                out.append(Tip("risk.concentration", "info", "tip.concentration.t", "tip.concentration.b",
                               {"coin": held[top_id].position.symbol, "pct": f.num(risky[top_id])}, priority=35))
        stable = sum(w for cid, w in s.weights.items() if held[cid].position.symbol in STABLECOINS)
        if stable == 0 and not any(k for k in x.targets if name(k) in STABLECOINS) and len(held) >= 2:
            out.append(Tip("risk.no_reserve", "info", "tip.no_reserve.t", "tip.no_reserve.b", priority=75))
        for h in held.values():
            below = h.below_cost_pct
            if below is not None and below >= BELOW_COST:
                out.append(Tip(f"risk.below_cost.{h.position.coin_id}", "info", "tip.below_cost.t",
                               "tip.below_cost.b", {"coin": h.position.symbol, "pct": f.num(below),
                                                    "avg": f.money(h.position.avg_cost_fiat or 0)}, priority=32))

    # ── market descriptions (held coins first, then watchlist) ──────────────
    order = list(held) + [c for c in x.watchlist if c not in held]
    by_id = {r.coin.id: r for r in x.rows}
    moves = 0
    for cid in order:
        r = by_id.get(cid)
        if not r:
            continue
        if r.change_24h is not None and abs(r.change_24h) >= BIG_MOVE and moves < 2 and not r.coin.is_stable:
            moves += 1
            out.append(Tip(f"market.move.{cid}", "info",
                           "tip.move_up.t" if r.change_24h > 0 else "tip.move_down.t", "tip.move.b",
                           {"coin": r.coin.symbol, "pct": f.pct(r.change_24h)}, action=f"chart:{cid}",
                           action_label="tip.btn.open_chart", priority=28 if cid in held else 45))
        if r.ath_change_pct is not None and r.ath_change_pct > -NEAR_ATH and not r.coin.is_stable:
            out.append(Tip(f"market.ath.{cid}", "info", "tip.near_ath.t", "tip.near_ath.b",
                           {"coin": r.coin.symbol, "pct": f.num(abs(r.ath_change_pct))}, priority=55))
        st = x.tech.get(cid)
        if st and st.rsi is not None and cid in held:
            if st.rsi_state == "overbought":
                out.append(Tip(f"market.hot.{cid}", "info", "tip.rsi_hot.t", "tip.rsi_hot.b",
                               {"coin": r.coin.symbol, "rsi": f.num(st.rsi)}, action=f"chart:{cid}",
                               action_label="tip.btn.open_chart", priority=50))
            elif st.rsi_state == "oversold":
                out.append(Tip(f"market.cold.{cid}", "info", "tip.rsi_cold.t", "tip.rsi_cold.b",
                               {"coin": r.coin.symbol, "rsi": f.num(st.rsi)}, action=f"chart:{cid}",
                               action_label="tip.btn.open_chart", priority=50))

    if x.trades_without_rate:
        out.append(Tip("data.no_rate", "info", "tip.no_rate.t", "tip.no_rate.b", {"n": x.trades_without_rate},
                       action="holdings:trades", action_label="tip.btn.see_trades", priority=80))

    # ── one learning card a day ─────────────────────────────────────────────
    topic = LEARN[x.today.toordinal() % len(LEARN)]
    out.append(Tip(f"learn.{topic}", "info", f"learn.{topic}.t", f"learn.{topic}.b", priority=90))

    out.sort(key=lambda t: (t.priority, LEVELS.index(t.level)))
    return out
