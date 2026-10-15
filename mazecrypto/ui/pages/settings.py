"""Settings: language, currency, exchanges, refresh, notifications, proxy, profiles."""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import replace

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mazecrypto.core import settings as cfg
from mazecrypto.core.presets import FIATS
from mazecrypto.i18n import t
from mazecrypto.providers.exchanges import EXCHANGES
from mazecrypto.providers.http import Http
from mazecrypto.providers.service import allowed_hosts
from mazecrypto.ui import theme
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.theme import P
from mazecrypto.ui.widgets import Page, TitledGlass, button, confirm, label, scrolled

LANG_NAMES = {"en": "English", "tr": "Türkçe"}


def exchange_list(selected: list[str]) -> QListWidget:
    """Checkable, drag-reorderable list of exchanges (selected ones first, in order)."""
    lw = QListWidget()
    lw.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    order = [e for e in selected if e in EXCHANGES] + [e for e in EXCHANGES if e not in selected]
    for eid in order:
        cls = EXCHANGES[eid]
        it = QListWidgetItem(f"{cls.name:<10} {cls.country}")
        it.setData(Qt.ItemDataRole.UserRole, eid)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
        it.setCheckState(Qt.CheckState.Checked if eid in selected else Qt.CheckState.Unchecked)
        lw.addItem(it)
    lw.setFixedHeight(34 * len(order) + 6)
    return lw


def checked_exchanges(lw: QListWidget) -> list[str]:
    return [lw.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lw.count())
            if lw.item(i).checkState() == Qt.CheckState.Checked]


async def probe_proxy(url: str) -> str:
    """Fetch one small public endpoint through ``url``; return a short report."""
    http = Http(allowed_hosts(), proxy=url, timeout=20, retries=0)
    try:
        await http.get_json("https://api.binance.com/api/v3/ping")
        return "ok"
    finally:
        await http.close()


async def tor_listening(host: str = "127.0.0.1", port: int = 9050) -> bool:
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), 1.5)
    except (OSError, TimeoutError):
        return False
    w.close()
    with contextlib.suppress(Exception):
        await w.wait_closed()
    return True


def _swatch(color: str) -> QIcon:
    """A small rounded colour sample for the accent picker."""
    pm = QPixmap(28, 28)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(QColor(255, 255, 255, 60))
    p.drawRoundedRect(3, 3, 22, 22, 7, 7)
    p.end()
    return QIcon(pm)


class SettingsPage(Page):
    def __init__(self, ctl: Controller):
        super().__init__(t("set.title"), t("set.sub"))
        self.ctl = ctl
        self.actions.addWidget(button(t("btn.save"), "Primary", self._save, icon_name="ph.floppy-disk"))
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(14)
        expert = ctl.expert

        def card(title: str, help_text: str = "") -> QFormLayout:
            g = TitledGlass(title)
            if help_text:
                g.lay.addWidget(label(help_text, "Explain", wrap=True))
            f = QFormLayout()
            f.setSpacing(12)
            f.setHorizontalSpacing(24)
            f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
            g.lay.addLayout(f)
            lay.addWidget(g)
            return f

        def key(text: str) -> QLabel:
            lb = label(text, "CardKey")
            lb.setMinimumWidth(200)
            return lb

        # ── experience ──
        f = card(t("set.mode"), t("set.mode_help"))
        self.expert = QCheckBox(t("set.expert"))
        f.addRow(self.expert)
        f.addRow(label(t("set.expert_unlocks"), "Small", wrap=True))
        self.accent = QComboBox()
        for name in theme.ACCENTS:
            self.accent.addItem(_swatch(theme.ACCENTS[name][0]), t(f"accent.{name}"), name)
        f.addRow(key(t("set.accent")), self.accent)

        f = card(t("set.general"))
        self.lang = QComboBox()
        for code, name in LANG_NAMES.items():
            self.lang.addItem(name, code)
        self.fiat = QComboBox()
        self.fiat.addItems(FIATS)
        self.fiat2 = QComboBox()
        self.fiat2.addItem(t("set.none"), "")
        for c in FIATS:
            self.fiat2.addItem(c, c)
        self.refresh = QSpinBox()
        self.refresh.setRange(15, 3600)
        self.refresh.setSuffix(" s")
        self.notify = QCheckBox(t("set.notify"))
        f.addRow(key(t("set.language")), self.lang)
        f.addRow(key(t("set.fiat")), self.fiat)
        f.addRow(key(t("set.fiat2")), self.fiat2)
        if expert:
            f.addRow(key(t("set.refresh")), self.refresh)
        f.addRow("", self.notify)

        # ── assistant ──
        f = card(t("set.ai"), t("set.ai_help"))
        self.ai_on = QCheckBox(t("set.ai_on"))
        self.ai_status = label("", "Small", wrap=True)
        self.ai_model = QComboBox()
        self.ai_model.setEditable(True)
        f.addRow(self.ai_on)
        if expert:
            f.addRow(key(t("set.ai_model")), self.ai_model)
        f.addRow(self.ai_status)

        # ── sources and network (expert) ──
        self.ex_holder = QVBoxLayout()
        self.proxy_on = QCheckBox(t("set.proxy_on"))
        self.proxy_url = QLineEdit()
        self.proxy_url.setPlaceholderText("socks5h://127.0.0.1:9050")
        self.proxy_status = label("", "Small", wrap=True)
        self.profile = QComboBox()
        self.profile.setEditable(True)
        self.profile.setToolTip(t("set.profile_tip"))
        if expert:
            f = card(t("set.exchanges"), t("set.exchanges_help"))
            f.addRow(self.ex_holder)
            f = card(t("set.network"), t("set.proxy_help"))
            row = QHBoxLayout()
            row.addWidget(self.proxy_url, 1)
            row.addWidget(button(t("set.test"), "", self._test_proxy))
            f.addRow(self.proxy_on)
            f.addRow(key(t("set.proxy_url")), row)
            f.addRow(self.proxy_status)

        f = card(t("set.data"))
        if expert:
            f.addRow(key(t("set.profile")), self.profile)
        btns = QHBoxLayout()
        if expert:
            btns.addWidget(button(t("set.open_folder"), "", lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(cfg.data_dir()))), icon_name="ph.folder-open"))
            btns.addWidget(button(t("set.clear_cache"), "", self._clear_cache))
        btns.addWidget(button(t("set.rerun_wizard"), "Link", self._rerun_wizard))
        btns.addStretch()
        f.addRow(btns)
        self.msg = label("", "Small")
        lay.addWidget(self.msg)
        lay.addStretch()
        self.outer.addWidget(scrolled(inner), 1)
        self.listen(ctl.aiChanged, lambda _ok: self._ai_status())
        self.load()

    def _ai_status(self) -> None:
        if not self.ctl.settings.ai_enabled:
            self.ai_status.setText(t("set.ai_off"))
            self.ai_status.setStyleSheet(f"color: {P['muted']};")
        elif self.ctl.ai_ready:
            self.ai_status.setText(t("set.ai_ready", model=self.ctl.ai_model))
            self.ai_status.setStyleSheet(f"color: {P['up']};")
        else:
            self.ai_status.setText(t("set.ai_missing"))
            self.ai_status.setStyleSheet(f"color: {P['muted']};")

        async def models() -> list[str]:
            return await self.ctl.ai.models() if self.ctl.ai else []

        def fill(names: list[str]) -> None:
            current = self.ctl.settings.ai_model
            self.ai_model.clear()
            self.ai_model.addItem(t("set.ai_auto"), "")
            for n in names:
                self.ai_model.addItem(n, n)
            i = self.ai_model.findData(current)
            self.ai_model.setCurrentIndex(i if i >= 0 else 0)

        self.ctl.run(models(), fill, lambda _e: fill([]))

    def load(self) -> None:
        s = self.ctl.settings
        self.lang.setCurrentIndex(max(0, self.lang.findData(s.language)))
        self.fiat.setCurrentText(s.fiat)
        self.fiat2.setCurrentIndex(max(0, self.fiat2.findData(s.secondary_fiat)))
        self.refresh.setValue(s.refresh_seconds)
        self.notify.setChecked(s.notifications)
        self.notify.setEnabled(self.ctl.notifier.available)
        self.expert.setChecked(s.expert)
        self.accent.setCurrentIndex(max(0, self.accent.findData(s.accent)))
        self.ai_on.setChecked(s.ai_enabled)
        self._ai_status()
        while self.ex_holder.count():
            w = self.ex_holder.takeAt(0).widget()
            if w:
                w.setParent(None)
        self.ex = exchange_list(s.exchanges)
        self.ex_holder.addWidget(self.ex)
        self.proxy_on.setChecked(s.proxy.enabled)
        self.proxy_url.setText(s.proxy.url)
        self.profile.clear()
        profiles = sorted({p.stem for p in (cfg.data_dir() / "profiles").glob("*.db")} | {s.profile})
        self.profile.addItems(profiles)
        self.profile.setCurrentText(s.profile)
        self.ctl.run(tor_listening(), lambda up: self.proxy_status.setText(t("set.tor_seen") if up else ""))

    def on_show(self) -> None:
        self.load()
        self.msg.setText("")

    def _collect(self) -> cfg.Settings | None:
        proxy = cfg.ProxySettings(self.proxy_on.isChecked(), self.proxy_url.text().strip())
        s = replace(self.ctl.settings, proxy=proxy, language=self.lang.currentData(), fiat=self.fiat.currentText(),
                    secondary_fiat=self.fiat2.currentData() or "", refresh_seconds=self.refresh.value(),
                    notifications=self.notify.isChecked(), exchanges=checked_exchanges(self.ex),
                    profile=self.profile.currentText().strip() or "default", expert=self.expert.isChecked(),
                    accent=self.accent.currentData() or theme.DEFAULT_ACCENT,
                    ai_enabled=self.ai_on.isChecked(),
                    ai_model=(self.ai_model.currentData() if self.ai_model.currentIndex() >= 0 and
                              self.ai_model.currentText() == self.ai_model.itemText(self.ai_model.currentIndex())
                              else self.ai_model.currentText()) or "",
                    watchlist=list(self.ctl.settings.watchlist))
        try:
            s.validate()
        except ValueError as e:
            self.msg.setText(t("set.invalid", err=e))
            self.msg.setStyleSheet(f"color: {P['down']};")
            return None
        return s

    def _save(self) -> None:
        s = self._collect()
        if s is None:
            return
        if not s.exchanges and not confirm(self, t("set.title"), t("set.no_exchange_q")):
            return
        self.ctl.apply_settings(s)
        self.msg.setText(t("set.saved"))
        self.msg.setStyleSheet(f"color: {P['up']};")

    def _test_proxy(self) -> None:
        url = self.proxy_url.text().strip()
        try:
            cfg.ProxySettings(True, url).validate()
        except ValueError as e:
            self.proxy_status.setText(str(e))
            return
        self.proxy_status.setText(t("set.testing"))
        self.proxy_status.setStyleSheet(f"color: {P['muted']};")

        def ok(_r) -> None:
            self.proxy_status.setText(t("set.proxy_ok"))
            self.proxy_status.setStyleSheet(f"color: {P['up']};")

        def fail(e: Exception) -> None:
            self.proxy_status.setText(t("set.proxy_fail", err=e))
            self.proxy_status.setStyleSheet(f"color: {P['down']};")

        self.ctl.run(probe_proxy(url), ok, fail)

    def _clear_cache(self) -> None:
        with self.ctl.cache.con:
            self.ctl.cache.con.execute("DELETE FROM cache")
        self.msg.setText(t("set.cache_cleared"))
        self.ctl.refresh()

    def _rerun_wizard(self) -> None:
        if confirm(self, t("set.rerun_wizard"), t("set.rerun_q")):
            self.ctl.settings.onboarded = False      # kept in memory too, so a later save does not undo it
            cfg.save(self.ctl.settings)
            self.msg.setText(t("set.rerun_done"))
