"""DCA planner: this period's card, progress and the full schedule."""
from __future__ import annotations

import time
from datetime import date, datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from mazecrypto.core import dca as dca_mod
from mazecrypto.core.models import Trade
from mazecrypto.i18n import t
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.glass import Glass
from mazecrypto.ui.theme import P, tabular
from mazecrypto.ui.widgets import (
    Dialog,
    NumItem,
    Page,
    StatCard,
    TitledGlass,
    button,
    card_grid,
    confirm,
    label,
    text_item,
)


class DcaPage(Page):
    def __init__(self, ctl: Controller):
        super().__init__(t("dca.title"), t("dca.sub"))
        self.ctl = ctl
        self.edit_btn = button(t("dca.edit"), "Primary", self._edit, icon_name="ph.pencil-simple")
        self.del_btn = button(t("btn.delete"), "Danger", self._delete)
        self.actions.addWidget(self.del_btn)
        self.actions.addWidget(self.edit_btn)

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(0, 20, 0, 0)
        intro = Glass(padding=28)
        intro.lay.setSpacing(12)
        intro.lay.addWidget(label(t("dca.what_t"), "PageTitle"))
        intro.lay.addWidget(label(t("dca.empty"), "Body", wrap=True))
        intro.lay.addWidget(label(t("dca.what_b"), "Explain", wrap=True))
        row = QHBoxLayout()
        row.addWidget(button(t("dca.create"), "Primary", self._edit, icon_name="ph.plus"))
        row.addStretch()
        intro.lay.addSpacing(6)
        intro.lay.addLayout(row)
        el.addWidget(intro)
        el.addStretch()
        self.outer.addWidget(self.empty, 1)

        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # this-period card
        self.now = Glass(padding=20, tint=P["accent"], horizontal=True)
        nl = self.now.lay
        nl.setSpacing(28)
        left = QVBoxLayout()
        left.setSpacing(6)
        self.now_key = label("", "CardKey")
        self.now_amount = label("", "CardValue")
        self.now_amount.setObjectName("BigValue")
        self.now_due = label("", "Mid")
        for w in (self.now_key, self.now_amount, self.now_due):
            left.addWidget(w)
        nl.addLayout(left)
        self.now_items = label("", "Body")
        self.now_items.setTextFormat(Qt.TextFormat.RichText)
        nl.addWidget(self.now_items, 1)
        self.done_btn = button(t("dca.mark_done"), "Primary", self._mark_current, icon_name="ph.check")
        nl.addWidget(self.done_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        bl.addWidget(self.now)
        bl.addSpacing(14)

        self.c_prog, self.c_inv = StatCard(t("dca.progress"), "ph.flag"), StatCard(t("dca.invested"),
                                                                                              "ph.coin")
        self.c_rem, self.c_next = StatCard(t("dca.remaining"), "ph.hourglass"), StatCard(t("dca.end"),
                                                                                         "ph.calendar")
        bl.addWidget(card_grid([self.c_prog, self.c_inv, self.c_rem, self.c_next]))
        bl.addSpacing(16)
        sched = TitledGlass(t("dca.schedule"))

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([h for h in ("#", t("trade.date"), t("dca.amount"),
                                                                  t("dca.items"), t("dca.done"))])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setShowGrid(False)
        tabular(self.table)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        sched.lay.addWidget(self.table, 1)
        bl.addWidget(sched, 1)
        self.outer.addWidget(self.body, 1)

        self.listen(ctl.portfolioChanged, self.render)
        self.listen(ctl.snapshotReady, lambda _s: self.render())
        self.render()

    def _schedule(self) -> tuple[dca_mod.DcaPlan | None, list[dca_mod.Installment]]:
        plan = self.ctl.store.dca_plan()
        if not plan:
            return None, []
        values = {h.position.coin_id: h.value_fiat or 0.0 for h in self.ctl.summary().holdings}
        return plan, dca_mod.schedule(plan, values, self.ctl.store.targets())

    def render(self) -> None:
        plan, sched = self._schedule()
        self.empty.setVisible(plan is None)
        self.body.setVisible(plan is not None)
        self.del_btn.setVisible(plan is not None)
        self.edit_btn.setText(t("dca.edit") if plan else t("dca.create"))
        if plan is None:
            self.set_header(t("dca.title"), t("dca.sub"))
            return
        done = self.ctl.store.dca_done()
        freq = t(f"dcaed.freq.{plan.frequency.value}")
        mode = t("dca.mode_targets") if plan.follow_targets else t("dca.mode_fixed")
        self.set_header(f"{t('dca.title')} · {plan.name}",
                        f"{freq} · {fmt.money(plan.amount, plan.fiat, 0)} × {plan.periods} · {mode}")

        cur = dca_mod.current(sched, done, date.today())
        if cur:
            inst, state = cur
            color = {"overdue": P["warn"], "due": P["accent"], "upcoming": P["text_mid"]}[state]
            self.now_key.setText(t("dca.period_n", n=inst.index + 1, total=plan.periods).upper())
            self.now_amount.setText(fmt.money(inst.total, plan.fiat, 0))
            self.now_amount.setStyleSheet(f"color: {color};")
            days = (inst.due - date.today()).days
            when = t("dca.in_days", n=days) if days > 0 else t("dca.today") if days == 0 else t("dca.ago", n=-days)
            self.now_due.setText(f"{t('dca.state.' + state)} · {fmt.date(_ts(inst.due))} · {when}")
            fiat_prices, _ = self.ctl.prices()
            lines = []
            for it in inst.items:
                px = fiat_prices.get(it.coin_id)
                est = f'<span style="color:{P["muted"]}">  ≈ {fmt.amount(it.amount / px)} {it.symbol}</span>' \
                    if px and plan.fiat == self.ctl.settings.fiat else ""
                lines.append(f'<b>{it.symbol}</b>&nbsp;&nbsp;{fmt.money(it.amount, plan.fiat, 2)}{est}')
            self.now_items.setText("<br>".join(lines))
            self.done_btn.setEnabled(True)
            self._current = inst
        else:
            self.now_key.setText(t("dca.finished").upper())
            self.now_amount.setText("✓")
            self.now_amount.setStyleSheet(f"color: {P['up']};")
            self.now_due.setText("")
            self.now_items.setText("")
            self.done_btn.setEnabled(False)
            self._current = None

        prog = dca_mod.progress(sched, done)
        self.c_prog.set(f"{prog.done} / {prog.total}", fmt.pct(prog.done / prog.total * 100, 0, False))
        self.c_inv.set(fmt.money(prog.invested, plan.fiat, 0))
        self.c_rem.set(fmt.money(prog.remaining, plan.fiat, 0))
        self.c_next.set(fmt.date(_ts(sched[-1].due)) if sched else "—")

        self.table.setRowCount(len(sched))
        cur_idx = cur[0].index if cur else -1
        for i, inst in enumerate(sched):
            is_done = inst.index in done
            color = P["muted"] if is_done else (P["accent"] if inst.index == cur_idx else None)
            self.table.setItem(i, 0, NumItem(f"{inst.index + 1:02d}", inst.index, color, Qt.AlignmentFlag.AlignLeft))
            self.table.setItem(i, 1, text_item(fmt.date(_ts(inst.due)), color))
            self.table.setItem(i, 2, NumItem(fmt.money(inst.total, plan.fiat, 0), inst.total, color))
            items = "  ".join(f"{x.symbol} {fmt.money(x.amount, plan.fiat, 0)}" for x in inst.items)
            self.table.setItem(i, 3, text_item(items, color or P["text_mid"]))
            cb = QCheckBox()
            cb.setChecked(is_done)
            cb.toggled.connect(lambda on, idx=inst.index: self._toggle(idx, on))
            wrap = QWidget()
            wl = QHBoxLayout(wrap)
            wl.setContentsMargins(10, 0, 0, 0)
            wl.addWidget(cb)
            self.table.setCellWidget(i, 4, wrap)

    def _toggle(self, idx: int, on: bool) -> None:
        done = self.ctl.store.dca_done()
        done = done | {idx} if on else done - {idx}
        self.ctl.store.set_dca_done(done)
        self.ctl.portfolioChanged.emit()

    def _mark_current(self) -> None:
        inst = getattr(self, "_current", None)
        plan = self.ctl.store.dca_plan()
        if not inst or not plan:
            return
        d = Dialog(self, t("dca.mark_done"), t("btn.ok"))
        d.content.addWidget(label(t("dca.mark_q", n=inst.index + 1), "Body", wrap=True))
        log = QCheckBox(t("dca.log_trades"))
        fiat_prices, usd_prices = self.ctl.prices()
        can_log = plan.fiat == self.ctl.settings.fiat and all(fiat_prices.get(x.coin_id) for x in inst.items)
        log.setChecked(can_log)
        log.setEnabled(can_log)
        d.content.addWidget(log)
        d.content.addWidget(label(t("dca.log_note"), "Small", wrap=True))
        if not d.exec():
            return
        if log.isChecked():
            rate = self.ctl.snap.fx.get(plan.fiat) if self.ctl.snap else None
            note = t("dca.trade_note", n=inst.index + 1)
            for x in inst.items:
                qty = x.amount / fiat_prices[x.coin_id]
                self.ctl.store.add_trade(Trade(int(time.time()), x.coin_id, x.symbol, qty, x.amount, plan.fiat, 0.0,
                                               "buy", "", note, rate))
        self._toggle(inst.index, True)
        if log.isChecked():
            self.ctl.save_trades_changed()

    def _edit(self) -> None:
        from mazecrypto.ui.dialogs.dca_editor import DcaDialog

        if DcaDialog(self.ctl, self, self.ctl.store.dca_plan()).exec():
            self.ctl.refresh()

    def _delete(self) -> None:
        if confirm(self, t("dca.delete"), t("dca.delete_q"), t("btn.delete"), danger=True):
            self.ctl.save_dca(None)
            self.ctl.store.set_dca_done(set())


def _ts(d: date) -> float:
    return datetime(d.year, d.month, d.day, 12).timestamp()
