"""One watched wallet: its address, balances and recent transfers."""

from __future__ import annotations

import contextlib

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QWidget

from mazecrypto.core import address as addr
from mazecrypto.core.wallet import Transfer, Wallet, is_synthetic
from mazecrypto.i18n import t
from mazecrypto.providers import wallets as chains
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.theme import P, tabular
from mazecrypto.ui.widgets import Dialog, NumItem, button, confirm, label, text_item


def wallet_title(w: Wallet) -> str:
    if w.label:
        return w.label
    if w.chain == "manual":
        return w.symbol
    info = chains.chain(w.chain)
    return info.name if info else w.chain


def open_url(url: str | None) -> None:
    if url:
        QDesktopServices.openUrl(QUrl(url))


def table(headers: list[str]) -> QTableWidget:
    tb = QTableWidget(0, len(headers))
    tb.setHorizontalHeaderLabels(headers)
    tb.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tb.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    tb.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tb.verticalHeader().hide()
    tb.verticalHeader().setDefaultSectionSize(34)
    tb.setShowGrid(False)
    tabular(tb)
    h = tb.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    h.setStretchLastSection(True)
    return tb


def fill_activity(tb: QTableWidget, transfers: list[Transfer], ctl: Controller, show_wallet: bool) -> None:
    """Date, type, amounts, value today, counterparty, wallet, status — one row per transfer."""
    fiat = ctl.settings.fiat
    prices = ctl.prices()[0]
    names = {w.id: wallet_title(w) for w in ctl.wallets()}
    kinds = {
        "in": ("wal.received", P["up"]),
        "out": ("wal.sent", P["down"]),
        "swap": ("wal.swap", P["accent"]),
        "self": ("wal.self", P["text_mid"]),
    }
    tb.setRowCount(len(transfers))
    for i, tr in enumerate(transfers):
        key, color = kinds[tr.direction]
        legs = "  ".join(
            f"{'+' if leg.amount > 0 else '−'}{fmt.amount(abs(leg.amount))} {leg.symbol}" for leg in tr.legs[:3]
        )
        if not legs and tr.fee:
            legs = f"−{fmt.amount(tr.fee)} {tr.fee_symbol}"
        value = sum(leg.amount * prices[leg.coin_id] for leg in tr.legs if leg.coin_id in prices)
        when = fmt.date(tr.ts) + " " + fmt.clock(tr.ts) if tr.ts else t("wal.unconfirmed")
        status = "" if tr.status == "ok" else t("wal.pending" if tr.status == "pending" else "wal.failed_tx")
        fee = f"{fmt.amount(tr.fee)} {tr.fee_symbol}" if tr.fee and not tr.fee_included else ""
        cells = [
            NumItem(when, tr.ts or 9e12, None, Qt.AlignmentFlag.AlignLeft),
            text_item(t(key), color),
            text_item(legs, P["up"] if tr.direction == "in" else None),
            NumItem(fmt.signed_money(value, fiat) if abs(value) >= 0.005 else "—", value, P["muted"]),
            text_item(addr.short(tr.counterparty) if tr.counterparty and tr.direction != "self" else "", P["text_mid"]),
            text_item(fee, P["muted"]),
            text_item(
                ", ".join(names.get(i_, "") for i_ in sorted(tr.wallet_ids)) if show_wallet else "", P["text_mid"]
            ),
            text_item(status, P["warn"] if tr.status == "pending" else P["down"]),
        ]
        cells[0].setData(Qt.ItemDataRole.UserRole, chains.tx_url(tr.chain, tr.txid))
        for c, it in enumerate(cells):
            tb.setItem(i, c, it)
    tb.setColumnHidden(6, not show_wallet)


def activity_headers() -> list[str]:
    return [
        t("trade.date"),
        t("wal.type"),
        t("wal.amounts"),
        t("wal.value_now"),
        t("wal.counterparty"),
        t("trade.fee"),
        t("wal.wallet"),
        t("wal.status"),
    ]


class WalletDetailDialog(Dialog):
    def __init__(self, ctl: Controller, wallet: Wallet, parent: QWidget | None = None):
        super().__init__(parent, wallet_title(wallet), t("btn.close"))
        self.cancel.hide()
        self.ctl = ctl
        self.w = wallet
        self.setMinimumSize(820, 640)
        info = chains.chain(wallet.chain)

        self.head = label("", "Mid", wrap=True)
        self.content.addWidget(self.head)
        self.extra = label("", "Small", wrap=True)
        self.extra.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.content.addWidget(self.extra)
        if wallet.address:
            a = label(wallet.address, "Mono")
            a.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.content.addWidget(a)
        row = QHBoxLayout()
        if wallet.address:
            row.addWidget(button(t("wal.copy"), "", self._copy, icon_name="ph.copy"))
        url = chains.explorer_url(wallet.chain, wallet.address) if wallet.address and not wallet.account else None
        if url:
            row.addWidget(button(t("wal.explorer"), "", lambda: open_url(url), icon_name="ph.arrow-square-out"))
        if not wallet.manual:
            row.addWidget(
                button(t("wal.refresh"), "", lambda: ctl.refresh_wallet(wallet.id), icon_name="ph.arrows-clockwise")
            )
        self.hist_btn = button("", "Link", self._history)
        self.hist_btn.hide()
        row.addWidget(self.hist_btn)
        row.addWidget(button(t("btn.edit"), "", self._edit, icon_name="ph.pencil-simple"))
        row.addWidget(button(t("btn.delete"), "", self._delete, icon_name="ph.trash"))
        row.addStretch()
        self.content.addLayout(row)

        self.content.addWidget(label(t("wal.balances").upper(), "SectionTitle"))
        self.bal = table([t("col.coin"), t("hold.amount"), t("hold.price"), t("hold.value"), t("wal.note")])
        self.bal.setMaximumHeight(220)
        self.content.addWidget(self.bal)
        self.note = label("", "Small", wrap=True)
        self.content.addWidget(self.note)

        self.content.addWidget(label(t("wal.activity").upper(), "SectionTitle"))
        self.act_note = label("", "Small", wrap=True)
        self.content.addWidget(self.act_note)
        self.act = table(activity_headers())
        self.act.cellDoubleClicked.connect(lambda r, _c: open_url(self.act.item(r, 0).data(Qt.ItemDataRole.UserRole)))
        self.content.addWidget(self.act, 1)
        if wallet.chain == "monero" and wallet.manual:
            self.act_note.setText(t("wal.xmr_note"))
            self.act.hide()
        elif wallet.manual:
            self.act_note.setText(t("wal.manual_note"))
            self.act.hide()
        elif info and not info.activity:
            self.act_note.setText(t("wal.no_history_chain_one", chain=info.name))
            self.act.hide()
        else:
            self.act_note.setText(t("status.updating"))
            self._load_activity()

        ctl.walletsChanged.connect(self._render)
        self.finished.connect(lambda _r: self._disconnect())
        self._render()

    def _disconnect(self) -> None:
        with contextlib.suppress(TypeError):
            self.ctl.walletsChanged.disconnect(self._render)

    def _render(self) -> None:
        w = next((x for x in self.ctl.wallets() if x.id == self.w.id), None)
        if w is None:
            return
        self.w = w
        fiat = self.ctl.settings.fiat
        prices = self.ctl.prices()[0]
        info = chains.chain(w.chain)
        bal, age, err = self.ctl.wallet_data.get(w.id, (None, None, None))
        bits = [info.name if info else w.chain, fmt.money(self.ctl.wallet_value(w), fiat, 2)]
        if not w.in_portfolio:
            bits.append(t("wal.watch_only"))
        if w.id in self.ctl.wallet_loading:
            bits.append(t("status.updating"))
        elif err:
            bits.append(t("wal.err_stale", err=err) if bal else t("wal.err_fetch", err=err))
        self.head.setText("  ·  ".join(bits))
        extra = []
        if w.device == "ledger":
            extra.append("Ledger")
        if w.account:
            extra.append(t("wal.script_" + w.script.replace("-", "_")))
            if w.path:
                extra.append(f"{t('wal.path')}: {w.path}")
            info2 = self.ctl.service.wallets.account_info(w)
            if info2:
                nxt, used = info2
                extra.append(t("wal.used_addresses") + f": {used}")
                if nxt:
                    extra.append(f"{t('wal.next_address')}: <tt>{nxt}</tt>")
        if w.monero_view:
            prog = self.ctl.monero.progress.get(w.id)
            st = self.ctl.monero.state(w.id)
            if prog and prog.phase == "back":
                extra.append(t("xmr.history_running", date=fmt.date(prog.ts)))
            elif prog:
                extra.append(t("xmr.scanning_long", h=prog.height, tip=prog.tip) + f" ({int(prog.fraction * 100)}%)")
            elif st and st.next:
                extra.append(f"{t('xmr.scanned_to')}: {st.next}")
            self.hist_btn.hide()
            if w.restore_height > 0:
                extra.append(f"{t('xmr.restore_height')}: {w.restore_height}")
            elif st and st.low:
                # No start date: history is read backwards from the day the wallet was added.
                n = len(st.outputs)
                if st.back_done and st.floor > 1:
                    extra.append(t("xmr.history_stopped", date=fmt.date(st.low_ts), n=n))
                    self.hist_btn.setText(t("xmr.history_resume"))
                    self.hist_btn.show()
                elif st.back_done:
                    extra.append(t("xmr.history_done", n=n))
                else:
                    extra.append(t("xmr.history_so_far", date=fmt.date(st.low_ts), n=n))
                    self.hist_btn.setText(t("xmr.history_stop"))
                    self.hist_btn.show()
            extra.append(t("xmr.view_b"))
        self.extra.setText("<br>".join(extra))
        self.extra.setVisible(bool(extra))
        rows = (
            [(w.coin_id, w.symbol, w.amount or 0.0, "")]
            if w.manual
            else [
                (
                    a.coin_id,
                    a.symbol,
                    a.amount,
                    ", ".join(
                        x
                        for x in (
                            a.name if a.name != a.symbol else "",
                            t("wal.token", c=addr.short(a.contract)) if a.contract else "",
                            t("wal.pending_amt", a=fmt.amount(a.pending)) if a.pending else "",
                        )
                        if x
                    ),
                )
                for a in (bal.assets if bal else ())
            ]
        )
        self.bal.setRowCount(len(rows))
        for i, (cid, sym, amount, note) in enumerate(rows):
            p = prices.get(cid)
            for c, it in enumerate(
                [
                    text_item(sym),
                    NumItem(fmt.amount(amount), amount),
                    NumItem(fmt.money(p, fiat), p),
                    NumItem(fmt.money(None if p is None else p * amount, fiat, 2), None if p is None else p * amount),
                    text_item(note, P["muted"]),
                ]
            ):
                self.bal.setItem(i, c, it)
        notes = []
        if bal and bal.ignored:
            notes.append(t("wal.ignored", n=bal.ignored))
        if bal and any(is_synthetic(a.coin_id) for a in bal.assets):
            notes.append(t("wal.indexer_price"))
        self.note.setText("  ".join(notes))
        self.note.setVisible(bool(notes))

    def _load_activity(self) -> None:
        def done(res: tuple[list[Transfer], list[str]]) -> None:
            lst, errors = res
            fill_activity(self.act, lst[:50], self.ctl, False)
            self.act_note.setText(
                t("wal.history_failed")
                if errors and not lst
                else t("wal.no_history")
                if not lst
                else t("wal.history_note")
            )

        self.ctl.run(
            self.ctl.wallet_activity([self.w.id]), done, lambda _e: self.act_note.setText(t("wal.history_failed"))
        )

    def _history(self) -> None:
        st = self.ctl.monero.state(self.w.id)
        resume = bool(st and st.back_done)
        self.ctl.monero.set_history(self.w.id, resume)
        if resume:
            self.ctl.sync_monero(force=True)
        self._render()

    def _copy(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(self.w.address)

    def _edit(self) -> None:
        from mazecrypto.ui.dialogs.wallet_editor import WalletDialog

        WalletDialog(self.ctl, self, self.w).exec()
        self._render()

    def _delete(self) -> None:
        if confirm(self, t("wal.delete_q"), t("wal.delete_b"), t("btn.delete"), danger=True):
            self.ctl.delete_wallet(self.w.id)
            self.accept()
