"""Glue between the Qt widgets and the Qt-free core/providers.

Network work runs as asyncio tasks on the qasync loop, so the UI thread never
blocks on I/O; results come back as Qt signals.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import sys
import time
import traceback
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from mazecrypto import i18n
from mazecrypto.core import address as addr
from mazecrypto.core import dca as dca_mod
from mazecrypto.core import settings as cfg
from mazecrypto.core.advisor import AdvisorInput, Formatter, Tip
from mazecrypto.core.advisor import tips as make_tips
from mazecrypto.core.candles import closes
from mazecrypto.core.dca import DcaPlan
from mazecrypto.core.insights import TechState, tech_state
from mazecrypto.core.models import Coin, Interval
from mazecrypto.core.portfolio import Position, Summary, build_positions, summarize
from mazecrypto.core.rules import Alert, Rule, RuleContext, data_needs, evaluate
from mazecrypto.core.store import Cache, ProfileStore
from mazecrypto.core.wallet import Transfer, Wallet, WalletBalance, is_synthetic, merge_activity, merge_wallets
from mazecrypto.providers import wallets as wallets_mod
from mazecrypto.providers.ai import AiUnavailable, LocalAI
from mazecrypto.providers.service import MarketService, Snapshot
from mazecrypto.ui import fmt
from mazecrypto.ui.notify import Notifier

_T0 = time.monotonic()
DEBUG = bool(os.environ.get("MAZE_CRYPTO_DEBUG"))


def debug(msg: str) -> None:
    if DEBUG:
        print(f"[maze-crypto {time.monotonic() - _T0:7.2f}s] {msg}", file=sys.stderr, flush=True)


class Controller(QObject):
    snapshotReady = pyqtSignal(object)        # Snapshot (prices)
    enriched = pyqtSignal(object)             # Snapshot (+ correlation, sparklines)
    status = pyqtSignal(str, str)             # state (busy/live/stale/offline), text
    portfolioChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    alerts = pyqtSignal(list)                 # list[Alert]
    tipsChanged = pyqtSignal(list)            # list[Tip]
    aiChanged = pyqtSignal(bool)              # local AI available / not
    walletsChanged = pyqtSignal()             # the list of watched addresses or their balances

    def __init__(self, settings: cfg.Settings):
        super().__init__()
        self.settings = settings
        self.cache = Cache(cfg.cache_dir() / "cache.db")
        self.store = ProfileStore(cfg.data_dir() / "profiles" / f"{settings.profile}.db")
        self.service = MarketService(settings, self.cache)
        self.notifier = Notifier()
        self.snap: Snapshot | None = None
        self.alert_log: list[Alert] = []
        self.tips: list[Tip] = []
        self.tech: dict[str, TechState] = {}
        self.ai: LocalAI | None = None
        self.ai_model = ""
        # Last balance of every watched address, by wallet id: (balance or None, stale age, error).
        self.wallet_data: dict[str, tuple[WalletBalance | None, float | None, str | None]] = {}
        self.wallet_loading: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._refreshing = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self._age = QTimer(self)                 # keeps the "stale 4m" label honest
        self._age.timeout.connect(self._emit_status)
        self._age.start(15_000)
        self.snapshotReady.connect(lambda _s: self.update_tips())
        self.enriched.connect(self._enriched)
        self.portfolioChanged.connect(self.update_tips)
        self.walletsChanged.connect(self.update_tips)

    # ── async plumbing ───────────────────────────────────────────────────────
    def run(self, coro: Awaitable[Any], done: Callable[[Any], None] | None = None,
            fail: Callable[[Exception], None] | None = None) -> asyncio.Task:
        async def wrapper() -> None:
            try:
                result = await coro
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - surfaced to the caller / log, never crashes the UI
                if fail:
                    fail(e)
                else:
                    traceback.print_exc()
                return
            if done:
                done(result)

        task = asyncio.ensure_future(wrapper())

        def close_unstarted(t: asyncio.Task) -> None:
            # Cancelled before its first step: close the inner coroutine so Python
            # does not warn that it was never awaited.
            if t.cancelled() and inspect.iscoroutine(coro):
                coro.close()

        task.add_done_callback(close_unstarted)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def shutdown(self) -> None:
        self.timer.stop()
        self._age.stop()
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        # Wait for the cancellations to land: an in-flight httpx request left
        # behind would otherwise run its cleanup after the loop has stopped.
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await self.service.close()
        if self.ai:
            with contextlib.suppress(Exception):
                await self.ai.close()
        self.store.close()
        self.cache.close()

    # ── refresh cycle ────────────────────────────────────────────────────────
    @property
    def expert(self) -> bool:
        return self.settings.expert

    def start(self) -> None:
        self.timer.start(self.settings.refresh_seconds * 1000)
        self.run(self._show_cached())
        self.refresh()
        self.check_ai()

    async def _show_cached(self) -> None:
        """Paint the last known data immediately; the network refresh replaces it."""
        await self._refresh_wallets(cache_only=True)
        snap = await self.service.refresh(self.tracked_coins(), cache_only=True)
        if self.snap is None and any(r.price_fiat is not None for r in snap.rows):
            snap.offline = False        # not offline, just not refreshed yet
            snap.stale_age = snap.stale_age or 0.0
            self.snap = snap
            debug("cached snapshot shown")
            self.snapshotReady.emit(snap)

    def tracked_coins(self) -> list[Coin]:
        """Watchlist plus every coin the portfolio, targets or DCA plan refers to."""
        coins = {c.id: c for c in self.settings.watchlist}
        for t in self.store.trades():
            coins.setdefault(t.coin_id, Coin(t.coin_id, t.symbol, t.symbol))
        for cid, sym in self.store.target_symbols().items():
            coins.setdefault(cid, Coin(cid, sym, sym))
        plan = self.store.dca_plan()
        if plan:
            for cid, sym in plan.symbols.items():
                coins.setdefault(cid, Coin(cid, sym, sym))
            for e in plan.extras:
                coins.setdefault(e.coin_id, Coin(e.coin_id, e.symbol, e.symbol))
        for r in self.store.rules():
            cid, sym = getattr(r.condition, "coin_id", ""), getattr(r.condition, "symbol", "")
            if cid and sym:
                coins.setdefault(cid, Coin(cid, sym, sym))
        # Wallet coins: the chain's own coin up front, so the first balance already has a price.
        for w in self.wallets():
            if w.manual:
                if w.coin_id:
                    coins.setdefault(w.coin_id, Coin(w.coin_id, w.symbol, w.name or w.symbol))
                continue
            info = wallets_mod.chain(w.chain)
            if info and info.native:
                coins.setdefault(info.native.id, info.native)
            bal = self.wallet_data.get(w.id, (None, None, None))[0]
            for a in bal.assets if bal else ():
                if not is_synthetic(a.coin_id) and a.amount > 0:
                    coins.setdefault(a.coin_id, Coin(a.coin_id, a.symbol, a.name))
        return list(coins.values())

    def refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        self.status.emit("busy", i18n.t("status.updating"))
        self.run(self._refresh(), fail=self._refresh_failed)

    async def _refresh(self) -> None:
        try:
            # Balances and prices in parallel; prices are shown as soon as they arrive.
            wallet_job = asyncio.ensure_future(self._refresh_wallets())
            snap = await self.service.refresh(self.tracked_coins())
            self.snap = snap
            self.snapshotReady.emit(snap)
            with contextlib.suppress(Exception):
                await wallet_job
            have = {r.coin.id for r in snap.rows}
            if any(c.id not in have for c in self.tracked_coins()):
                snap = await self.service.refresh(self.tracked_coins())     # a wallet turned up a new coin
                self.snap = snap
            debug(f"prices: {sum(r.price_fiat is not None for r in snap.rows)}/{len(snap.rows)} coins, "
                  f"offline={snap.offline}, stale_age={snap.stale_age and round(snap.stale_age)}, "
                  f"requests={self.service.http.requests}, errors={snap.errors[:3]}")
            self.snapshotReady.emit(snap)
            self._emit_status()
            await self._evaluate_rules()
            await self.service.enrich(snap)
            debug("enriched (correlation, sparklines)")
            self.enriched.emit(snap)
        finally:
            self._refreshing = False

    def _refresh_failed(self, e: Exception) -> None:
        traceback.print_exception(e)
        self.status.emit("offline", i18n.t("status.offline"))

    def _emit_status(self) -> None:
        s = self.snap
        if s is None or self._refreshing and s is None:
            return
        if s.offline:
            self.status.emit("offline", i18n.t("status.offline_cached", age=fmt.age(s.stale_age or 0))
                             if s.stale_age else i18n.t("status.offline"))
        elif s.stale_age is not None:
            self.status.emit("stale", i18n.t("status.stale", age=fmt.age(s.stale_age + time.time() - s.ts)))
        else:
            self.status.emit("live", i18n.t("status.live", age=fmt.age(time.time() - s.ts)))

    # ── wallets ──────────────────────────────────────────────────────────────
    def wallets(self) -> list[Wallet]:
        out = []
        for d in self.store.get("wallets") or []:
            try:
                w = Wallet.from_dict(d)
            except (KeyError, ValueError, TypeError):
                continue
            if wallets_mod.chain(w.chain):
                out.append(w)
        return out

    def _save_wallets(self, lst: list[Wallet]) -> None:
        self.store.put("wallets", [w.to_dict() for w in lst])
        ids = {w.id for w in lst}
        self.wallet_data = {k: val for k, val in self.wallet_data.items() if k in ids}

    def _wallet_ttl(self) -> float:
        return max(45.0, self.settings.refresh_seconds - 5.0)

    async def _fetch_wallet(self, w: Wallet, cache_only: bool) -> None:
        try:
            f = await self.service.wallets.balance(w, self._wallet_ttl(), cache_only)
            self.wallet_data[w.id] = (f.value, f.stale_age, f.error)
        except Exception as e:  # noqa: BLE001 - one failing address must not stop the others
            if not cache_only:
                prev = self.wallet_data.get(w.id, (None, None, None))[0]
                self.wallet_data[w.id] = (prev, None, str(e) or type(e).__name__)

    async def _refresh_wallets(self, cache_only: bool = False) -> None:
        """Every watched address at once; one that fails keeps its last balance, marked stale."""
        lst = [w for w in self.wallets() if not w.manual]
        if not lst:
            return
        await asyncio.gather(*(self._fetch_wallet(w, cache_only) for w in lst))
        self.walletsChanged.emit()

    def refresh_wallet(self, wallet_id: str) -> None:
        w = next((x for x in self.wallets() if x.id == wallet_id), None)
        if w is None or w.manual:
            return

        async def go() -> None:
            self.wallet_loading.add(wallet_id)
            self.walletsChanged.emit()
            try:
                await self._fetch_wallet(w, False)
            finally:
                self.wallet_loading.discard(wallet_id)
            self._after_wallet_change()

        self.run(go())

    def _after_wallet_change(self) -> None:
        self.walletsChanged.emit()
        self.portfolioChanged.emit()
        have = {r.coin.id for r in self.snap.rows} if self.snap else set()
        if any(c.id not in have for c in self.tracked_coins()):
            self.refresh()

    def add_wallets(self, new: list[Wallet]) -> str | None:
        """Add watched addresses (one per network). Returns an error message, or None when saved."""
        cur = self.wallets()
        if len(cur) + len(new) > wallets_mod.MAX_WALLETS:
            return i18n.t("wal.err_too_many", n=wallets_mod.MAX_WALLETS)
        taken = {addr.key(w.chain, w.address) for w in cur if not w.manual}
        for w in new:
            if not w.manual and addr.key(w.chain, w.address) in taken:
                info = wallets_mod.chain(w.chain)
                return i18n.t("wal.err_duplicate", chain=info.name if info else w.chain)
        self._save_wallets(cur + new)
        for w in new:
            if not w.manual:
                self.refresh_wallet(w.id)
        self._after_wallet_change()
        return None

    def update_wallet(self, w: Wallet) -> None:
        self._save_wallets([w if x.id == w.id else x for x in self.wallets()])
        self._after_wallet_change()

    def delete_wallet(self, wallet_id: str) -> None:
        self._save_wallets([x for x in self.wallets() if x.id != wallet_id])
        self.walletsChanged.emit()
        self.portfolioChanged.emit()

    def wallet_holdings(self) -> dict[str, tuple[str, float]]:
        """Coin id -> (symbol, total amount) over the wallets that count toward the portfolio."""
        out: dict[str, tuple[str, float]] = {}
        for w in self.wallets():
            if not w.in_portfolio:
                continue
            if w.manual:
                items = [(w.coin_id, w.symbol, w.amount or 0.0)] if w.coin_id and (w.amount or 0) > 0 else []
            else:
                bal = self.wallet_data.get(w.id, (None, None, None))[0]
                items = [(a.coin_id, a.symbol, a.amount) for a in (bal.assets if bal else ()) if a.amount > 0]
            for cid, sym, amount in items:
                out[cid] = (sym, out.get(cid, (sym, 0.0))[1] + amount)
        return out

    def wallet_value(self, w: Wallet) -> float | None:
        prices = self.prices()[0]
        if w.manual:
            p = prices.get(w.coin_id)
            return None if w.amount is None or p is None else w.amount * p
        bal = self.wallet_data.get(w.id, (None, None, None))[0]
        if bal is None:
            return None
        priced = [a.amount * prices[a.coin_id] for a in bal.assets if a.coin_id in prices]
        if not priced and any(a.amount > 0 for a in bal.assets):
            return None
        return sum(priced)

    async def wallet_activity(self, ids: list[str] | None = None) -> tuple[list[Transfer], list[str]]:
        """History of ``ids`` (all wallets when None), merged so transfers between them show once."""
        lst = [w for w in self.wallets() if not w.manual and (ids is None or w.id in ids)]
        errors: list[str] = []

        async def one(w: Wallet) -> list[Transfer]:
            try:
                f = await self.service.wallets.activity(w)
            except Exception as e:  # noqa: BLE001
                errors.append(str(e) or type(e).__name__)
                return []
            if f.error:
                errors.append(f.error)
            # A token the balance check did not accept (unpriced, flagged, dust) is spam in the history too.
            bal = self.wallet_data.get(w.id, (None, None, None))[0]
            accepted = {a.coin_id for a in bal.assets} if bal else set()
            out = []
            for t in f.value:
                t.legs = [leg for leg in t.legs if not is_synthetic(leg.coin_id) or leg.coin_id in accepted]
                if t.legs or t.fee is not None:
                    out.append(t)
            return out

        parts = await asyncio.gather(*(one(w) for w in lst))
        return merge_activity(parts), errors

    # ── settings ─────────────────────────────────────────────────────────────
    def apply_settings(self, new: cfg.Settings) -> None:
        old = self.settings
        cfg.save(new)
        ai_changed = (old.ai_enabled, old.ai_model) != (new.ai_enabled, new.ai_model)
        rebuild = (old.exchanges != new.exchanges or old.proxy != new.proxy or old.fiat != new.fiat
                   or old.secondary_fiat != new.secondary_fiat)
        profile_changed = old.profile != new.profile
        self.settings = new
        i18n.set_language(new.language)
        if rebuild or profile_changed:
            svc = self.service
            self.run(svc.close())
            self.service = MarketService(new, self.cache)
        if profile_changed:
            self.store.close()
            self.store = ProfileStore(cfg.data_dir() / "profiles" / f"{new.profile}.db")
            self.wallet_data = {}
            self.portfolioChanged.emit()
        self.timer.setInterval(new.refresh_seconds * 1000)
        self.settingsChanged.emit()
        self.snap = None if rebuild else self.snap
        if ai_changed:
            self.check_ai()
        self.refresh()

    # ── portfolio helpers ────────────────────────────────────────────────────
    def prices(self) -> tuple[dict[str, float], dict[str, float]]:
        fiat, usd = {}, {}
        if self.snap:
            for r in self.snap.rows:
                if r.price_fiat is not None:
                    fiat[r.coin.id] = r.price_fiat
                if r.price_usd is not None:
                    usd[r.coin.id] = r.price_usd
        # Tokens only a chain's indexer prices (never sent to CoinGecko).
        rate = self.snap.fx.get(self.settings.fiat) if self.snap else None
        for bal, _age, _err in self.wallet_data.values():
            for a in bal.assets if bal else ():
                if is_synthetic(a.coin_id) and a.price_usd:
                    usd[a.coin_id] = a.price_usd
                    if rate:
                        fiat[a.coin_id] = a.price_usd * rate
        return fiat, usd

    def positions(self) -> dict[str, Position]:
        rate = self.snap.fx.get(self.settings.fiat) if self.snap else None
        try:
            from_trades = build_positions(self.store.trades(), self.settings.fiat, rate)
        except ValueError:
            from_trades = {}
        held = self.wallet_holdings()
        # Wallets supply the amounts; trades still supply the average cost.
        return merge_wallets(from_trades, held) if held else from_trades

    def summary(self) -> Summary:
        fiat, usd = self.prices()
        return summarize(self.positions(), fiat, usd)

    def coin_name(self, coin_id: str) -> str:
        for c in self.tracked_coins():
            if c.id == coin_id:
                return c.symbol
        held = self.wallet_holdings().get(coin_id)
        return held[0] if held else coin_id

    def save_trades_changed(self) -> None:
        self.portfolioChanged.emit()
        held = {t.coin_id for t in self.store.trades()}
        if self.snap and not held <= {r.coin.id for r in self.snap.rows}:
            self.refresh()          # a new coin needs a price

    def save_dca(self, plan: DcaPlan | None) -> None:
        self.store.set_dca_plan(plan)
        self.portfolioChanged.emit()

    # ── rules ────────────────────────────────────────────────────────────────
    def save_rules(self, rules: list[Rule]) -> None:
        self.store.set_rules(rules)
        self.run(self._evaluate_rules())

    async def _evaluate_rules(self) -> None:
        rules = self.store.rules()
        if not rules or not self.snap:
            return
        fiat, usd = self.prices()
        s = summarize(self.positions(), fiat, usd)
        ctx = RuleContext(prices_fiat=fiat, prices_usd=usd,
                          avg_cost_fiat={h.position.coin_id: h.position.avg_cost_fiat or 0.0 for h in s.holdings},
                          weights=s.weights, targets=self.store.targets(), fiat=self.settings.fiat)
        coins = {c.id: c for c in self.tracked_coins()}
        for cid, iv in data_needs(rules):
            coin = coins.get(cid)
            if not coin:
                continue
            with contextlib.suppress(Exception):
                cs = await self.service.candles(coin, "usd", Interval(iv), 260 if iv != "1h" else 72,
                                                ref_usd=usd.get(cid))
                ctx.closes[(cid, iv)] = closes(cs.candles)
        state = self.store.rule_state()
        fired = evaluate(rules, ctx, state, time.time())
        self.store.set_rule_state(state)
        if fired:
            self.alert_log = (fired + self.alert_log)[:200]
            self.alerts.emit(fired)
            if self.settings.notifications:
                for a in fired:
                    self.notifier.send(a.rule.name, self.alert_text(a))

    def alert_text(self, a: Alert) -> str:
        parts = []
        if a.value is not None:
            kind = a.rule.condition.kind
            unit = " pp" if kind == "drift" else "" if kind in ("rsi", "price") else "%"
            val = fmt.num(a.value, 2)
            subject = self.coin_name(a.subject) if a.subject else ""
            parts.append(f"{subject + ': ' if subject else ''}{val}{unit}")
        if a.rule.message:
            parts.append(a.rule.message)
        return " — ".join(parts) or a.rule.name

    # ── assistant ────────────────────────────────────────────────────────────
    def _enriched(self, snap: Snapshot) -> None:
        # RSI from the 31 daily closes fetched for the sparklines; enough for a
        # 14-period RSI, while trend states that need 200 days stay empty.
        self.tech = {r.coin.id: tech_state(r.spark) for r in snap.rows if len(r.spark) > 15}
        self.update_tips()

    def update_tips(self) -> None:
        s = self.summary()
        trades = self.store.trades()
        plan = self.store.dca_plan()
        cur = None
        if plan:
            values = {h.position.coin_id: h.value_fiat or 0.0 for h in s.holdings}
            sched = dca_mod.schedule(plan, values, self.store.targets())
            cur = dca_mod.current(sched, self.store.dca_done(), date.today())
        names = {c.id: c.symbol for c in self.tracked_coins()} | self.store.target_symbols()
        names["*other*"] = i18n.t("targets.other")
        fiat = self.settings.fiat
        x = AdvisorInput(
            fiat=fiat, rows=self.snap.rows if self.snap else (), watchlist=[c.id for c in self.settings.watchlist],
            summary=s, trades=len(trades), wallets=len(self.wallets()),
            trades_without_rate=sum(1 for t in trades if not t.usd_rate),
            targets=self.store.targets(), names=names, plan_amount=plan.amount if plan else None, current=cur,
            plan_exists=plan is not None, rules=len(self.store.rules()), tech=self.tech,
            offline=bool(self.snap and self.snap.offline), stale_age=self.snap.stale_age if self.snap else None)
        f = Formatter(money=lambda v: fmt.money(v, fiat, 0 if abs(v) >= 100 else 2),
                      pct=lambda v: fmt.pct(v, 1), num=lambda v: fmt.num(v, 1))
        self.tips = make_tips(x, f)
        self.tipsChanged.emit(self.tips)

    def tip_text(self, tip: Tip) -> tuple[str, str]:
        body = i18n.t(tip.body, **tip.params)
        if tip.extra:
            body += " " + i18n.t(tip.extra, **tip.params)
        return i18n.t(tip.title, **tip.params), body

    # ── local AI (Ollama on this machine) ────────────────────────────────────
    def check_ai(self) -> None:
        async def probe() -> str:
            if self.ai is None:
                self.ai = LocalAI()
            return await self.ai.pick_model(self.settings.ai_model)

        def ok(model: str) -> None:
            self.ai_model = model
            debug(f"local AI ready: {model}")
            self.aiChanged.emit(True)

        def fail(_e: Exception) -> None:
            self.ai_model = ""
            self.aiChanged.emit(False)

        if not self.settings.ai_enabled:
            self.ai_model = ""
            self.aiChanged.emit(False)
            return
        self.run(probe(), ok, fail)

    @property
    def ai_ready(self) -> bool:
        return bool(self.settings.ai_enabled and self.ai and self.ai_model)

    def ai_context(self) -> dict:
        """A compact, local-only summary of what the user sees, for the AI prompt."""
        s = self.summary()
        fiat = self.settings.fiat
        targets = self.store.targets()
        rows = {r.coin.id: r for r in self.snap.rows} if self.snap else {}

        def r2(v: float | None) -> float | None:
            return None if v is None else round(v, 2)

        ctx: dict = {
            "currency": fiat,
            "portfolio": {"value": r2(s.value_fiat), "cost": r2(s.cost_fiat), "pnl": r2(s.pnl_fiat),
                          "pnl_pct": r2(s.pnl_pct)} if s.holdings else None,
            "holdings": [{"symbol": h.position.symbol, "amount": h.position.amount, "value": r2(h.value_fiat),
                          "avg_cost": r2(h.position.avg_cost_fiat), "pnl_pct": r2(h.pnl_pct),
                          "weight_pct": r2(s.weights.get(h.position.coin_id)),
                          "target_pct": targets.get(h.position.coin_id)} for h in s.holdings],
            "watchlist": [{"symbol": r.coin.symbol, "price": r2(r.price_fiat), "change_24h_pct": r2(r.change_24h),
                           "change_7d_pct": r2(r.change_7d), "change_30d_pct": r2(r.change_30d),
                           "from_ath_pct": r2(r.ath_change_pct), "corr_btc_30d": r2(r.corr_btc_30d),
                           "rsi14_daily": r2(self.tech[r.coin.id].rsi) if r.coin.id in self.tech else None}
                          for cid in [c.id for c in self.settings.watchlist] if (r := rows.get(cid))],
            "alerts": [r.name for r in self.store.rules() if r.enabled],
            "assistant_notes": [self.tip_text(t)[0] for t in self.tips if not t.id.startswith("learn.")][:8],
        }
        plan = self.store.dca_plan()
        if plan:
            ctx["dca_plan"] = {"amount_per_period": plan.amount, "frequency": plan.frequency.value,
                               "periods": plan.periods, "done": len(self.store.dca_done())}
        return ctx

    async def ask_ai(self, question: str, on_chunk: Callable[[str], None]) -> None:
        if not self.ai_ready or self.ai is None:
            raise AiUnavailable("AI is not available")
        language = "Turkish" if i18n.language() == "tr" else "English"
        async for chunk in self.ai.ask(question, self.ai_context(), self.ai_model, language):
            on_chunk(chunk)
