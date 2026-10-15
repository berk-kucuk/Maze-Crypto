"""Create or edit the recurring-buy plan."""
from __future__ import annotations

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from mazecrypto.core.dca import DcaExtra, DcaPlan, Frequency
from mazecrypto.core.models import Coin
from mazecrypto.i18n import t
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.theme import P
from mazecrypto.ui.widgets import Dialog, button, label


class DcaDialog(Dialog):
    def __init__(self, ctl: Controller, parent: QWidget | None, plan: DcaPlan | None):
        super().__init__(parent, t("dcaed.title"))
        self.ctl = ctl
        self.setMinimumWidth(600)
        fiat = ctl.settings.fiat
        self.coins: dict[str, Coin] = {c.id: c for c in ctl.tracked_coins()}

        form = QFormLayout()
        form.setSpacing(10)
        self.name = QLineEdit(plan.name if plan else "DCA")
        self.start = QDateEdit()
        self.start.setCalendarPopup(True)
        self.start.setDisplayFormat("yyyy-MM-dd")
        self.start.setDate(QDate(plan.start) if plan else QDate.currentDate())
        self.freq = QComboBox()
        for f in Frequency:
            self.freq.addItem(t(f"dcaed.freq.{f.value}"), f.value)
        if plan:
            self.freq.setCurrentIndex(list(Frequency).index(plan.frequency))
        self.periods = QSpinBox()
        self.periods.setRange(1, 520)
        self.periods.setValue(plan.periods if plan else 12)
        self.amount = QLineEdit(fmt.num(plan.amount, 2) if plan else "")
        self.amount.setPlaceholderText(t("dcaed.amount_ph", fiat=fiat))
        self.follow = QCheckBox(t("dcaed.follow"))
        self.follow.setChecked(bool(plan and plan.follow_targets))
        self.follow.setEnabled(bool(ctl.store.targets()))
        self.follow.toggled.connect(self._toggle_alloc)
        self.total = label("", "Small")
        for w in (self.periods, self.amount):
            (w.valueChanged if isinstance(w, QSpinBox) else w.textChanged).connect(self._sum)

        form.addRow(label(t("dcaed.name"), "CardKey"), self.name)
        form.addRow(label(t("dcaed.start"), "CardKey"), self.start)
        form.addRow(label(t("dcaed.freq"), "CardKey"), self.freq)
        form.addRow(label(t("dcaed.periods"), "CardKey"), self.periods)
        form.addRow(label(t("dcaed.amount", fiat=fiat), "CardKey"), self.amount)
        form.addRow("", self.total)
        form.addRow("", self.follow)
        self.content.addLayout(form)

        self.alloc_title = label(t("dcaed.alloc").upper(), "SectionTitle")
        self.content.addWidget(self.alloc_title)
        self.alloc_grid = QGridLayout()
        self.alloc_grid.setHorizontalSpacing(10)
        self.content.addLayout(self.alloc_grid)
        self.alloc_rows: list[tuple[QComboBox, QDoubleSpinBox]] = []
        self.add_alloc_btn = button((t("dcaed.add_coin")), "Link", lambda: self._add_alloc())
        self.content.addWidget(self.add_alloc_btn)
        self.alloc_sum = label("", "Small")
        self.content.addWidget(self.alloc_sum)

        self.content.addWidget(label(t("dcaed.extras").upper(), "SectionTitle"))
        self.content.addWidget(label(t("dcaed.extras_help"), "Small", wrap=True))
        self.extra_grid = QGridLayout()
        self.extra_grid.setHorizontalSpacing(10)
        self.content.addLayout(self.extra_grid)
        self.extra_rows: list[tuple[QSpinBox, QComboBox, QLineEdit]] = []
        self.content.addWidget(button((t("dcaed.add_extra")), "Link", lambda: self._add_extra()))

        if plan:
            for cid, pct in plan.allocation.items():
                self._add_alloc(cid, pct, plan.symbols.get(cid))
            for e in plan.extras:
                self._add_extra(e)
        elif not self.alloc_rows:
            self._add_alloc()
        self._toggle_alloc(self.follow.isChecked())
        self._sum()

    def _coin_combo(self, cid: str | None = None, symbol: str | None = None) -> QComboBox:
        if cid and cid not in self.coins:
            self.coins[cid] = Coin(cid, symbol or cid.upper(), symbol or cid)
        cb = QComboBox()
        for c in self.coins.values():
            cb.addItem(c.symbol, c.id)
        if cid:
            cb.setCurrentIndex(cb.findData(cid))
        return cb

    def _add_alloc(self, cid: str | None = None, pct: float = 0.0, symbol: str | None = None) -> None:
        cb = self._coin_combo(cid, symbol)
        sp = QDoubleSpinBox()
        sp.setRange(0, 100)
        sp.setDecimals(1)
        sp.setSuffix(" %")
        sp.setValue(pct)
        sp.valueChanged.connect(self._sum)
        r = len(self.alloc_rows)
        self.alloc_grid.addWidget(cb, r, 0)
        self.alloc_grid.addWidget(sp, r, 1)
        self.alloc_rows.append((cb, sp))
        self._sum()

    def _add_extra(self, e: DcaExtra | None = None) -> None:
        period = QSpinBox()
        period.setRange(1, 520)
        period.setPrefix("#")
        period.setValue((e.period + 1) if e else 1)
        cb = self._coin_combo(e.coin_id if e else None, e.symbol if e else None)
        amt = QLineEdit(fmt.num(e.amount, 2) if e else "")
        amt.setPlaceholderText(self.ctl.settings.fiat)
        r = len(self.extra_rows)
        self.extra_grid.addWidget(period, r, 0)
        self.extra_grid.addWidget(cb, r, 1)
        self.extra_grid.addWidget(amt, r, 2)
        self.extra_rows.append((period, cb, amt))

    def _toggle_alloc(self, follow: bool) -> None:
        for cb, sp in self.alloc_rows:
            cb.setEnabled(not follow)
            sp.setEnabled(not follow)
        self.add_alloc_btn.setEnabled(not follow)
        self._sum()

    def _sum(self) -> None:
        total = sum(sp.value() for _cb, sp in self.alloc_rows)
        ok = abs(total - 100) < 0.05 or self.follow.isChecked()
        self.alloc_sum.setText(t("targets.total", total=f"{total:.1f}") if not self.follow.isChecked()
                               else t("dcaed.follow_note"))
        self.alloc_sum.setStyleSheet(f"color: {P['up'] if ok else P['warn']};")
        try:
            a = fmt.parse_number(self.amount.text())
            self.total.setText(t("dcaed.total", total=fmt.money(a * self.periods.value(), self.ctl.settings.fiat, 0)))
        except ValueError:
            self.total.setText("")

    def validate(self) -> bool:
        try:
            amount = fmt.parse_number(self.amount.text())
            extras = [DcaExtra(p.value() - 1, cb.currentData(), self.coins[cb.currentData()].symbol,
                               fmt.parse_number(a.text())) for p, cb, a in self.extra_rows if a.text().strip()]
        except (ValueError, KeyError):
            self.show_error(t("trade.err_number"))
            return False
        alloc: dict[str, float] = {}
        for cb, sp in self.alloc_rows:
            if sp.value() > 0 and cb.currentData():
                alloc[cb.currentData()] = alloc.get(cb.currentData(), 0.0) + sp.value()
        plan = DcaPlan(self.name.text().strip()[:40] or "DCA", self.start.date().toPyDate(),
                       Frequency(self.freq.currentData()), self.periods.value(), amount, self.ctl.settings.fiat,
                       alloc, {cid: self.coins[cid].symbol for cid in alloc}, extras, self.follow.isChecked())
        try:
            plan.validate()
        except ValueError as e:
            self.show_error(t("dcaed.err", err=e))
            return False
        self.ctl.save_dca(plan)
        return True
