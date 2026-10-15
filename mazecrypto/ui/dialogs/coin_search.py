"""Search CoinGecko and pick a coin."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QWidget

from mazecrypto.core.models import Coin
from mazecrypto.i18n import t
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.widgets import Dialog, label


class CoinSearchDialog(Dialog):
    def __init__(self, ctl: Controller, parent: QWidget | None = None, exclude: set[str] | None = None):
        super().__init__(parent, t("search.title"), t("btn.add"))
        self.ctl = ctl
        self.exclude = exclude or set()
        self.coin: Coin | None = None
        self.setMinimumSize(460, 420)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(t("search.placeholder"))
        self.list = QListWidget()
        self.hint = label(t("search.hint"), "Small", wrap=True)
        self.content.addWidget(self.edit)
        self.content.addWidget(self.list, 1)
        self.content.addWidget(self.hint)
        self._timer = QTimer(self, singleShot=True, interval=350)
        self._timer.timeout.connect(self._search)
        self.edit.textChanged.connect(lambda _: self._timer.start())
        self.edit.returnPressed.connect(self._enter)
        self.list.itemDoubleClicked.connect(lambda _: self._try_accept())
        self._seq = 0
        self.edit.setFocus()

    def _enter(self) -> None:
        if self.list.count() and not self.list.currentItem():
            self.list.setCurrentRow(0)
        if self.list.currentItem():
            self._try_accept()

    def _search(self) -> None:
        q = self.edit.text().strip()
        if len(q) < 2:
            self.list.clear()
            return
        self._seq += 1
        seq = self._seq
        self.hint.setText(t("search.searching"))
        self.ctl.run(self.ctl.service.search(q), lambda res: self._show(seq, res), lambda e: self._failed(seq, e))

    def _show(self, seq: int, results: list[tuple[Coin, int | None]]) -> None:
        if seq != self._seq:
            return
        self.list.clear()
        for coin, rank in results:
            it = QListWidgetItem(f"{coin.symbol:<8} {coin.name:<28} {'#' + str(rank) if rank else ''}")
            it.setData(Qt.ItemDataRole.UserRole, coin)
            if coin.id in self.exclude:
                it.setFlags(Qt.ItemFlag.NoItemFlags)
                it.setText(it.text() + "  ✓")
            self.list.addItem(it)
        self.hint.setText(t("search.hint") if results else t("search.none"))

    def _failed(self, seq: int, e: Exception) -> None:
        if seq == self._seq:
            self.hint.setText(t("search.failed", err=str(e)))

    def validate(self) -> bool:
        it = self.list.currentItem()
        if not it or not it.flags() & Qt.ItemFlag.ItemIsEnabled:
            self.show_error(t("search.pick"))
            return False
        self.coin = it.data(Qt.ItemDataRole.UserRole)
        return True
