"""Add or edit a watched wallet: a public address, or a balance entered by hand."""

from __future__ import annotations

import secrets
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGridLayout, QHBoxLayout, QLineEdit, QWidget

from mazecrypto.core import address as addr
from mazecrypto.core.models import Coin
from mazecrypto.core.wallet import Wallet
from mazecrypto.i18n import t
from mazecrypto.providers import wallets as chains
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.glass import Glass
from mazecrypto.ui.theme import P
from mazecrypto.ui.widgets import Dialog, Segmented, button, label

SEARCH = "__search__"


def clear_clipboard_if(text: str) -> None:
    """A secret was pasted: take it off the clipboard too, so it cannot be pasted anywhere else by mistake."""
    cb = QGuiApplication.clipboard()
    if cb is not None and cb.text().strip() == text.strip():
        cb.clear()


class WalletDialog(Dialog):
    def __init__(
        self, ctl: Controller, parent: QWidget | None = None, wallet: Wallet | None = None, coin: Coin | None = None
    ):
        super().__init__(parent, t("wal.edit") if wallet else t("wal.add"))
        self.ctl = ctl
        self.wallet = wallet
        self.setMinimumWidth(640)
        self.preset_chains = chains.chains_for_coin(coin.id) if coin else None
        start_manual = (
            wallet.manual
            if wallet
            else bool(self.preset_chains and all(c in ("manual", "monero") for c in self.preset_chains))
        )
        self.secret: str | None = None

        self.mode = Segmented(
            (("address", t("wal.mode_address")), ("manual", t("wal.mode_manual"))),
            "manual" if start_manual else "address",
        )
        self.mode.changed.connect(self._mode_changed)
        if wallet is None:
            self.content.addWidget(self.mode)

        self.warn = Glass(padding=14)
        self.warn.set_tint(P["down"])
        self.warn_text = label("", "Body", wrap=True)
        self.warn.lay.addWidget(self.warn_text)
        self.warn.hide()
        self.content.addWidget(self.warn)

        form = QFormLayout()
        form.setSpacing(10)
        self.form = form
        self.addr = QLineEdit()
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.addr.setFont(mono)
        self.addr.setPlaceholderText(t("wal.address_ph"))
        self.addr.setMaxLength(400)
        self.addr.textChanged.connect(self._changed)
        paste = button(t("wal.paste"), "", self._paste, icon_name="ph.clipboard-text")
        row = QHBoxLayout()
        row.addWidget(self.addr, 1)
        row.addWidget(paste)
        self.addr_row = QWidget()
        self.addr_row.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.addr_info = label(t("wal.address_help"), "Small", wrap=True)
        self.net_box = QWidget()
        self.net_lay = QGridLayout(self.net_box)
        self.net_lay.setHorizontalSpacing(18)
        self.net_lay.setVerticalSpacing(6)
        self.net_lay.setContentsMargins(0, 0, 0, 0)
        self.net_checks: dict[str, QCheckBox] = {}

        self.coin = QComboBox()
        for c in ctl.tracked_coins():
            self.coin.addItem(f"{c.symbol}  ·  {c.name}", c)
        self.coin.addItem(t("trade.other_coin"), SEARCH)
        self.coin.activated.connect(self._coin_activated)
        self.coin.currentIndexChanged.connect(lambda _i: self._manual_note())
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("0.5")
        self.label = QLineEdit()
        self.label.setMaxLength(40)
        self.label.setPlaceholderText(t("wal.label_ph"))
        self.in_portfolio = QCheckBox(t("wal.in_portfolio"))
        self.in_portfolio.setChecked(wallet.in_portfolio if wallet else True)
        self.manual_note = label("", "Small", wrap=True)

        self.addr_key = label(t("wal.address"), "CardKey")
        form.addRow(self.addr_key, self.addr_row)
        form.addRow("", self.addr_info)
        form.addRow(label(t("wal.network"), "CardKey"), self.net_box)
        form.addRow(label(t("trade.coin"), "CardKey"), self.coin)
        form.addRow(label(t("trade.amount"), "CardKey"), self.amount)
        form.addRow("", self.manual_note)
        form.addRow(label(t("wal.label"), "CardKey"), self.label)
        form.addRow("", self.in_portfolio)
        form.addRow("", label(t("wal.in_portfolio_help"), "Small", wrap=True))
        self.content.addLayout(form)

        safe = Glass(padding=14)
        safe.set_tint(P["up"])
        safe.lay.addWidget(label(t("wal.safe_t"), "Strong"))
        safe.lay.addWidget(label(t("wal.safe_b"), "Small", wrap=True))
        self.content.addWidget(safe)
        self.privacy = label(t("wal.privacy_b"), "Small", wrap=True)
        self.content.addWidget(self.privacy)

        if coin and start_manual:
            self._select_coin(coin)
        if wallet:
            self.label.setText(wallet.label)
            self.addr.setText(wallet.address)
            if wallet.manual:
                self._select_coin(Coin(wallet.coin_id, wallet.symbol, wallet.name or wallet.symbol))
                self.amount.setText(repr(wallet.amount) if wallet.amount is not None else "")
            else:
                self.addr.setReadOnly(True)
                self.addr_info.setText(t("wal.address_fixed"))
        # Inputs keep their natural height, and the dialog is never shorter than its content:
        # otherwise a window manager that sizes it from the (word-wrapped) hint squeezes the fields flat.
        for w in (self.addr, paste, self.coin, self.amount, self.label):
            w.setMinimumHeight(max(36, w.sizeHint().height()))
        self._mode_changed(self.mode.current())

    # ── helpers ──────────────────────────────────────────────────────────────
    def _select_coin(self, c: Coin) -> None:
        for i in range(self.coin.count()):
            d = self.coin.itemData(i)
            if isinstance(d, Coin) and d.id == c.id:
                self.coin.setCurrentIndex(i)
                return
        self.coin.insertItem(0, f"{c.symbol}  ·  {c.name}", c)
        self.coin.setCurrentIndex(0)

    def _coin_activated(self, idx: int) -> None:
        if self.coin.itemData(idx) != SEARCH:
            return
        from mazecrypto.ui.dialogs.coin_search import CoinSearchDialog

        d = CoinSearchDialog(self.ctl, self)
        if d.exec() and d.coin:
            self._select_coin(d.coin)
        else:
            self.coin.setCurrentIndex(0)

    def _manual(self) -> bool:
        return self.mode.current() == "manual"

    def _mode_changed(self, _key: str = "") -> None:
        manual = self._manual()
        for w in (self.coin, self.amount, self.manual_note):
            self.form.setRowVisible(w, manual)
        self.form.setRowVisible(self.net_box, False)     # shown once an address is recognised
        self.privacy.setVisible(not manual)
        self._manual_note()
        self._changed(self.addr.text())

    def _manual_note(self) -> None:
        c = self.coin.currentData()
        xmr = isinstance(c, Coin) and c.id == "monero"
        manual = self._manual()
        self.manual_note.setText(t("wal.xmr_note") if xmr else t("wal.manual_note"))
        # Manual: the field is an optional Monero address, or just a reference for any other coin.
        self.addr_key.setText(t("wal.address") if not manual or xmr else t("wal.reference"))
        self.addr.setPlaceholderText(
            t("wal.address_ph") if not manual else t("wal.xmr_ph") if xmr else t("wal.reference_ph")
        )
        self._refit()

    def _paste(self) -> None:
        cb = QGuiApplication.clipboard()
        self.secret = None
        self.warn.hide()
        if cb is not None:
            self.addr.setText(cb.text()[:400])

    def _show_secret(self, kind: str, text: str) -> None:
        clear_clipboard_if(text)
        self.secret = kind
        self.warn_text.setText(f"<b>{t('wal.secret_t')}</b><br>{t('wal.secret_' + kind)}<br>{t('wal.secret_b')}")
        self.warn.show()
        self.addr.blockSignals(True)
        self.addr.clear()
        self.addr.blockSignals(False)

    def _changed(self, text: str) -> None:
        if self.addr.isReadOnly():
            return
        # After a secret was caught the keyboard may still be typing the rest of the phrase: words
        # (letters only, or anything with a space) cannot be an address, drop them.
        if self.secret and text and (any(c.isspace() for c in text) or text.isalpha()):
            self.addr.blockSignals(True)
            self.addr.clear()
            self.addr.blockSignals(False)
            return
        r = addr.parse(text) if text.strip() else None
        if isinstance(r, addr.Secret):
            self._show_secret(r.kind, text)
            return
        if isinstance(r, addr.Ok):
            self.secret = None
            self.warn.hide()
            if r.chains == ("monero",) and not self._manual() and self.wallet is None:
                self.mode.set("manual")
                self._select_coin(Coin("monero", "XMR", "Monero"))
                self._mode_changed()
                return
        self.addr_info.setText(
            t(f"wal.bad_{r.reason}")
            if isinstance(r, addr.Bad)
            else t("wal.valid", a=addr.short(r.address, 8, 6))
            if isinstance(r, addr.Ok)
            else t("wal.reference_help")
            if self._manual()
            else t("wal.address_help")
        )
        self.addr_info.setStyleSheet(f"color: {P['down']};" if isinstance(r, addr.Bad) and not self._manual() else "")
        self._networks(r)

    def _refit(self) -> None:
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self) -> None:
        """As tall as the content needs at the current width (word-wrapped notes included)."""
        lay = self.layout()
        if lay is None:
            return
        lay.activate()
        need = lay.heightForWidth(self.width()) if lay.hasHeightForWidth() else lay.sizeHint().height()
        need = max(need, lay.minimumSize().height())
        self.setMinimumHeight(need)
        if self.height() < need:
            self.resize(self.width(), need)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._fit_height()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if e.oldSize().width() != e.size().width():
            self._refit()

    def _networks(self, r) -> None:
        while self.net_lay.count():
            it = self.net_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.net_checks.clear()
        show = isinstance(r, addr.Ok) and not self._manual() and not (self.wallet and not self.wallet.manual)
        self.form.setRowVisible(self.net_box, show)
        self._refit()
        if not show:
            return
        options = list(r.chains)
        if self.preset_chains:
            narrowed = [c for c in options if c in self.preset_chains]
            options = narrowed or options
        multi = len(options) > 1 and all(c in addr.EVM_CHAINS for c in options)
        for i, cid in enumerate(options):
            info = chains.chain(cid)
            cb = QCheckBox(info.name if info else cid)
            cb.setChecked(i == 0)
            if not multi:
                cb.toggled.connect(lambda on, me=cid: on and self._only(me))
            self.net_lay.addWidget(cb, i // 3, i % 3)
            self.net_checks[cid] = cb

    def _only(self, keep: str) -> None:
        for cid, cb in self.net_checks.items():
            if cid != keep:
                cb.setChecked(False)

    # ── save ────────────────────────────────────────────────────────────────
    def validate(self) -> bool:
        lbl = "".join(c for c in self.label.text().strip() if c.isprintable())[:40]
        now = int(time.time())
        w0 = self.wallet
        if w0 is not None and not w0.manual:
            w0.label, w0.in_portfolio = lbl, self.in_portfolio.isChecked()
            self.ctl.update_wallet(w0)
            return True
        if self._manual():
            c = self.coin.currentData()
            if not isinstance(c, Coin):
                self.show_error(t("trade.err_coin"))
                return False
            try:
                amount = fmt.parse_number(self.amount.text())
                if amount < 0:
                    raise ValueError
            except ValueError:
                self.show_error(t("trade.err_number"))
                return False
            ref = self.addr.text().strip()
            if ref:
                r = addr.parse(ref)
                if isinstance(r, addr.Secret):
                    self._show_secret(r.kind, ref)
                    return False
                if c.id == "monero" and not (isinstance(r, addr.Ok) and r.chains == ("monero",)):
                    self.show_error(t("wal.bad_not_xmr"))
                    return False
                ref = r.address if isinstance(r, addr.Ok) else ref[:120]
            ch = "monero" if c.id == "monero" else "manual"
            w = Wallet(
                w0.id if w0 else secrets.token_hex(8),
                ch,
                ref,
                lbl,
                c.id,
                c.symbol,
                c.name,
                amount,
                self.in_portfolio.isChecked(),
                w0.created if w0 else now,
            )
            if w0:
                self.ctl.update_wallet(w)
                return True
            err = self.ctl.add_wallets([w])
            if err:
                self.show_error(err)
            return err is None
        r = addr.parse(self.addr.text())
        if isinstance(r, addr.Secret):
            self._show_secret(r.kind, self.addr.text())
            return False
        if isinstance(r, addr.Bad):
            self.show_error(t(f"wal.bad_{r.reason}"))
            return False
        picked = [cid for cid, cb in self.net_checks.items() if cb.isChecked()]
        if not picked:
            self.show_error(t("wal.pick_network"))
            return False
        err = self.ctl.add_wallets(
            [
                Wallet(
                    secrets.token_hex(8), cid, r.address, lbl, in_portfolio=self.in_portfolio.isChecked(), created=now
                )
                for cid in picked
            ]
        )
        if err:
            self.show_error(err)
        return err is None
