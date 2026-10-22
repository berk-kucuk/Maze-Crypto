"""Offscreen smoke test: every page and dialog builds and the main flows work, no network."""
import asyncio
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qasync = pytest.importorskip("qasync")
pytest.importorskip("pyqtgraph")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from mazecrypto import i18n  # noqa: E402
from mazecrypto.core import settings as cfg  # noqa: E402
from mazecrypto.core.models import Coin, CoinRow  # noqa: E402
from mazecrypto.providers.service import Snapshot  # noqa: E402


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    # As in the app: closing the window must not stop the Qt loop under asyncio.
    app.setQuitOnLastWindowClosed(False)
    return app


def test_pages_and_dialogs(app, tmp_path, monkeypatch):
    # One event loop for both languages: qasync cannot start a second loop on
    # the same QApplication.
    async def both() -> None:
        for lang, expert in (("en", False), ("tr", True)):
            monkeypatch.setenv("MAZE_CRYPTO_HOME", str(tmp_path / lang))
            i18n.set_language(lang)
            await scenario(lang, expert)

    async def scenario(lang: str, expert: bool) -> None:
        from mazecrypto.ui.controller import Controller
        from mazecrypto.ui.dialogs.dca_editor import DcaDialog
        from mazecrypto.ui.dialogs.rule_editor import RuleDialog
        from mazecrypto.ui.dialogs.targets_editor import TargetsDialog
        from mazecrypto.ui.dialogs.trade_editor import TradeDialog
        from mazecrypto.ui.onboarding import Onboarding
        from mazecrypto.ui.window import NAV, MainWindow

        btc = Coin("bitcoin", "BTC", "Bitcoin")
        s = cfg.Settings(language=lang, fiat="EUR", watchlist=[btc], onboarded=True, notifications=False,
                         expert=expert, ai_enabled=False)
        ctl = Controller(s)
        win = None
        try:
            win = MainWindow(ctl)
            # A synthetic snapshot instead of the network.
            ctl.snap = Snapshot([CoinRow(btc, 90000.0, 80000.0, "Kraken", "Kraken", 1.0, 2.0, 3.0, -20.0, 1.0,
                                         spark=[1.0, 2.0, 3.0])], {"USD": 1.0, "EUR": 0.9}, 0.0)
            ctl.snapshotReady.emit(ctl.snap)
            for _group, items in NAV:
                for key, _label, _icon, expert_only in items:
                    assert (key in win._navs) == (expert or not expert_only)
                    win.nav(key)
                    await asyncio.sleep(0)          # let Qt and the loop run; never processEvents() in a task
            assert ctl.tips and ctl.tips[-1].id.startswith("learn.")

            # Rebuilding the pages (colour / mode change) must leave no connection
            # behind into deleted widgets: a later signal would raise in a slot.
            errors: list[BaseException] = []
            old_hook = sys.excepthook
            sys.excepthook = lambda _t, e, _tb: errors.append(e)
            try:
                ctl.settings.accent = "violet"
                ctl.settings.expert = not expert
                ctl.settingsChanged.emit()
                await asyncio.sleep(0)
                for sig in (ctl.snapshotReady, ctl.enriched):
                    sig.emit(ctl.snap)
                ctl.portfolioChanged.emit()
                ctl.tipsChanged.emit(ctl.tips)
                await asyncio.sleep(0)
            finally:
                sys.excepthook = old_hook
                ctl.settings.expert = expert
            assert not errors, errors
            for tip in ctl.tips:
                title, body = ctl.tip_text(tip)
                assert "{" not in title + body and not title.startswith("tip.")

            d = TradeDialog(ctl)
            d.amount.setText("0.01")
            d.cost.setText("800")
            assert d.validate()
            assert len(ctl.store.trades()) == 1
            sell = TradeDialog(ctl)
            sell.side.set("sell")
            sell.amount.setText("1")
            sell.cost.setText("1")
            assert not sell.validate()                      # more than held

            # Wallets: a recovery phrase is refused and wiped; a Monero balance entered by hand counts.
            from mazecrypto.ui.dialogs.wallet_detail import WalletDetailDialog
            from mazecrypto.ui.dialogs.wallet_editor import WalletDialog

            wd = WalletDialog(ctl)
            wd.addr.setText("abandon " * 11 + "about")
            assert wd.secret == "seed" and wd.addr.text() == ""
            wd.addr.setText("andon about")                  # the rest of a typed phrase is dropped too
            assert wd.addr.text() == ""
            assert not wd.validate() and not ctl.wallets()
            xmr = "888tNkZrPN6JsEgekjMnABU4TBzc2Dt29EPAvkRxbANsAnjyPbb3iQ1YBRk1UXcdRsiKc9dhwMVgN5S9cQUiyoogDavup3H"
            wd.addr.setText(xmr)                             # a Monero address switches to the view-only form
            assert wd.mode.current() == "monero" and wd.xmr_addr.text() == xmr
            vw = WalletDialog(ctl)
            put: dict = {}
            ctl.monero.keys.put = lambda wid, k: put.__setitem__(wid, k)   # keep the test key out of the keyring
            vw.mode.set("monero")
            vw._mode_changed()
            vw.xmr_addr.setText("41jV8SmnZa2KwmkeSV5U7p8rdTtbbqLZfCfYFuUdEo44JT9uxEPAqP85xccmqnGArkLXzrGEzfp3aiViwfUg5e8GLX6LqU9")
            vw.restore.setText("2026-10-01")
            vw.view_key.setText("fb70e56298e51aede6b715b6fdc09c80a2f71b1ae3b542374bcb61eaa0cba008")   # the spend key
            assert not vw.validate() and vw.view_key.text() == "" and not put
            vw.view_key.setText("d41d25229ad2ea1c8d24fba56fa38a482259399b8adb73195c65b2fc8f6d6407")
            assert vw.validate() and len(put) == 1
            assert [w.monero_view for w in ctl.wallets()] == [True]
            ctl.delete_wallet(ctl.wallets()[0].id)
            wd.mode.set("manual")
            wd._mode_changed()
            wd.addr.setText(xmr)
            wd._select_coin(Coin("monero", "XMR", "Monero"))
            wd.amount.setText("2.5")
            assert wd.validate()
            assert [w.chain for w in ctl.wallets()] == ["monero"]
            assert ctl.wallet_holdings()["monero"] == ("XMR", 2.5)
            ed = WalletDialog(ctl)
            ed.addr.setText("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
            assert "hyperliquid" in ed.net_checks and ed.net_checks["ethereum"].isChecked()
            WalletDetailDialog(ctl, ctl.wallets()[0], win)
            win.nav("holdings")
            win.pages["holdings"].render()
            assert win.pages["holdings"].wal.rowCount() == 1
            await asyncio.sleep(0)

            TargetsDialog(ctl)
            DcaDialog(ctl, None, None)
            r = RuleDialog(ctl, None, None)
            assert not r.validate()                         # price rule without a price
            r.fields["price"]["value"].setText("100000")
            assert r.validate() and r.rule is not None

            wiz = Onboarding(cfg.Settings())
            wiz._go(1)
            assert wiz.stack.currentIndex() == 0            # consent required
        finally:
            # Always shut down, or a failed assert leaves tasks that block the loop.
            if win is not None:
                win.close()
            await ctl.shutdown()

    asyncio.run(both(), loop_factory=qasync.QEventLoop)
    i18n.set_language("en")
