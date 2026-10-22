"""Add accounts from a Ledger in one session: pick every coin wanted, connect
once, and the app opens each coin's app on the device in turn (the user only
confirms "open"), reading Ledger Live's accounts of each. Read-only."""

from __future__ import annotations

import asyncio
import secrets
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QGridLayout, QHBoxLayout, QLineEdit, QTableWidgetItem, QWidget

from mazecrypto.core import address as addr
from mazecrypto.core import hd
from mazecrypto.core.wallet import Wallet
from mazecrypto.hw import discovery
from mazecrypto.hw import ledger as lw
from mazecrypto.i18n import t
from mazecrypto.providers import wallets as chains
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.dialogs.wallet_detail import table
from mazecrypto.ui.dialogs.wallet_editor import parse_restore, restore_row
from mazecrypto.ui.glass import Glass
from mazecrypto.ui.theme import P
from mazecrypto.ui.widgets import Dialog, button, label, text_item


def script_name(s: str) -> str:
    return t("wal.script_" + s.replace("-", "_"))


class LedgerDialog(Dialog):
    def __init__(self, ctl: Controller, parent: QWidget | None = None):
        super().__init__(parent, t("ledger.title"), t("ledger.add"))
        self.ctl = ctl
        self.setMinimumSize(860, 640)
        self.lg: lw.Ledger | None = None
        self.found: list[lw.Found] = []
        self.task = None

        safe = Glass(padding=14)
        safe.set_tint(P["up"])
        safe.lay.addWidget(label(t("ledger.safe_t"), "Strong"))
        safe.lay.addWidget(label(t("ledger.safe_b"), "Small", wrap=True))
        self.content.addWidget(safe)

        self.content.addWidget(label(t("ledger.coins").upper(), "SectionTitle"))
        grid = QGridLayout()
        self.coin_checks: dict[str, QCheckBox] = {}
        for i, (cid, _app, coin) in enumerate(lw.NETWORKS):
            info = chains.chain(cid)
            cb = QCheckBox(f"{coin} · {info.name if info else cid}")
            cb.setChecked(cid in ("bitcoin", "ethereum"))
            cb.toggled.connect(lambda _on: self._extras())
            grid.addWidget(cb, i // 3, i % 3)
            self.coin_checks[cid] = cb
        self.content.addLayout(grid)
        self.content.addWidget(label(t("ledger.coins_help"), "Small", wrap=True))

        self.evm_box = QWidget()
        eg = QGridLayout(self.evm_box)
        eg.setContentsMargins(0, 0, 0, 0)
        self.evm_checks: dict[str, QCheckBox] = {}
        eg.addWidget(label(t("ledger.evm_nets"), "Small"), 0, 0, 1, 4)
        for i, cid in enumerate(addr.EVM_CHAINS):
            info = chains.chain(cid)
            cb = QCheckBox(info.name if info else cid)
            cb.setChecked(cid == "ethereum")
            eg.addWidget(cb, 1 + i // 4, i % 4)
            self.evm_checks[cid] = cb
        self.content.addWidget(self.evm_box)

        self.xmr_box = QWidget()
        xr = QHBoxLayout(self.xmr_box)
        xr.setContentsMargins(0, 0, 0, 0)
        self.restore = QLineEdit()
        xr.addWidget(label(t("xmr.restore"), "CardKey"))
        xr.addWidget(restore_row(self.restore), 1)
        self.content.addWidget(self.xmr_box)
        self.xmr_help = label(t("xmr.restore_auto"), "Small", wrap=True)
        self.content.addWidget(self.xmr_help)

        self.content.addWidget(label(t("ledger.steps_multi"), "Small", wrap=True))
        row = QHBoxLayout()
        self.read_btn = button(t("ledger.connect"), "Primary", self._read, icon_name="ph.usb")
        self.verify_btn = button(t("ledger.verify"), "", self._verify, icon_name="ph.check-circle")
        self.verify_btn.setEnabled(False)
        row.addWidget(self.read_btn)
        row.addWidget(self.verify_btn)
        row.addStretch()
        self.content.addLayout(row)
        self.status = label("", "Body", wrap=True)
        self.content.addWidget(self.status)

        self.tb = table(
            ["", t("col.coin"), t("ledger.account"), t("wal.type"), t("wal.address"), t("wal.path"), t("wal.status")]
        )
        self.tb.itemSelectionChanged.connect(
            lambda: self.verify_btn.setEnabled(bool(self.tb.selectedItems()) and self.lg is not None)
        )
        self.content.addWidget(self.tb, 1)
        self.ok.setEnabled(False)
        self.finished.connect(lambda _r: self._close())
        self._extras()

    def _extras(self) -> None:
        self.evm_box.setVisible(self.coin_checks["ethereum"].isChecked())
        x = self.coin_checks["monero"].isChecked()
        self.xmr_box.setVisible(x)
        self.xmr_help.setVisible(x)

    def _close(self) -> None:
        if self.task:
            self.task.cancel()
        if self.lg:
            self.lg.close()
            self.lg = None

    def _say(self, text: str, color: str | None = None) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};" if color else "")

    def _msg(self, e: Exception, chain: str = "") -> str:
        if isinstance(e, lw.LedgerError):
            return t(f"ledger.err_{e.code}", app=lw.APP.get(chain, ""))
        return t("ledger.err_other", err=str(e) or type(e).__name__)

    async def _ensure(self, want: str) -> lw.Ledger:
        def status(_kind: str) -> None:  # called from the device thread: no widget access here
            pass

        if self.lg is not None:
            try:
                name, _v = await asyncio.to_thread(self.lg.app)
                if name == want:
                    return self.lg
            except lw.LedgerError:
                pass
            self.lg.close()
            self.lg = None
        self._say(t("ledger.opening", app=want), P["accent"])
        self.lg = await asyncio.to_thread(lw.connect, want, status)
        return self.lg

    def _read(self) -> None:
        nets = [c for c, cb in self.coin_checks.items() if cb.isChecked()]
        if not nets:
            self._say(t("ledger.pick_coins"), P["down"])
            return
        if lw.find() is None:
            self._say(t("ledger.err_no_device") + " " + t("ledger.udev_hint"), P["down"])
            return
        self.read_btn.setEnabled(False)
        self.ok.setEnabled(False)
        self.found = []
        self._fill()

        async def go() -> list[str]:
            skipped = []
            svc = self.ctl.service.wallets
            for net in [c for c, _a, _k in lw.NETWORKS if c in nets]:
                try:
                    lg = await self._ensure(lw.APP[net])
                    self._say(t("ledger.reading", app=lw.APP[net]), P["accent"])
                    done = list(self.found)

                    def show(lst: list[lw.Found], done: list[lw.Found] = done) -> None:
                        self.found = done + lst
                        self._fill()

                    await discovery.discover(lg, net, svc, show)
                except lw.LedgerError as e:
                    if e.code in ("not_installed", "denied", "wrong_app", "bad_request", "status", "xmr_view_declined"):
                        skipped.append(self._msg(e, net))
                        continue
                    raise
            return skipped

        def done(skipped: list[str]) -> None:
            self.read_btn.setEnabled(True)
            self.ok.setEnabled(bool(self.found))
            self._say(
                t("ledger.done", n=len(self.found))
                + ("<br>" + t("ledger.skipped") + " " + "; ".join(skipped) if skipped else ""),
                P["warn"] if skipped else P["up"],
            )

        def fail(e: Exception) -> None:
            self.read_btn.setEnabled(True)
            self.ok.setEnabled(bool(self.found))
            self._say(self._msg(e), P["down"])
            if self.lg:
                self.lg.close()
                self.lg = None

        self.task = self.ctl.run(go(), done, fail)

    def _owned(self) -> set[str]:
        return {addr.key(w.chain, w.address) for w in self.ctl.wallets()}

    def _fill(self) -> None:
        owned = self._owned()
        self.tb.setRowCount(len(self.found))
        for i, f in enumerate(self.found):
            have = addr.key(f.chain, f.address) in owned
            chk = QTableWidgetItem()
            chk.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled if not have else Qt.ItemFlag.NoItemFlags
            )
            pre = not have and (
                f.used is True or f.chain == "monero" or (i == 0 and not any(x.used for x in self.found))
            )
            chk.setCheckState(Qt.CheckState.Checked if pre else Qt.CheckState.Unchecked)
            state = (
                t("ledger.added")
                if have
                else t("ledger.used")
                if f.used
                else t("ledger.new")
                if f.used is False
                else ""
            )
            if f.balance:
                state += f" · {fmt.amount(f.balance)}"
            coin = next(k for c, _a, k in lw.NETWORKS if c == f.chain)
            cells = [
                chk,
                text_item(coin),
                text_item(t("xmr.view_badge") if f.chain == "monero" else t("ledger.account_n", n=f.index + 1)),
                text_item(script_name(f.script) if f.script else "", P["text_mid"]),
                text_item(addr.short(f.address, 12, 8), P["text_mid"]),
                text_item(hd.path_str(f.path) if f.path else "", P["muted"]),
                text_item(state, P["up"] if f.used else P["muted"]),
            ]
            for c, it in enumerate(cells):
                self.tb.setItem(i, c, it)

    def _verify(self) -> None:
        rows = {i.row() for i in self.tb.selectedItems()}
        if not rows:
            return
        f = self.found[min(rows)]

        async def go() -> None:
            lg = await self._ensure(lw.APP[f.chain])
            self._say(t("ledger.verify_on_device"), P["accent"])
            await asyncio.to_thread(lw.verify, lg, f)

        self.ctl.run(
            go(),
            lambda _r: self._say(t("ledger.verified"), P["up"]),
            lambda e: self._say(self._msg(e, f.chain), P["down"]),
        )

    def validate(self) -> bool:
        owned = self._owned()
        now = int(time.time())
        picked = [
            f
            for i, f in enumerate(self.found)
            if (it := self.tb.item(i, 0)) is not None and it.checkState() == Qt.CheckState.Checked
        ]
        if not picked:
            self.show_error(t("ledger.nothing"))
            return False
        height = 0
        if any(f.chain == "monero" for f in picked):
            # Empty = unknown: the wallet's history is read backwards from today.
            parsed = parse_restore(self.restore.text()) if self.restore.text().strip() else 0
            if parsed is None:
                self.show_error(t("xmr.err_restore"))
                return False
            height = parsed
        new: list[Wallet] = []
        keys: dict[str, bytes] = {}
        for f in picked:
            if f.chain == "ethereum":
                nets = [c for c, cb in self.evm_checks.items() if cb.isChecked()] or ["ethereum"]
            else:
                nets = [f.chain]
            for ch in nets:
                if addr.key(ch, f.address) in owned:
                    continue
                wid = secrets.token_hex(8)
                if ch == "monero":
                    new.append(
                        Wallet(
                            wid,
                            "monero",
                            f.address,
                            "Ledger · Monero",
                            "monero",
                            "XMR",
                            "Monero",
                            created=now,
                            device="ledger",
                            view=True,
                            restore_height=height,
                        )
                    )
                    if f.view_key:
                        keys[wid] = f.view_key
                else:
                    info = chains.chain(ch)
                    kind = script_name(f.script) if f.script else (info.name if info else ch)
                    new.append(
                        Wallet(
                            wid,
                            ch,
                            f.address,
                            f"Ledger · {kind} #{f.index + 1}"[:40],
                            created=now,
                            script=f.script,
                            path=hd.path_str(f.path),
                            device="ledger",
                        )
                    )
        if not new:
            self.show_error(t("ledger.nothing"))
            return False
        for wid, k in keys.items():
            self.ctl.monero.keys.put(wid, k)
        err = self.ctl.add_wallets(new)
        if err:
            for wid in keys:
                self.ctl.monero.forget(wid)
            self.show_error(err)
            return False
        return True
