"""Home: the portfolio at a glance, the assistant's tips and the local AI box."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from mazecrypto.i18n import t
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.glass import Glass
from mazecrypto.ui.theme import P, change_color
from mazecrypto.ui.widgets import (
    Page,
    Sparkline,
    StatCard,
    TipCard,
    TitledGlass,
    button,
    card_grid,
    label,
    scrolled,
    section,
)

if TYPE_CHECKING:
    from mazecrypto.ui.window import MainWindow

MAX_TIPS = 6
QUESTIONS = ("ai.q.overview", "ai.q.risk", "ai.q.plan", "ai.q.rsi")


def greeting() -> str:
    h = datetime.now().hour
    return t("home.morning") if 5 <= h < 12 else t("home.afternoon") if h < 18 else t("home.evening")


class HomePage(Page):
    def __init__(self, ctl: Controller, win: MainWindow):
        super().__init__(greeting(), t("home.sub"))
        self.ctl, self.win = ctl, win
        self._ai_task: asyncio.Task | None = None
        self.actions.addWidget(button(t("hold.add"), "Primary", lambda: win.open_action("holdings:add"),
                                      "Ctrl+N", "ph.plus"))

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(0)

        self.c_value = StatCard(t("home.value"), "ph.wallet")
        self.c_day = StatCard(t("home.today"), "ph.clock-counter-clockwise")
        self.c_plan = StatCard(t("home.plan"), "ph.calendar-check")
        self.c_alerts = StatCard(t("home.alerts"), "ph.bell")
        lay.addWidget(card_grid([self.c_value, self.c_day, self.c_plan, self.c_alerts]))
        lay.addSpacing(16)

        cols = QHBoxLayout()
        cols.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(section(t("home.assistant")))
        self.tips_box = QVBoxLayout()
        self.tips_box.setSpacing(10)
        left.addLayout(self.tips_box)
        left.addSpacing(6)
        self.ai_card = self._build_ai()
        left.addWidget(self.ai_card)
        left.addStretch()
        cols.addLayout(left, 3)

        right_w = QWidget()
        right_w.setMaximumWidth(440)
        right_w.setMinimumWidth(300)
        right = QVBoxLayout(right_w)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)
        right.addWidget(section(t("home.market")))
        self.market = TitledGlass(t("home.watch_title"), button(t("home.see_all"), "Link",
                                                                lambda: win.nav("watchlist")))
        self.market_grid = QGridLayout()
        self.market_grid.setHorizontalSpacing(12)
        self.market_grid.setVerticalSpacing(10)
        self.market.lay.addLayout(self.market_grid)
        right.addWidget(self.market)
        right.addStretch()
        cols.addWidget(right_w, 2)
        lay.addLayout(cols)
        lay.addSpacing(18)
        lay.addWidget(label(t("home.disclaimer"), "Small", wrap=True))
        lay.addStretch()
        self.outer.addWidget(scrolled(inner), 1)

        self.listen(ctl.tipsChanged, self._render_tips)
        self.listen(ctl.snapshotReady, lambda _s: self._render_cards())
        self.listen(ctl.enriched, lambda _s: self._render_market())
        self.listen(ctl.portfolioChanged, self._render_cards)
        self.listen(ctl.aiChanged, self._ai_changed)
        self._render_cards()
        self._render_tips(ctl.tips)
        self._render_market()
        self._ai_changed(ctl.ai_ready)

    def on_show(self) -> None:
        self.set_header(greeting(), t("home.sub"))
        self._render_cards()

    # ── cards ────────────────────────────────────────────────────────────────
    def _render_cards(self) -> None:
        fiat = self.ctl.settings.fiat
        s = self.ctl.summary()
        if s.holdings:
            self.c_value.set(fmt.money(s.value_fiat, fiat, 2),
                             t("home.pnl", pnl=fmt.signed_money(s.pnl_fiat, fiat), pct=fmt.pct(s.pnl_pct)),
                             sub_color=change_color(s.pnl_fiat))
            rows = {r.coin.id: r for r in self.ctl.snap.rows} if self.ctl.snap else {}
            prev = 0.0
            for h in s.holdings:
                ch = rows.get(h.position.coin_id).change_24h if h.position.coin_id in rows else None
                v = h.value_fiat or 0.0
                prev += v / (1 + ch / 100) if ch is not None and ch > -100 else v
            day = s.value_fiat - prev
            day_pct = day / prev * 100 if prev else None
            self.c_day.set(fmt.signed_money(day, fiat), fmt.pct(day_pct), change_color(day), change_color(day))
        else:
            self.c_value.set("—", t("watch.no_trades"))
            self.c_day.set("—", t("home.today_empty"))
        cur = next((tp for tp in self.ctl.tips if tp.id.startswith("plan.")), None)
        plan = self.ctl.store.dca_plan()
        if cur:
            self.c_plan.set(cur.params.get("amount", "—"), t(cur.title, **cur.params),
                            P["accent"] if cur.level == "action" else None)
        elif plan:
            self.c_plan.set(fmt.money(plan.amount, plan.fiat, 0), t("home.plan_ok"))
        else:
            self.c_plan.set("—", t("watch.no_plan"))
        n = len([r for r in self.ctl.store.rules() if r.enabled])
        if self.ctl.alert_log:
            self.c_alerts.set(str(n), t("home.last_alert", name=self.ctl.alert_log[0].rule.name), P["warn"])
        else:
            self.c_alerts.set(str(n), t("home.alerts_quiet") if n else t("home.alerts_none"))

    def _render_tips(self, tips) -> None:
        while self.tips_box.count():
            w = self.tips_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for tip in tips[:MAX_TIPS]:
            title, body = self.ctl.tip_text(tip)
            card = TipCard(tip.level, title, body, tip.action, t(tip.action_label) if tip.action_label else "")
            card.actionRequested.connect(self.win.open_action)
            self.tips_box.addWidget(card)
        self._render_cards()

    def _render_market(self) -> None:
        while self.market_grid.count():
            w = self.market_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        snap = self.ctl.snap
        if not snap or not self.ctl.settings.watchlist:
            self.market_grid.addWidget(label(t("watch.empty"), "Small", wrap=True), 0, 0)
            return
        rows = {r.coin.id: r for r in snap.rows}
        fiat = self.ctl.settings.fiat
        for i, c in enumerate(self.ctl.settings.watchlist[:8]):
            r = rows.get(c.id)
            if not r:
                continue
            sym = button(c.symbol, "Link", lambda _=False, coin=c: self.win.open_chart(coin))
            sym.setStyleSheet(f"color: {P['text']}; text-align: left; padding: 0;")
            self.market_grid.addWidget(sym, i, 0)
            sp = Sparkline(r.spark[-31:])
            sp.setFixedHeight(28)
            self.market_grid.addWidget(sp, i, 1)
            price = label(fmt.money(r.price_fiat, fiat), "Strong")
            price.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.market_grid.addWidget(price, i, 2)
            ch = label(fmt.pct(r.change_24h, 1))
            ch.setStyleSheet(f"color: {change_color(r.change_24h)};")
            ch.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            ch.setMinimumWidth(64)
            self.market_grid.addWidget(ch, i, 3)
        self.market_grid.setColumnStretch(1, 1)

    # ── local AI ─────────────────────────────────────────────────────────────
    def _build_ai(self) -> Glass:
        card = TitledGlass(t("ai.title"))
        self.ai_hint = label(t("ai.hint"), "Small", wrap=True)
        card.lay.addWidget(self.ai_hint)
        chips = QGridLayout()
        chips.setSpacing(6)
        for i, key in enumerate(QUESTIONS):
            chips.addWidget(button(t(key), "Chip", lambda _=False, k=key: self._ask(t(k))), i // 2, i % 2)
        chips.setColumnStretch(2, 1)
        card.lay.addLayout(chips)
        row = QHBoxLayout()
        self.ai_in = QLineEdit()
        self.ai_in.setPlaceholderText(t("ai.placeholder"))
        self.ai_in.returnPressed.connect(lambda: self._ask(self.ai_in.text()))
        row.addWidget(self.ai_in, 1)
        self.ai_send = button(t("ai.ask"), "Primary", lambda: self._ask(self.ai_in.text()), icon_name="ph.sparkle")
        row.addWidget(self.ai_send)
        card.lay.addLayout(row)
        self.ai_out = label("", "Body", wrap=True)
        self.ai_out.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ai_out.hide()
        card.lay.addWidget(self.ai_out)
        self.ai_note = label(t("ai.note"), "Small", wrap=True)
        card.lay.addWidget(self.ai_note)
        return card

    def _ai_changed(self, ready: bool) -> None:
        self.ai_card.setVisible(ready)
        if ready:
            self.ai_hint.setText(t("ai.hint_model", model=self.ctl.ai_model))

    def _ask(self, question: str) -> None:
        q = question.strip()
        if not q or not self.ctl.ai_ready:
            return
        if self._ai_task and not self._ai_task.done():
            self._ai_task.cancel()
        self.ai_in.setText(q)
        self.ai_out.setText(t("ai.thinking"))
        self.ai_out.setStyleSheet(f"color: {P['muted']};")
        self.ai_out.show()
        parts: list[str] = []

        def chunk(c: str) -> None:
            parts.append(c)
            self.ai_out.setStyleSheet(f"color: {P['text']};")
            self.ai_out.setText("".join(parts).strip())

        def fail(e: Exception) -> None:
            self.ai_out.setStyleSheet(f"color: {P['down']};")
            self.ai_out.setText(t("ai.failed", err=e))

        self._ai_task = self.ctl.run(self.ctl.ask_ai(q, chunk), fail=fail)
