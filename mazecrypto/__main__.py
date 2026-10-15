"""Entry point: ``maze-crypto`` / ``python -m mazecrypto``."""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
from pathlib import Path

from mazecrypto import APP_ID, APP_NAME, __version__

ASSETS = Path(__file__).resolve().parent / "assets"


def _log_exception(etype, value, tb) -> None:
    # PyQt6 aborts the process on an exception escaping a slot unless the hook
    # is replaced. A bug in one widget must be logged, not crash the app.
    traceback.print_exception(etype, value, tb, file=sys.stderr)


def _screenshot_hook(app, win) -> None:
    """MAZE_CRYPTO_SCREENSHOT=out.png [MAZE_CRYPTO_PAGE=chart] -> save a window grab and quit (docs, QA)."""
    out = os.environ.get("MAZE_CRYPTO_SCREENSHOT")
    if not out:
        return
    from PyQt6.QtCore import QTimer

    page = os.environ.get("MAZE_CRYPTO_PAGE", "home")
    size = os.environ.get("MAZE_CRYPTO_SIZE")          # e.g. 1360x1600 for a full-page grab
    if size:
        w, h = (int(x) for x in size.split("x"))
        win.setMinimumSize(0, 0)
        win.resize(w, h)
    delay = int(os.environ.get("MAZE_CRYPTO_DELAY", "6000"))
    QTimer.singleShot(300, lambda: win.nav(page))

    def grab() -> None:
        win.grab().save(out)
        win.close()

    QTimer.singleShot(delay, grab)


def main() -> int:
    if "--version" in sys.argv:
        print(f"{APP_NAME} {__version__}")
        return 0
    sys.excepthook = _log_exception
    os.environ.setdefault("RESOURCE_NAME", APP_ID)     # WM_CLASS on X11 -> matches the .desktop file

    import qasync
    from PyQt6.QtGui import QFont, QFontDatabase, QIcon
    from PyQt6.QtWidgets import QApplication, QDialog

    from mazecrypto import i18n
    from mazecrypto.core import settings as cfg
    from mazecrypto.ui import theme

    app = QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("Maze")
    app.setDesktopFileName(APP_ID)
    for icon in (ASSETS / "icons" / "maze-crypto-256.png", Path("/usr/share/pixmaps/maze-crypto.png")):
        if icon.exists():
            app.setWindowIcon(QIcon(str(icon)))
            break
    families = set(QFontDatabase.families())
    font = QFont(next((f for f in theme.SANS_FAMILIES if f in families), "Sans Serif"), 10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)
    settings = cfg.load()
    i18n.set_language(settings.language)
    theme.set_accent(os.environ.get("MAZE_CRYPTO_ACCENT") or settings.accent)
    app.setStyleSheet(theme.build_qss())

    wiz = None
    if not settings.onboarded or "--onboarding" in sys.argv:
        from mazecrypto.ui.onboarding import Onboarding

        wiz = Onboarding(settings)
        if wiz.exec() != QDialog.DialogCode.Accepted:
            return 0
        settings = wiz.result_settings()
        cfg.save(settings)
        i18n.set_language(settings.language)

    asyncio.run(_run(app, settings, wiz), loop_factory=qasync.QEventLoop)
    return 0


async def _run(app, settings, wiz) -> None:
    from mazecrypto.ui.controller import Controller
    from mazecrypto.ui.window import MainWindow

    ctl = Controller(settings)
    if os.environ.get("MAZE_CRYPTO_SCREENSHOT"):
        ctl.settings.notifications = False
    if wiz is not None:
        wiz.apply_to_store(ctl.store)
    win = MainWindow(ctl)
    win.show()
    ctl.start()
    _screenshot_hook(app, win)

    # Quit through our own event, not QApplication.quit(): stopping the Qt loop
    # directly would kill the asyncio loop before the shutdown below has run.
    app.setQuitOnLastWindowClosed(False)
    closed = asyncio.Event()
    win.closed.connect(closed.set)
    await closed.wait()
    await ctl.shutdown()


if __name__ == "__main__":
    sys.exit(main())
