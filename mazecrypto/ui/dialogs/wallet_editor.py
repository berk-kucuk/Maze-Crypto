"""Add or edit a watched wallet: a public address or HD account key, a view-only
Monero wallet, or a balance entered by hand."""

from __future__ import annotations

import secrets
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGridLayout, QHBoxLayout, QLineEdit, QWidget

from mazecrypto.core import address as addr
from mazecrypto.core import hd
from mazecrypto.core.models import Coin
from mazecrypto.core.wallet import Wallet
from mazecrypto.i18n import t
from mazecrypto.providers import monero
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


def parse_restore(text: str) -> int | None:
    """A block height, or a YYYY-MM-DD date turned into one; None when it is neither."""
    r = text.strip()
    if r.isdigit():
        n = int(r)
        return n if 0 < n < 100_000_000 else None
    try:
        return monero.height_at(int(time.mktime(time.strptime(r, "%Y-%m-%d")) - time.timezone))
    except ValueError:
        return None


def restore_row(edit: QLineEdit) -> QWidget:
    """When a Monero wallet started — optional. Empty = unknown: history is read backwards from today."""
    edit.setPlaceholderText(t("xmr.restore_unknown_ph"))
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(edit, 1)
    day = 86400

    def preset(key: str, days: int | None) -> None:
        def set_it() -> None:
            edit.setText("" if days is None else time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * day)))

        lay.addWidget(button(t(key), "Link", set_it))

    preset("xmr.restore_unknown", None)
    preset("xmr.new_wallet", 0)
    preset("xmr.restore_1m", 30)
    preset("xmr.restore_6m", 182)
    preset("xmr.restore_1y", 365)
    return w


class WalletDialog(Dialog):
    def __init__(
        self, ctl: Controller, parent: QWidget | None = None, wallet: Wallet | None = None, coin: Coin | None = None
    ):
        super().__init__(parent, t("wal.edit") if wallet else t("wal.add"))
        self.ctl = ctl
        self.wallet = wallet
        self.setMinimumWidth(640)
        self.preset_chains = chains.chains_for_coin(coin.id) if coin else None
        if wallet is not None:
            start = "manual" if wallet.manual else "address"
        elif coin and coin.id == "monero":
            start = "monero"
        elif self.preset_chains and all(c == "manual" for c in self.preset_chains):
            start = "manual"
        else:
            start = "address"
        self.secret: str | None = None

        self.mode = Segmented(
            (("address", t("wal.mode_address")), ("monero", "Monero"), ("manual", t("wal.mode_manual"))),
            start,
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

        self.ledger_btn = button(t("ledger.cta"), "", self._ledger, icon_name="ph.usb")
        if wallet is None:
            self.content.addWidget(self.ledger_btn)

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
        # HD account
        self.script = QComboBox()
        self.script.currentIndexChanged.connect(lambda _i: self._first_address())
        self.first_addr = label("", "Small", wrap=True)
        # Monero view-only
        self.xmr_addr = QLineEdit()
        self.xmr_addr.setFont(mono)
        self.xmr_addr.setPlaceholderText(t("wal.xmr_ph"))
        self.view_key = QLineEdit()
        self.view_key.setFont(mono)
        self.view_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.view_key.setMaxLength(64)
        self.view_key.setPlaceholderText(t("xmr.view_key_ph"))
        self.restore = QLineEdit()
        self.restore.textChanged.connect(lambda _t: self._restore_hint())
        self.restore_row = restore_row(self.restore)
        self.restore_note = label(t("xmr.restore_auto"), "Small", wrap=True)
        self.xmr_note = label(
            t("xmr.tor_b", host=ctl.settings.tor_host, port=ctl.settings.tor_port) + "<br><br>" + t("xmr.view_b"),
            "Small",
            wrap=True,
        )

        self.addr_key = label(t("wal.address"), "CardKey")
        form.addRow(self.addr_key, self.addr_row)
        form.addRow("", self.addr_info)
        form.addRow(label(t("wal.network"), "CardKey"), self.net_box)
        form.addRow(label(t("wal.script"), "CardKey"), self.script)
        form.addRow("", self.first_addr)
        form.addRow(label(t("xmr.address"), "CardKey"), self.xmr_addr)
        form.addRow(label(t("xmr.view_key"), "CardKey"), self.view_key)
        form.addRow(label(t("xmr.restore"), "CardKey"), self.restore_row)
        form.addRow("", self.restore_note)
        form.addRow("", self.xmr_note)
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

        if coin and start == "manual":
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

    def _xmr(self) -> bool:
        return self.mode.current() == "monero"

    def _ledger(self) -> None:
        from mazecrypto.ui.dialogs.ledger_dialog import LedgerDialog

        if LedgerDialog(self.ctl, self).exec():
            self.reject()

    def _restore_height(self) -> int | None:
        return parse_restore(self.restore.text())

    def _restore_hint(self) -> None:
        h = self._restore_height()
        empty = not self.restore.text().strip()
        self.restore_note.setText(
            t("xmr.restore_auto") if empty else t("xmr.restore_from", h=h) if h else t("xmr.err_restore")
        )

    def _first_address(self) -> None:
        r = addr.parse(self.addr.text()) if self.addr.text().strip() else None
        k = hd.parse(r.xpub) if isinstance(r, addr.Account) else None
        ch = next((c for c, cb in self.net_checks.items() if cb.isChecked()), None)
        sc = self.script.currentData()
        a = None
        if k and ch and sc:
            try:
                a = hd.address(k, ch, sc, 0, 0)
            except ValueError:
                a = None
        self.first_addr.setText(t("wal.account_b") + (f"<br><b>{t('wal.first_address')}</b> <tt>{a}</tt>" if a else ""))

    def _mode_changed(self, _key: str = "") -> None:
        manual = self._manual()
        xmr = self._xmr()
        for w in (self.addr_row, self.addr_info):
            self.form.setRowVisible(w, not xmr)
        for w in (self.xmr_addr, self.view_key, self.restore_row, self.restore_note, self.xmr_note):
            self.form.setRowVisible(w, xmr)
        self.ledger_btn.setVisible(self.wallet is None and self.mode.current() == "address")
        for w in (self.coin, self.amount, self.manual_note):
            self.form.setRowVisible(w, manual)
        self.form.setRowVisible(self.net_box, False)  # shown once an address is recognised
        self.form.setRowVisible(self.script, False)
        self.form.setRowVisible(self.first_addr, False)
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
                self.mode.set("monero")
                self.xmr_addr.setText(r.address)
                self.addr.blockSignals(True)
                self.addr.clear()
                self.addr.blockSignals(False)
                self._mode_changed()
                return
        if isinstance(r, addr.Account):
            self.secret = None
            self.warn.hide()
        self.addr_info.setText(
            t(f"wal.bad_{r.reason}")
            if isinstance(r, addr.Bad)
            else t("wal.valid", a=addr.short(r.address, 8, 6))
            if isinstance(r, addr.Ok)
            else t("wal.valid_account")
            if isinstance(r, addr.Account)
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
        show = (
            isinstance(r, addr.Ok | addr.Account)
            and not self._manual()
            and not (self.wallet and not self.wallet.manual)
        )
        acct = isinstance(r, addr.Account) and show
        self.form.setRowVisible(self.script, acct)
        self.form.setRowVisible(self.first_addr, acct)
        if acct:
            self.script.blockSignals(True)
            self.script.clear()
            opts = [r.script] if r.script else (["pkh"] if r.chains == ("litecoin",) else ["pkh", "tr"])
            for sc in opts:
                self.script.addItem(t("wal.script_" + sc.replace("-", "_")), sc)
            self.script.blockSignals(False)
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
            cb.toggled.connect(lambda _on: self._first_address())
        if acct:
            self._first_address()

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
        if self._xmr():
            return self._save_monero(lbl, now)
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
        if isinstance(r, addr.Account):
            sc = self.script.currentData()
            if not sc:
                self.show_error(t("wal.pick_script"))
                return False
            err = self.ctl.add_wallets(
                [
                    Wallet(
                        secrets.token_hex(8),
                        picked[0],
                        r.xpub,
                        lbl,
                        in_portfolio=self.in_portfolio.isChecked(),
                        created=now,
                        script=sc,
                    )
                ]
            )
            if err:
                self.show_error(err)
            return err is None
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

    def _save_monero(self, lbl: str, now: int) -> bool:
        a = addr.clean(self.xmr_addr.text())
        r = addr.parse(a)
        if not (isinstance(r, addr.Ok) and r.chains == ("monero",)):
            self.show_error(t("wal.bad_not_xmr"))
            return False
        h = 0 if not self.restore.text().strip() else self._restore_height()  # empty: read history backwards
        if h is None:
            self.show_error(t("xmr.err_restore"))
            return False
        key = self.view_key.text().strip().lower()
        problem = monero.check_view_key(a, key)
        if problem == "spend_key":
            clear_clipboard_if(self.view_key.text())
            self.view_key.clear()
            self.warn_text.setText(f"<b>{t('wal.secret_t')}</b><br>{t('wal.secret_spend_key')}<br>{t('wal.secret_b')}")
            self.warn.show()
            return False
        if problem:
            self.show_error(t(f"xmr.key_{problem}"))
            return False
        clear_clipboard_if(self.view_key.text())
        w = Wallet(
            secrets.token_hex(8),
            "monero",
            a,
            lbl,
            "monero",
            "XMR",
            "Monero",
            in_portfolio=self.in_portfolio.isChecked(),
            created=now,
            view=True,
            restore_height=h,
        )
        self.ctl.monero.keys.put(w.id, bytes.fromhex(key))
        self.view_key.clear()
        err = self.ctl.add_wallets([w])
        if err:
            self.ctl.monero.forget(w.id)
            self.show_error(err)
        return err is None
