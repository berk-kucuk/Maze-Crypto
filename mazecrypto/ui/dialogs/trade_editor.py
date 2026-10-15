"""Add or edit one trade."""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QDate, QDateTime, QTime
from PyQt6.QtWidgets import QComboBox, QDateEdit, QFormLayout, QLineEdit, QPlainTextEdit, QWidget

from mazecrypto.core import validate as v
from mazecrypto.core.models import Coin, Trade
from mazecrypto.core.portfolio import build_positions
from mazecrypto.core.presets import FIATS
from mazecrypto.core.store import check_trade
from mazecrypto.i18n import t
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.widgets import Dialog, Segmented, label

SEARCH = "__search__"


class TradeDialog(Dialog):
    def __init__(self, ctl: Controller, parent: QWidget | None = None, trade: Trade | None = None):
        super().__init__(parent, t("trade.edit") if trade else t("trade.new"))
        self.ctl = ctl
        self.trade = trade
        self.setMinimumWidth(520)
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(form.labelAlignment())

        self.side = Segmented((("buy", t("trade.buy")), ("sell", t("trade.sell"))), trade.side if trade else "buy")
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setMaximumDate(QDate.currentDate())
        self.coin = QComboBox()
        self._fill_coins(trade)
        self.coin.activated.connect(self._coin_activated)
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("0.015")
        self.cost = QLineEdit()
        self.cost.setPlaceholderText(t("trade.cost_ph"))
        self.fiat = QComboBox()
        self.fiat.addItems(sorted(set(FIATS) | {ctl.settings.fiat}))
        self.fee = QLineEdit()
        self.fee.setPlaceholderText("0")
        self.rate = QLineEdit()
        self.platform = QComboBox()
        self.platform.setEditable(True)
        used = sorted({x.platform for x in ctl.store.trades() if x.platform})
        self.platform.addItems([""] + used)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText(t("trade.note_ph"))
        self.note.setFixedHeight(70)
        self.unit = label("", "Small")

        form.addRow(label(t("trade.side"), "CardKey"), self.side)
        form.addRow(label(t("trade.date"), "CardKey"), self.date)
        form.addRow(label(t("trade.coin"), "CardKey"), self.coin)
        form.addRow(label(t("trade.amount"), "CardKey"), self.amount)
        form.addRow(label(t("trade.cost"), "CardKey"), self.cost)
        form.addRow(label(t("trade.currency"), "CardKey"), self.fiat)
        form.addRow(label(t("trade.fee"), "CardKey"), self.fee)
        form.addRow(label(t("trade.rate"), "CardKey"), self.rate)
        form.addRow("", self.unit)
        form.addRow(label(t("trade.platform"), "CardKey"), self.platform)
        form.addRow(label(t("trade.note"), "CardKey"), self.note)
        self.content.addLayout(form)
        if not ctl.expert:
            # Basic mode: the USD rate is filled in with today's rate and kept out of sight.
            form.setRowVisible(self.rate, False)

        for w in (self.amount, self.cost):
            w.textChanged.connect(self._unit_price)
        self.fiat.currentTextChanged.connect(self._fiat_changed)

        if trade:
            self.date.setDate(QDateTime.fromSecsSinceEpoch(trade.ts).date())
            self.amount.setText(repr(trade.amount))
            self.cost.setText(repr(trade.cost))
            self.fee.setText(repr(trade.fee) if trade.fee else "")
            self.fiat.setCurrentText(trade.fiat)
            self.rate.setText(repr(trade.usd_rate) if trade.usd_rate else "")
            self.platform.setCurrentText(trade.platform)
            self.note.setPlainText(trade.note)
        else:
            self.date.setDate(QDate.currentDate())
            self.fiat.setCurrentText(ctl.settings.fiat)
            self._fiat_changed(ctl.settings.fiat)
        self._unit_price()

    def _fill_coins(self, trade: Trade | None) -> None:
        self.coin.clear()
        coins = self.ctl.tracked_coins()
        if trade and trade.coin_id not in {c.id for c in coins}:
            coins.append(Coin(trade.coin_id, trade.symbol, trade.symbol))
        for c in coins:
            self.coin.addItem(f"{c.symbol}  ·  {c.name}", c)
        self.coin.addItem(t("trade.other_coin"), SEARCH)
        if trade:
            self.coin.setCurrentIndex(next(i for i, c in enumerate(coins) if c.id == trade.coin_id))

    def _coin_activated(self, idx: int) -> None:
        if self.coin.itemData(idx) != SEARCH:
            return
        from mazecrypto.ui.dialogs.coin_search import CoinSearchDialog

        d = CoinSearchDialog(self.ctl, self)
        if d.exec() and d.coin:
            self.coin.insertItem(0, f"{d.coin.symbol}  ·  {d.coin.name}", d.coin)
            self.coin.setCurrentIndex(0)
        else:
            self.coin.setCurrentIndex(0)

    def _fiat_changed(self, fiat: str) -> None:
        # Pre-fill today's rate; the user should correct it for older trades.
        rate = self.ctl.snap.fx.get(fiat) if self.ctl.snap else None
        if rate and not self.trade:
            self.rate.setText(f"{rate:.4f}")
        self.rate.setPlaceholderText(t("trade.rate_ph", fiat=fiat))
        self._unit_price()

    def _unit_price(self) -> None:
        try:
            a, c = fmt.parse_number(self.amount.text()), fmt.parse_number(self.cost.text())
            self.unit.setText(t("trade.unit", price=fmt.money(c / a, self.fiat.currentText())) if a > 0 else "")
        except (ValueError, ZeroDivisionError):
            self.unit.setText(t("trade.rate_hint") if self.ctl.expert else "")

    def validate(self) -> bool:
        coin = self.coin.currentData()
        if not isinstance(coin, Coin):
            self.show_error(t("trade.err_coin"))
            return False
        try:
            amount = fmt.parse_number(self.amount.text())
            cost = fmt.parse_number(self.cost.text())
            fee = fmt.parse_number(self.fee.text()) if self.fee.text().strip() else 0.0
            rate = fmt.parse_number(self.rate.text()) if self.rate.text().strip() else None
        except ValueError:
            self.show_error(t("trade.err_number"))
            return False
        d = self.date.date().toPyDate()
        ts = int(datetime.combine(d, QTime.currentTime().toPyTime() if not self.trade else
                                  datetime.fromtimestamp(self.trade.ts).time()).timestamp())
        tr = Trade(ts, coin.id, coin.symbol, amount, cost, self.fiat.currentText(), fee, self.side.current(),
                   self.platform.currentText(), self.note.toPlainText(), rate,
                   self.trade.id if self.trade else None)
        others = [x for x in self.ctl.store.trades() if x.id != tr.id]
        rate_now = self.ctl.snap.fx.get(self.ctl.settings.fiat) if self.ctl.snap else None
        try:
            check_trade(tr)
            build_positions([*others, tr], self.ctl.settings.fiat, rate_now)
        except v.ValidationError as e:
            self.show_error(t("trade.err_invalid", err=e))
            return False
        except ValueError:
            # Selling more than held, or a currency that cannot be converted.
            self.show_error(t("trade.err_position"))
            return False
        if tr.id is None:
            self.ctl.store.add_trade(tr)
        else:
            self.ctl.store.update_trade(tr)
        self.trade = tr
        return True
