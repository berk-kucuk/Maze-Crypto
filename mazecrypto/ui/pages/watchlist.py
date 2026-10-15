"""Watchlist: prices, changes, ATH distance, BTC correlation, sparkline."""
from __future__ import annotations

import time
from dataclasses import replace
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QMenu, QTableWidget

from mazecrypto.core.models import CoinRow
from mazecrypto.i18n import t
from mazecrypto.providers.service import Snapshot
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.glass import Glass
from mazecrypto.ui.theme import P, change_color, tabular
from mazecrypto.ui.widgets import (
    KeyItem,
    NumItem,
    Page,
    Sparkline,
    button,
    label,
    text_item,
)

if TYPE_CHECKING:
    from mazecrypto.ui.window import MainWindow


class WatchlistPage(Page):
    def __init__(self, ctl: Controller, win: MainWindow):
        super().__init__(t("watch.title"))
        self.ctl, self.win = ctl, win
        self.actions.addWidget(button(t("watch.add"), "Primary", self.add_coin, "Ctrl+K", "ph.plus"))

        self.table = QTableWidget()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._open(r))
        self.table.setWordWrap(False)
        tabular(self.table)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        card = Glass(padding=8)
        card.lay.addWidget(self.table)
        self.empty = label(t("watch.empty"), "Placeholder")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card.lay.addWidget(self.empty, 1)
        self.outer.addWidget(card, 1)
        self.foot = label("", "Small")
        self.outer.addSpacing(8)
        self.outer.addWidget(self.foot)

        self.listen(ctl.snapshotReady, self._render)
        self.listen(ctl.enriched, self._render)
        self.listen(ctl.settingsChanged, self._setup_columns)
        self._setup_columns()

    # ── columns ──────────────────────────────────────────────────────────────
    def _columns(self) -> list[tuple[str, str]]:
        s = self.ctl.settings
        cols = [("coin", t("col.coin")), ("price", t("col.price", cur=s.fiat))]
        if not self.ctl.expert:
            # Basic: what moved and how it looks over a month.
            return cols + [("c24", t("col.24h")), ("c7", t("col.7d")), ("spark", t("col.spark")),
                           ("ath", t("col.ath"))]
        if s.secondary_fiat:
            cols.append(("price2", t("col.price", cur=s.secondary_fiat)))
        cols += [("c24", t("col.24h")), ("c7", t("col.7d")), ("c30", t("col.30d")), ("ath", t("col.ath")),
                 ("corr", t("col.corr")), ("spark", t("col.spark")), ("mcap", t("col.mcap")),
                 ("src", t("col.source"))]
        return cols

    def _setup_columns(self) -> None:
        self.cols = self._columns()
        self.table.setColumnCount(len(self.cols))
        self.table.setHorizontalHeaderLabels([c[1] for c in self.cols])
        tips = {"ath": t("col.ath_tip"), "corr": t("col.corr_tip"), "spark": t("col.spark_tip"),
                "mcap": t("col.mcap_tip"), "src": t("col.source_tip")}
        for i, (key, _name) in enumerate(self.cols):
            if key in tips:
                self.table.horizontalHeaderItem(i).setToolTip(tips[key])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # Basic mode has few columns: let the coin column take the slack. Expert
        # mode is wide already, so the source column at the end absorbs it.
        h.setStretchLastSection(self.ctl.expert)
        if self.ctl.expert:
            h.setSectionResizeMode(self._col("coin"), QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(self._col("coin"), 190)
        else:
            h.setSectionResizeMode(self._col("coin"), QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(self._col("spark"), QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(self._col("spark"), 120)
        h.setHighlightSections(False)
        n = len(self.ctl.settings.watchlist)
        self.set_header(t("watch.title"), t("watch.sub", n=n, fiat=self.ctl.settings.fiat))
        if self.ctl.snap:
            self._render(self.ctl.snap)
        else:
            self._toggle_empty()

    def _col(self, key: str) -> int:
        return next(i for i, c in enumerate(self.cols) if c[0] == key)

    def _toggle_empty(self) -> None:
        empty = not self.ctl.settings.watchlist
        self.table.setVisible(not empty)
        self.empty.setVisible(empty)

    # ── rendering ────────────────────────────────────────────────────────────
    def _render(self, snap: Snapshot) -> None:
        s = self.ctl.settings
        order = [c.id for c in s.watchlist]
        rows = {r.coin.id: r for r in snap.rows if r.coin.id in order}
        self._toggle_empty()
        sort_col = self.table.horizontalHeader().sortIndicatorSection()
        sort_ord = self.table.horizontalHeader().sortIndicatorOrder()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(order))
        for i, cid in enumerate(order):
            r = rows.get(cid) or CoinRow(next(c for c in s.watchlist if c.id == cid))
            self._fill(i, r, snap)
        self.table.setSortingEnabled(True)
        if 0 <= sort_col < self.table.columnCount() and self.table.horizontalHeader().isSortIndicatorShown():
            self.table.sortItems(sort_col, sort_ord)
        stamp = f"{fmt.date(snap.ts)} {time.strftime('%H:%M:%S', time.localtime(snap.ts))}"
        self.foot.setText(t("watch.foot", time=stamp))

    def _fill(self, i: int, r: CoinRow, snap: Snapshot) -> None:
        s = self.ctl.settings
        keys = {k for k, _ in self.cols}

        def put(key: str, item) -> None:
            if key in keys:
                self.table.setItem(i, self._col(key), item)

        dim = P["muted"] if r.stale else None
        put("coin", KeyItem(r.coin.symbol, r.coin.id))
        cell = QLabel(f'<span style="font-weight:600">{r.coin.symbol}</span>&nbsp;&nbsp;'
                      f'<span style="color:{P["muted"]}">{r.coin.name}</span>')
        cell.setContentsMargins(10, 0, 0, 0)
        cell.setToolTip(r.coin.id)
        cell.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.table.setCellWidget(i, self._col("coin"), cell)
        put("price", NumItem(fmt.money(r.price_fiat, s.fiat), r.price_fiat, dim))
        if "price2" in keys:
            rate = snap.fx.get(s.secondary_fiat)
            p2 = r.price_usd * rate if r.price_usd is not None and rate else None
            put("price2", NumItem(fmt.money(p2, s.secondary_fiat), p2, P["text_mid"]))
        for key, val in (("c24", r.change_24h), ("c7", r.change_7d), ("c30", r.change_30d)):
            put(key, NumItem(fmt.pct(val), val, change_color(val)))
        near_ath = r.ath_change_pct is not None and r.ath_change_pct > -5
        put("ath", NumItem(fmt.pct(r.ath_change_pct, 1), r.ath_change_pct,
                           P["accent"] if near_ath else P["text_mid"]))
        corr = r.corr_btc_30d
        put("corr", NumItem(fmt.num(corr, 2), corr, P["accent"] if corr is not None and corr >= 0.7 else P["text_mid"]))
        if "spark" in keys:
            spark = Sparkline(r.spark[-31:])
            spark.setToolTip(t("col.spark_tip"))
            self.table.setCellWidget(i, self._col("spark"), spark)
            trend = (r.spark[-1] / r.spark[0] - 1) if len(r.spark) > 1 else None
            put("spark", NumItem("", trend))
        put("mcap", NumItem(fmt.compact(r.market_cap), r.market_cap, P["text_mid"]))
        if "src" in keys:
            src = text_item(r.fiat_source or "—", P["muted"])
            src.setToolTip(f"USD: {r.usd_source or '—'}\n{s.fiat}: {r.fiat_source or '—'}")
            put("src", src)

    # ── actions ──────────────────────────────────────────────────────────────
    def _coin_at(self, row: int):
        it = self.table.item(row, self._col("coin"))
        cid = it.data(Qt.ItemDataRole.UserRole) if it else None
        return next((c for c in self.ctl.settings.watchlist if c.id == cid), None)

    def _open(self, row: int) -> None:
        coin = self._coin_at(row)
        if coin:
            self.win.open_chart(coin)

    def _menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        coin = self._coin_at(row)
        if not coin:
            return
        m = QMenu(self)
        for text, fn in ((t("watch.open_chart"), lambda: self.win.open_chart(coin)),
                         (t("watch.move_up"), lambda: self._move(coin, -1)),
                         (t("watch.move_down"), lambda: self._move(coin, 1)),
                         (None, None),
                         (t("watch.remove"), lambda: self._remove(coin))):
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, m)
            a.triggered.connect(fn)
            m.addAction(a)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _save_watchlist(self, wl) -> None:
        new = replace(self.ctl.settings, watchlist=wl, proxy=replace(self.ctl.settings.proxy))
        self.ctl.apply_settings(new)

    def _move(self, coin, step: int) -> None:
        wl = list(self.ctl.settings.watchlist)
        i = wl.index(coin)
        j = max(0, min(len(wl) - 1, i + step))
        wl[i], wl[j] = wl[j], wl[i]
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self._save_watchlist(wl)

    def _remove(self, coin) -> None:
        self._save_watchlist([c for c in self.ctl.settings.watchlist if c.id != coin.id])

    def open_detail(self, detail: str) -> None:
        if detail == "add":
            self.add_coin()

    def add_coin(self) -> None:
        from mazecrypto.ui.dialogs.coin_search import CoinSearchDialog

        d = CoinSearchDialog(self.ctl, self, {c.id for c in self.ctl.settings.watchlist})
        if d.exec() and d.coin:
            self._save_watchlist([*self.ctl.settings.watchlist, d.coin])
