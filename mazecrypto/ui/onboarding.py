"""First-run wizard: language, currency, exchanges and privacy, watchlist and starting templates."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mazecrypto import APP_NAME, i18n
from mazecrypto.core import presets
from mazecrypto.core import settings as cfg
from mazecrypto.core.models import Coin
from mazecrypto.core.portfolio import OTHER
from mazecrypto.core.store import ProfileStore
from mazecrypto.i18n import t
from mazecrypto.ui.glass import Aurora
from mazecrypto.ui.pages.settings import LANG_NAMES, checked_exchanges, exchange_list
from mazecrypto.ui.theme import P
from mazecrypto.ui.widgets import button, label

_ICON = Path(__file__).resolve().parent.parent / "assets" / "icons" / "maze-crypto-64.png"
DEFAULT_EXCHANGES = {"TRY": ["btcturk", "binance"], "EUR": ["kraken", "coinbase"], "USD": ["coinbase", "kraken"],
                     "GBP": ["coinbase", "kraken"]}


class Onboarding(QDialog):
    STEPS = 4

    def __init__(self, settings: cfg.Settings):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(760, 600)
        self.s = replace(settings, watchlist=list(settings.watchlist), proxy=replace(settings.proxy))
        self.targets_choice = ""
        self.rule_choice = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.bg = Aurora(animated=False)
        outer.addWidget(self.bg)
        self.root = QVBoxLayout(self.bg)
        self.root.setContentsMargins(36, 28, 36, 24)
        self._build(0)

    # The whole wizard is rebuilt when the language changes.
    def _build(self, step: int) -> None:
        while self.root.count():
            it = self.root.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                QWidget().setLayout(it.layout())
        head = QHBoxLayout()
        head.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(_ICON)).scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))
        head.addWidget(logo)
        head.addWidget(label("Maze Crypto", "AppName"))
        head.addStretch()
        self.step_lbl = label("", "Badge")
        head.addWidget(self.step_lbl)
        self.root.addLayout(head)
        self.root.addSpacing(18)
        self.stack = QStackedWidget()
        for build in (self._p_welcome, self._p_currency, self._p_sources, self._p_watch):
            self.stack.addWidget(build())
        self.root.addWidget(self.stack, 1)
        self.err = label("", "Small")
        self.err.setStyleSheet(f"color: {P['down']};")
        self.root.addWidget(self.err)
        nav = QHBoxLayout()
        self.back = button(t("btn.back"), "", lambda: self._go(-1), icon_name="ph.arrow-left")
        self.next = button(t("btn.next"), "Primary", lambda: self._go(1))
        nav.addWidget(self.back)
        nav.addStretch()
        nav.addWidget(self.next)
        self.root.addLayout(nav)
        self._show(step)

    def _page(self, title: str, sub: str) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label(title, "PageTitle"))
        lay.addWidget(label(sub, "PageSub", wrap=True))
        lay.addSpacing(8)
        return w, lay

    def _p_welcome(self) -> QWidget:
        w, lay = self._page(t("wiz.welcome"), t("wiz.welcome_sub"))
        f = QFormLayout()
        self.lang = QComboBox()
        for code, name in LANG_NAMES.items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(self.s.language)))
        self.lang.activated.connect(self._lang_changed)
        f.addRow(label(t("set.language"), "CardKey"), self.lang)
        lay.addLayout(f)
        lay.addSpacing(10)
        lay.addWidget(label(t("about.disclaimer"), "Disclaimer", wrap=True))
        self.accept_box = QCheckBox(t("wiz.understand"))
        lay.addWidget(self.accept_box)
        lay.addWidget(label(t("about.keys_body"), "Small", wrap=True))
        lay.addStretch()
        return w

    def _p_currency(self) -> QWidget:
        w, lay = self._page(t("wiz.currency"), t("wiz.currency_sub"))
        f = QFormLayout()
        f.setSpacing(12)
        self.fiat = QComboBox()
        self.fiat.addItems(presets.FIATS)
        self.fiat.setCurrentText(self.s.fiat)
        self.fiat2 = QComboBox()
        self.fiat2.addItem(t("set.none"), "")
        for c in presets.FIATS:
            self.fiat2.addItem(c, c)
        self.fiat2.setCurrentIndex(max(0, self.fiat2.findData(self.s.secondary_fiat)))
        self.fiat.currentTextChanged.connect(self._fiat_changed)
        f.addRow(label(t("set.fiat"), "CardKey"), self.fiat)
        f.addRow(label(t("set.fiat2"), "CardKey"), self.fiat2)
        lay.addLayout(f)
        lay.addWidget(label(t("wiz.currency_note"), "Small", wrap=True))
        lay.addStretch()
        return w

    def _p_sources(self) -> QWidget:
        w, lay = self._page(t("wiz.sources"), t("set.exchanges_help"))
        self.ex = exchange_list(self.s.exchanges)
        lay.addWidget(self.ex)
        lay.addWidget(label(t("set.network").upper(), "SectionTitle"))
        self.proxy_on = QCheckBox(t("set.proxy_on"))
        self.proxy_on.setChecked(self.s.proxy.enabled)
        self.proxy_url = QLineEdit(self.s.proxy.url)
        lay.addWidget(self.proxy_on)
        lay.addWidget(self.proxy_url)
        lay.addWidget(label(t("set.proxy_help"), "Small", wrap=True))
        lay.addStretch()
        return w

    def _p_watch(self) -> QWidget:
        w, lay = self._page(t("wiz.watch"), t("wiz.watch_sub"))
        f = QFormLayout()
        f.setSpacing(12)
        self.preset = QComboBox()
        for key in presets.WATCHLISTS:
            self.preset.addItem(t(f"wiz.preset.{key}"), key)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        f.addRow(label(t("wiz.preset"), "CardKey"), self.preset)
        lay.addLayout(f)
        self.coins = QListWidget()
        lay.addWidget(self.coins, 1)
        lay.addWidget(label(t("wiz.watch_note"), "Small", wrap=True))
        lay.addWidget(label(t("wiz.templates").upper(), "SectionTitle"))
        f2 = QFormLayout()
        self.targets = QComboBox()
        self.targets.addItem(t("wiz.tpl_none"), "")
        for key in ("btc_heavy", "balanced"):
            self.targets.addItem(t(f"targets.tpl.{key}"), key)
        f2.addRow(label(t("targets.title"), "CardKey"), self.targets)
        self.rule_box = QCheckBox(t("wiz.rule_drift"))
        f2.addRow("", self.rule_box)
        lay.addLayout(f2)
        if self.s.watchlist:
            self._fill_coins(self.s.watchlist)
        else:
            self._preset_changed(0)
        return w

    # ── behaviour ────────────────────────────────────────────────────────────
    def _fill_coins(self, coins: list[Coin]) -> None:
        self.coins.clear()
        for c in coins:
            it = QListWidgetItem(f"{c.symbol:<6}  {c.name}")
            it.setData(Qt.ItemDataRole.UserRole, c)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self.coins.addItem(it)

    def _preset_changed(self, idx: int) -> None:
        self._fill_coins(presets.WATCHLISTS[self.preset.itemData(idx)])

    def _fiat_changed(self, fiat: str) -> None:
        if fiat in DEFAULT_EXCHANGES:
            self.s.exchanges = DEFAULT_EXCHANGES[fiat]
            self.ex.setParent(None)
            lay = self.stack.widget(2).layout()
            self.ex = exchange_list(self.s.exchanges)
            lay.insertWidget(3, self.ex)
        if fiat != "USD" and not self.fiat2.currentData():
            self.fiat2.setCurrentIndex(self.fiat2.findData("USD"))

    def _lang_changed(self, _idx: int) -> None:
        self._collect()
        accepted = self.accept_box.isChecked()
        i18n.set_language(self.s.language)
        self._build(0)
        self.accept_box.setChecked(accepted)

    def _collect(self) -> None:
        self.s.language = self.lang.currentData()
        self.s.fiat = self.fiat.currentText()
        self.s.secondary_fiat = self.fiat2.currentData() or ""
        self.s.exchanges = checked_exchanges(self.ex)
        self.s.proxy = cfg.ProxySettings(self.proxy_on.isChecked(), self.proxy_url.text().strip())
        self.s.watchlist = [self.coins.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.coins.count())
                            if self.coins.item(i).checkState() == Qt.CheckState.Checked]
        self.targets_choice = self.targets.currentData() or ""
        self.rule_choice = self.rule_box.isChecked()

    def _show(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self.step_lbl.setText(f"{i + 1} / {self.STEPS}")
        self.back.setVisible(i > 0)
        self.next.setText(t("btn.finish") if i == self.STEPS - 1 else t("btn.next"))
        self.err.setText("")

    def _go(self, step: int) -> None:
        i = self.stack.currentIndex()
        if step > 0 and i == 0 and not self.accept_box.isChecked():
            self.err.setText(t("wiz.need_accept"))
            return
        self._collect()
        if step > 0 and i == 2:
            if not self.s.exchanges:
                self.err.setText(t("wiz.need_exchange"))
                return
            try:
                self.s.proxy.validate()
            except ValueError as e:
                self.err.setText(str(e))
                return
        if step > 0 and i == self.STEPS - 1:
            self.s.onboarded = True
            try:
                self.s.validate()
            except ValueError as e:
                self.err.setText(str(e))
                return
            self.accept()
            return
        self._show(max(0, min(self.STEPS - 1, i + step)))

    def result_settings(self) -> cfg.Settings:
        return self.s

    def apply_to_store(self, store: ProfileStore) -> None:
        """Write the chosen templates into the profile, without overwriting existing data."""
        if self.targets_choice and not store.targets():
            tpl = presets.target_template(self.targets_choice)
            names = {c.id: c.symbol for wl in presets.WATCHLISTS.values() for c in wl}
            store.set_targets(tpl)
            store.set_target_symbols({cid: names.get(cid, cid.upper()) for cid in tpl if cid != OTHER})
        if self.rule_choice and not store.rules():
            store.set_rules(presets.rule_templates()["rebalance"])
