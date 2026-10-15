"""Edit the target allocation: one percentage per coin plus an "other" bucket."""
from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QPushButton, QWidget

from mazecrypto.core import presets
from mazecrypto.core.models import Coin
from mazecrypto.core.portfolio import OTHER, validate_targets
from mazecrypto.i18n import t
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.theme import P
from mazecrypto.ui.widgets import Dialog, button, label


def _spin(value: float = 0.0) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(0, 100)
    s.setDecimals(1)
    s.setSingleStep(1)
    s.setSuffix(" %")
    s.setValue(value)
    s.setFixedWidth(110)
    return s


class TargetsDialog(Dialog):
    def __init__(self, ctl: Controller, parent: QWidget | None = None):
        super().__init__(parent, t("targets.title"))
        self.ctl = ctl
        self.setMinimumWidth(520)
        self.content.addWidget(label(t("targets.help"), "Body", wrap=True))
        top = QHBoxLayout()
        self.template = QComboBox()
        self.template.addItem(t("targets.tpl_pick"), "")
        for key in ("btc_heavy", "balanced", "empty"):
            self.template.addItem(t(f"targets.tpl.{key}"), key)
        self.template.activated.connect(self._template)
        top.addWidget(self.template, 1)
        top.addWidget(button((t("targets.add_coin")), "", self._add_coin))
        self.content.addLayout(top)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(6)
        self.content.addLayout(self.grid)
        self.total = label("", "Mid")
        self.content.addWidget(self.total)
        self.rows: list[tuple[str, str, QDoubleSpinBox, QPushButton | None]] = []

        symbols = self.ctl.store.target_symbols()
        known = {c.id: c.symbol for c in ctl.tracked_coins()}
        targets = ctl.store.targets()
        for cid, pct in targets.items():
            if cid != OTHER:
                self._add_row(cid, symbols.get(cid) or known.get(cid, cid.upper()), pct)
        self.other = _spin(targets.get(OTHER, 0.0))
        self.other.valueChanged.connect(self._sum)
        self._render()

    def _add_row(self, cid: str, symbol: str, pct: float) -> None:
        if any(r[0] == cid for r in self.rows):
            return
        spin = _spin(pct)
        spin.valueChanged.connect(self._sum)
        rm = button("✕", "Link", lambda: self._remove(cid))
        self.rows.append((cid, symbol, spin, rm))

    def _remove(self, cid: str) -> None:
        for r in self.rows:
            if r[0] == cid:
                for w in (r[2], r[3]):
                    w.setParent(None)
        self.rows = [r for r in self.rows if r[0] != cid]
        self._render()

    def _render(self) -> None:
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        for i, (_cid, sym, spin, rm) in enumerate(self.rows):
            self.grid.addWidget(label(sym), i, 0)
            self.grid.addWidget(spin, i, 1)
            self.grid.addWidget(rm, i, 2)
        n = len(self.rows)
        other_lbl = label(t("targets.other"), "Mid")
        other_lbl.setToolTip(t("targets.other_tip"))
        self.grid.addWidget(other_lbl, n, 0)
        self.grid.addWidget(self.other, n, 1)
        self.grid.setColumnStretch(0, 1)
        self._sum()

    def _sum(self) -> None:
        total = sum(r[2].value() for r in self.rows) + self.other.value()
        color = P["up"] if abs(total - 100) < 0.05 else P["warn"] if total < 100 else P["down"]
        self.total.setText(t("targets.total", total=f"{total:.1f}"))
        self.total.setStyleSheet(f"color: {color};")

    def _add_coin(self) -> None:
        from mazecrypto.ui.dialogs.coin_search import CoinSearchDialog

        d = CoinSearchDialog(self.ctl, self, {r[0] for r in self.rows})
        if d.exec() and d.coin:
            self._add_row(d.coin.id, d.coin.symbol, 0.0)
            self._render()

    def _template(self, idx: int) -> None:
        key = self.template.itemData(idx)
        if not key:
            return
        names: dict[str, Coin] = {c.id: c for wl in presets.WATCHLISTS.values() for c in wl}
        for r in list(self.rows):
            self._remove(r[0])
        tpl = presets.target_template(key)
        for cid, pct in tpl.items():
            if cid != OTHER:
                self._add_row(cid, names[cid].symbol if cid in names else cid.upper(), pct)
        self.other.setValue(tpl.get(OTHER, 0.0))
        self._render()

    def validate(self) -> bool:
        targets = {cid: spin.value() for cid, _s, spin, _rm in self.rows}
        if self.other.value() > 0:
            targets[OTHER] = self.other.value()
        try:
            clean = validate_targets(targets)
        except ValueError:
            self.show_error(t("targets.err_sum"))
            return False
        self.ctl.store.set_targets(clean)
        self.ctl.store.set_target_symbols({cid: sym for cid, sym, _s, _r in self.rows})
        self.ctl.portfolioChanged.emit()
        return True
