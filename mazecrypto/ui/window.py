"""Frameless main window: aurora background, glass sidebar, top bar, page stack."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizeGrip,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mazecrypto import APP_NAME, __version__, i18n
from mazecrypto.i18n import t
from mazecrypto.ui import theme
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.glass import Aurora, Glass, icon
from mazecrypto.ui.theme import P
from mazecrypto.ui.widgets import button, label, repolish

_ICON = Path(__file__).resolve().parent.parent / "assets" / "icons" / "maze-crypto-64.png"
WINDOW_RADIUS = 14

# (group heading, [(page key, label key, icon, expert only)])
NAV = (
    ("nav.overview", (("home", "nav.home", "ph.house", False),
                      ("watchlist", "nav.watchlist", "ph.list-bullets", False),
                      ("chart", "nav.chart", "ph.chart-line-up", False),
                      ("insights", "nav.insights", "ph.chart-pie-slice", True))),
    ("nav.portfolio", (("holdings", "nav.holdings", "ph.wallet", False),
                       ("dca", "nav.dca", "ph.calendar-check", False),
                       ("rules", "nav.rules", "ph.bell", False))),
    ("nav.system", (("settings", "nav.settings", "ph.gear-six", False),
                    ("about", "nav.about", "ph.info", False))),
)


class _DragArea(QWidget):
    """Empty space that moves the frameless window; startSystemMove() works on Wayland and X11."""

    def __init__(self, window: MainWindow):
        super().__init__(window)
        self._window = window

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._window.windowHandle():
            self._window.windowHandle().startSystemMove()
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._window.toggle_maximize()
        super().mouseDoubleClickEvent(e)


class MainWindow(QMainWindow):
    closed = pyqtSignal()

    def __init__(self, ctl: Controller):
        super().__init__()
        self.ctl = ctl
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        # Transparent outside the rounded background, so the corners are really round.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("MainWin")
        self.setMinimumSize(1100, 700)
        self.resize(1360, 880)

        self.bg = Aurora(corner_radius=WINDOW_RADIUS)
        self.setCentralWidget(self.bg)
        root = QHBoxLayout(self.bg)
        root.setContentsMargins(12, 12, 6, 6)
        root.setSpacing(6)

        self.sidebar = Glass(padding=12, radius=16)
        self.sidebar.setFixedWidth(228)
        root.addWidget(self.sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        root.addLayout(right, 1)

        self._grip = QSizeGrip(self.bg)
        self._grip.setFixedSize(14, 14)
        self.pages: dict[str, QWidget] = {}
        self._navs: dict[str, QPushButton] = {}
        self._shortcuts: list[QShortcut] = []
        self._state = (i18n.language(), ctl.expert, ctl.settings.accent)
        self._build_sidebar()
        self._build_pages()
        self._global_shortcuts()
        ctl.status.connect(self._set_status)
        ctl.settingsChanged.connect(self._settings_changed)
        self.nav("home")

    # ── layout ───────────────────────────────────────────────────────────────
    def _build_topbar(self) -> QWidget:
        bar = _DragArea(self)
        bar.setFixedHeight(46)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(22, 4, 0, 0)
        lay.setSpacing(8)
        lay.addStretch()
        self._pill = label("…", "StatusPill")
        lay.addWidget(self._pill)
        self._proxy = label("Tor", "ProxyPill")
        self._proxy.setToolTip(t("status.proxy_tip"))
        lay.addWidget(self._proxy)
        self._refresh_btn = QPushButton()
        self._refresh_btn.setObjectName("WinBtn")
        self._refresh_btn.setIcon(icon("ph.arrows-clockwise", P["text_mid"]))
        self._refresh_btn.setToolTip(t("tip.refresh"))
        self._refresh_btn.clicked.connect(self.ctl.refresh)
        lay.addWidget(self._refresh_btn)
        lay.addSpacing(10)
        for ic, name, slot, tip in (("ph.minus", "WinBtn", self.showMinimized, t("tip.minimize")),
                                    ("ph.square", "WinBtn", self.toggle_maximize, t("tip.maximize")),
                                    ("ph.x", "WinClose", self.close, t("tip.close"))):
            b = QPushButton()
            b.setObjectName(name)
            b.setIcon(icon(ic, P["text_mid"]))
            b.setIconSize(QSize(14, 14))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            lay.addWidget(b)
        return bar

    def _build_sidebar(self) -> None:
        lay = self.sidebar.lay
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        lay.setSpacing(2)

        brand = _DragArea(self)
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(6, 4, 4, 14)
        bl.setSpacing(10)
        logo = QLabel()
        pm = QPixmap(str(_ICON))
        if not pm.isNull():
            dpr = self.devicePixelRatioF() or 1.0
            pm = pm.scaled(int(34 * dpr), int(34 * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            logo.setPixmap(pm)
        bl.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label("Maze Crypto", "AppName"))
        names.addWidget(label(t("app.tagline"), "AppTag"))
        bl.addLayout(names, 1)
        lay.addWidget(brand)

        self._navs = {}
        for head_key, items in NAV:
            visible = [it for it in items if self.ctl.expert or not it[3]]
            if not visible:
                continue
            h = label(t(head_key).upper(), "NavHeading")
            h.setContentsMargins(12, 12, 0, 4)
            lay.addWidget(h)
            for key, text_key, icon_name, _expert in visible:
                b = button("  " + t(text_key), "NavButton")
                b.setIcon(icon(icon_name, P["text_mid"]))
                b.setIconSize(QSize(18, 18))
                b.clicked.connect(lambda _=False, k=key: self.nav(k))
                self._navs[key] = b
                lay.addWidget(b)
        lay.addStretch()

        mode = QHBoxLayout()
        mode.setContentsMargins(10, 0, 6, 0)
        mode_lbl = label(t("mode.expert") if self.ctl.expert else t("mode.basic"), "ModePill")
        mode_lbl.setToolTip(t("mode.tip"))
        mode.addWidget(mode_lbl)
        mode.addStretch()
        lay.addLayout(mode)
        foot = label(f"v{__version__} · {t('app.readonly')}", "SideFoot")
        foot.setContentsMargins(12, 8, 0, 2)
        lay.addWidget(foot)
        for i, b in enumerate(self._navs.values(), 1):
            if i <= 9:
                b.setToolTip(f"Ctrl+{i}")
        self._nav_shortcuts()

    def _build_pages(self) -> None:
        from mazecrypto.ui.pages.about import AboutPage
        from mazecrypto.ui.pages.chart import ChartPage
        from mazecrypto.ui.pages.dca import DcaPage
        from mazecrypto.ui.pages.holdings import HoldingsPage
        from mazecrypto.ui.pages.home import HomePage
        from mazecrypto.ui.pages.insights import InsightsPage
        from mazecrypto.ui.pages.rules import RulesPage
        from mazecrypto.ui.pages.settings import SettingsPage
        from mazecrypto.ui.pages.watchlist import WatchlistPage

        for p in self.pages.values():
            if hasattr(p, "unlisten"):
                p.unlisten()
            self.stack.removeWidget(p)
            p.deleteLater()
        self.pages = {
            "home": HomePage(self.ctl, self),
            "watchlist": WatchlistPage(self.ctl, self),
            "chart": ChartPage(self.ctl),
            "insights": InsightsPage(self.ctl),
            "holdings": HoldingsPage(self.ctl),
            "dca": DcaPage(self.ctl),
            "rules": RulesPage(self.ctl),
            "settings": SettingsPage(self.ctl),
            "about": AboutPage(),
        }
        for p in self.pages.values():
            self.stack.addWidget(p)
        self._proxy.setVisible(self.ctl.settings.proxy.enabled)

    def _nav_shortcuts(self) -> None:
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts = [QShortcut(QKeySequence(f"Ctrl+{i}"), self, lambda k=key: self.nav(k))
                           for i, key in enumerate(self._navs, 1) if i <= 9]

    def _global_shortcuts(self) -> None:
        QShortcut(QKeySequence("F5"), self, self.ctl.refresh)
        QShortcut(QKeySequence("Ctrl+R"), self, self.ctl.refresh)
        QShortcut(QKeySequence("Ctrl+N"), self, lambda: self.open_action("holdings:add"))
        QShortcut(QKeySequence("Ctrl+K"), self, lambda: self.open_action("watchlist:add"))
        QShortcut(QKeySequence("Ctrl+,"), self, lambda: self.nav("settings"))
        QShortcut(QKeySequence("Ctrl+Q"), self, self.close)

    # ── behaviour ────────────────────────────────────────────────────────────
    def nav(self, key: str) -> None:
        if key not in self.pages:
            return
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        if hasattr(page, "on_show"):
            page.on_show()
        for k, b in self._navs.items():
            active = k == key
            b.setProperty("active", "true" if active else "false")
            repolish(b)

    def open_action(self, action: str) -> None:
        """Targets used by assistant tips and shortcuts: "page" or "page:detail"."""
        page, _, detail = action.partition(":")
        if page == "chart" and detail:
            coin = next((c for c in self.ctl.tracked_coins() if c.id == detail), None)
            if coin:
                self.open_chart(coin)
            return
        self.nav(page)
        target = self.pages.get(page)
        if target is not None and detail and hasattr(target, "open_detail"):
            target.open_detail(detail)

    def open_chart(self, coin) -> None:
        self.pages["chart"].show_coin(coin)
        self.nav("chart")

    def _set_status(self, state: str, text: str) -> None:
        self._pill.setText(text)
        self._pill.setProperty("state", state)
        repolish(self._pill)

    def _settings_changed(self) -> None:
        state = (i18n.language(), self.ctl.expert, self.ctl.settings.accent)
        self._proxy.setVisible(self.ctl.settings.proxy.enabled)
        if state == self._state:
            return
        if state[2] != self._state[2]:
            theme.set_accent(state[2])
            QApplication.instance().setStyleSheet(theme.build_qss())
            self.bg.update()
        # Language, mode or colour changed: rebuild the sidebar and pages instead of
        # patching every label and every expert-only control by hand.
        self._state = state
        current = next((k for k, p in self.pages.items() if p is self.stack.currentWidget()), "home")
        self._build_sidebar()
        self._build_pages()
        self.nav(current if current in self._navs else "home")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._grip.move(self.width() - 16, self.height() - 16)

    def toggle_maximize(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def closeEvent(self, e):
        self.ctl.timer.stop()
        super().closeEvent(e)
        self.closed.emit()
