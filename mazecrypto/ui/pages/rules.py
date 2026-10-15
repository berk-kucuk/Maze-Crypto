"""Alert rules: list, live state, templates, alert history."""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QInputDialog,
    QListWidget,
    QMenu,
    QTableWidget,
)

from mazecrypto.core import presets
from mazecrypto.core.models import Coin
from mazecrypto.core.rules import Rule
from mazecrypto.i18n import t
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.dialogs.rule_editor import describe
from mazecrypto.ui.glass import Glass
from mazecrypto.ui.theme import P
from mazecrypto.ui.widgets import Page, TitledGlass, button, confirm, label, text_item


class RulesPage(Page):
    def __init__(self, ctl: Controller):
        super().__init__(t("rules.title"), t("rules.sub"))
        self.ctl = ctl
        self.actions.addWidget(button(t("rules.test"), "", self._test_notify, icon_name="ph.bell-ringing"))
        tpl = button(t("rules.templates"), "", icon_name="ph.magic-wand")
        m = QMenu(tpl)
        keys = ("dip_reserve", "rebalance", "oversold", "big_move") if ctl.expert else ("dip_reserve", "big_move",
                                                                                         "rebalance")
        for key in keys:
            m.addAction(t(f"rules.tpl.{key}"), lambda k=key: self._template(k))
        tpl.setMenu(m)
        self.actions.addWidget(tpl)
        self.actions.addWidget(button(t("rules.add"), "Primary", self._add, icon_name="ph.plus"))

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([h for h in (t("rules.on"), t("rule.name"),
                                                                  t("rules.condition"), t("rules.cooldown"),
                                                                  t("rules.last"))])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setShowGrid(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnHidden(3, not ctl.expert)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._edit(r))
        self.table.itemChanged.connect(self._item_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.rules_card = Glass(padding=10)
        self.rules_card.lay.addWidget(self.table)
        self.outer.addWidget(self.rules_card, 3)

        self.empty = Glass(padding=28)
        self.empty.lay.setSpacing(10)
        self.empty.lay.addWidget(label(t("rules.what_t"), "PageTitle"))
        self.empty.lay.addWidget(label(t("rules.empty"), "Body", wrap=True))
        self.empty.lay.addWidget(label(t("rules.what_b"), "Explain", wrap=True))
        self.empty.lay.addStretch()
        self.outer.addWidget(self.empty, 3)

        self.outer.addSpacing(16)
        hist = TitledGlass(t("rules.history"))
        self.history = QListWidget()
        hist.lay.addWidget(self.history)
        self.outer.addWidget(hist, 2)
        self.notify_lbl = label("", "Small")
        self.outer.addSpacing(8)
        self.outer.addWidget(self.notify_lbl)

        self.listen(ctl.alerts, lambda _a: self.render())
        self.listen(ctl.portfolioChanged, self.render)
        self.render()

    def on_show(self) -> None:
        self.render()

    def render(self) -> None:
        rules = self.ctl.store.rules()
        state = self.ctl.store.rule_state()
        coins = {c.id: c.symbol for c in self.ctl.tracked_coins()}
        self.rules_card.setVisible(bool(rules))
        self.empty.setVisible(not rules)
        self.table.blockSignals(True)
        self.table.setRowCount(len(rules))
        for i, r in enumerate(rules):
            on = text_item("", data=r.id)
            on.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            on.setCheckState(Qt.CheckState.Checked if r.enabled else Qt.CheckState.Unchecked)
            self.table.setItem(i, 0, on)
            st = state.get(r.id)
            active = bool(st and st.active and r.enabled)
            name = text_item(r.name, P["warn"] if active else (None if r.enabled else P["muted"]), r.id)
            if r.message:
                name.setToolTip(r.message)
            self.table.setItem(i, 1, name)
            self.table.setItem(i, 2, text_item(describe(r.condition, coins, self.ctl.settings.fiat), P["text_mid"]))
            self.table.setItem(i, 3, text_item(f"{fmt.num(r.cooldown_hours, 0 if r.cooldown_hours >= 1 else 2)} h",
                                               P["muted"]))
            last = st.last_fired if st else 0
            self.table.setItem(i, 4, text_item(f"{fmt.date(last)} {time.strftime('%H:%M', time.localtime(last))}"
                                               if last else "—", P["muted"]))
        self.table.blockSignals(False)
        self.history.clear()
        for a in self.ctl.alert_log:
            stamp = time.strftime("%H:%M", time.localtime(a.ts))
            self.history.addItem(f"{fmt.date(a.ts)} {stamp}   {a.rule.name}   {self.ctl.alert_text(a)}")
        if not self.ctl.alert_log:
            self.history.addItem(t("rules.no_history"))
        n = self.ctl.notifier.available and self.ctl.settings.notifications
        self.notify_lbl.setText(t("rules.notify_on") if n else t("rules.notify_off"))

    # ── editing ──────────────────────────────────────────────────────────────
    def _rule_at(self, row: int) -> Rule | None:
        it = self.table.item(row, 1)
        rid = it.data(Qt.ItemDataRole.UserRole) if it else None
        return next((r for r in self.ctl.store.rules() if r.id == rid), None)

    def _save(self, rules: list[Rule]) -> None:
        self.ctl.save_rules(rules)
        self.render()

    def _add(self) -> None:
        from mazecrypto.ui.dialogs.rule_editor import RuleDialog

        d = RuleDialog(self.ctl, self, None)
        if d.exec() and d.rule:
            self._save([*self.ctl.store.rules(), d.rule])

    def _edit(self, row: int) -> None:
        from mazecrypto.ui.dialogs.rule_editor import RuleDialog

        rule = self._rule_at(row)
        if not rule:
            return
        d = RuleDialog(self.ctl, self, rule)
        if d.exec() and d.rule:
            self._save([d.rule if r.id == rule.id else r for r in self.ctl.store.rules()])

    def _item_changed(self, it) -> None:
        if it.column() == 0:
            self._enable(it.data(Qt.ItemDataRole.UserRole), it.checkState() == Qt.CheckState.Checked)

    def _enable(self, rid: str, on: bool) -> None:
        rules = self.ctl.store.rules()
        for r in rules:
            if r.id == rid:
                r.enabled = on
        self._save(rules)

    def _delete(self, row: int) -> None:
        rule = self._rule_at(row)
        if rule and confirm(self, t("rules.delete"), t("rules.delete_q", name=rule.name), t("btn.delete"), True):
            self._save([r for r in self.ctl.store.rules() if r.id != rule.id])

    def _menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        m = QMenu(self)
        m.addAction(t("btn.edit"), lambda: self._edit(row))
        m.addAction(t("btn.delete"), lambda: self._delete(row))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _pick_coin(self, title: str, default: str = "bitcoin") -> Coin | None:
        coins = self.ctl.tracked_coins()
        if not coins:
            return None
        names = [f"{c.symbol} · {c.name}" for c in coins]
        idx = next((i for i, c in enumerate(coins) if c.id == default), 0)
        choice, ok = QInputDialog.getItem(self, t("rules.templates"), title, names, idx, False)
        return coins[names.index(choice)] if ok else None

    def _template(self, key: str) -> None:
        coin, reserve = presets.BTC, presets.USDT
        if key != "rebalance":
            coin = self._pick_coin(t("rules.pick_coin"))
            if not coin:
                return
        if key == "dip_reserve":
            reserve = self._pick_coin(t("rules.pick_reserve"), "tether") or presets.USDT
        new = presets.rule_templates(coin, reserve)[key]
        self._save([*self.ctl.store.rules(), *new])

    def _test_notify(self) -> None:
        ok = self.ctl.notifier.send("Maze Crypto", t("rules.test_body"))
        self.notify_lbl.setText(t("rules.test_ok") if ok else t("rules.test_fail"))
