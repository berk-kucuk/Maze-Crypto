"""Insights: correlation matrix, technical state per coin, portfolio concentration.

Everything here describes the current state; none of it is a recommendation.
"""
from __future__ import annotations

import asyncio
import math

import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QVBoxLayout, QWidget

from mazecrypto.core import insights as ins
from mazecrypto.core.candles import closes
from mazecrypto.core.models import Coin, Interval
from mazecrypto.core.portfolio import OTHER, drift
from mazecrypto.i18n import t, upper_first
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.theme import P, change_color, tabular
from mazecrypto.ui.widgets import NumItem, Page, StatCard, TitledGlass, button, card_grid, label, scrolled, text_item


def _mix(a: QColor, b: QColor, k: float) -> QColor:
    k = max(0.0, min(1.0, k))
    return QColor(int(a.red() + (b.red() - a.red()) * k), int(a.green() + (b.green() - a.green()) * k),
                  int(a.blue() + (b.blue() - a.blue()) * k))


class Heatmap(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.names: list[str] = []
        self.m = np.zeros((0, 0))
        self.setMinimumHeight(120)

    def set_data(self, names: list[str], m: np.ndarray) -> None:
        self.names, self.m = names, m
        n = len(names)
        self.setMinimumHeight(28 + n * 30)
        self.update()

    def paintEvent(self, _e) -> None:
        n = len(self.names)
        if not n:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(self.font())
        f.setPointSizeF(8.5)
        p.setFont(f)
        left, top = 60, 24
        cell = min(54.0, (self.width() - left) / n, 30.0 * 1.8)
        h = 30.0
        bg, pos, neg = QColor(P["raised"]), QColor(P["accent"]), QColor(P["down"])
        for j, name in enumerate(self.names):
            p.setPen(QColor(P["muted"]))
            p.drawText(QRectF(left + j * cell, 0, cell, top - 4), Qt.AlignmentFlag.AlignCenter, name)
        for i, name in enumerate(self.names):
            p.setPen(QColor(P["muted"]))
            p.drawText(QRectF(0, top + i * h, left - 8, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       name)
            for j in range(n):
                v = self.m[i, j]
                r = QRectF(left + j * cell + 1, top + i * h + 1, cell - 2, h - 2)
                path = QPainterPath()
                path.addRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
                if math.isnan(v):
                    p.fillPath(path, QColor(255, 255, 255, 8))
                    continue
                p.fillPath(path, _mix(bg, pos if v >= 0 else neg, abs(v) * 0.85))
                p.setPen(QColor(P["on_accent"] if abs(v) > 0.6 else P["text_mid"]))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, fmt.num(v, 2))


def summary_text(st: ins.TechState) -> str:
    """A few plain words for one coin, e.g. "Uptrend, momentum slowing"."""
    parts = []
    if st.trend == "above_both":
        parts.append(t("ins.sum.up"))
    elif st.trend == "below_both":
        parts.append(t("ins.sum.down"))
    elif st.trend == "mixed":
        parts.append(t("ins.sum.mixed"))
    if st.rsi_state == "overbought":
        parts.append(t("ins.sum.hot"))
    elif st.rsi_state == "oversold":
        parts.append(t("ins.sum.cold"))
    elif st.macd_state:
        parts.append(t("ins.sum.mom_up" if st.macd_state == "bullish" else "ins.sum.mom_down"))
    text = ", ".join(parts)
    return upper_first(text) if text else t("ins.short")


class InsightsPage(Page):
    def __init__(self, ctl: Controller):
        super().__init__(t("ins.title"), t("ins.sub"))
        self.ctl = ctl
        self._loaded_for: tuple = ()
        self.actions.addWidget(button(t("ins.recompute"), "", self.compute, icon_name="ph.arrows-clockwise"))
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(0)

        self.c_conc = StatCard(t("ins.top"), "ph.chart-pie-slice")
        self.c_stable = StatCard(t("ins.stable"), "ph.shield-check")
        self.c_drift = StatCard(t("ins.max_drift"), "ph.scales")
        self.c_corr = StatCard(t("ins.avg_corr"), "ph.link")
        lay.addWidget(card_grid([self.c_conc, self.c_stable, self.c_drift, self.c_corr]))
        lay.addSpacing(16)

        tech = TitledGlass(t("ins.tech"))
        tech.lay.addWidget(label(t("ins.tech_help"), "Explain", wrap=True))
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([h for h in (
            t("col.coin"), t("ins.in_short"), "RSI 14", t("ins.trend"), "MACD", t("ins.vol"), t("ins.dd"),
            t("ins.90d"))])
        for i, key in enumerate(("", "", "rsi", "trend", "macd", "vol", "dd", "")):
            if key:
                self.table.horizontalHeaderItem(i).setToolTip(t(f"ins.g.{key}_b"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setShowGrid(False)
        tabular(self.table)
        self.table.setSortingEnabled(True)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)     # keep watchlist order until a header is clicked
        tech.lay.addWidget(self.table)
        lay.addWidget(tech)
        lay.addSpacing(16)

        row = QHBoxLayout()
        row.setSpacing(16)
        corr = TitledGlass(t("ins.corr"))
        self.heat = Heatmap()
        corr.lay.addWidget(self.heat)
        corr.lay.addStretch()
        row.addWidget(corr, 3)
        guide = TitledGlass(t("ins.g.title"))
        for key in ("corr", "rsi", "trend", "macd", "vol", "dd"):
            guide.lay.addWidget(label(t(f"ins.g.{key}_t"), "ExplainTitle"))
            guide.lay.addWidget(label(t(f"ins.g.{key}_b"), "Explain", wrap=True))
        guide.lay.addStretch()
        row.addWidget(guide, 2)
        lay.addLayout(row)
        lay.addSpacing(14)
        lay.addWidget(label(t("ins.disclaimer"), "Small", wrap=True))
        lay.addStretch()
        self.status = label("", "Small")
        self.outer.addWidget(scrolled(inner), 1)
        self.outer.addWidget(self.status)
        self.listen(ctl.portfolioChanged, self._portfolio)
        self.listen(ctl.snapshotReady, lambda _s: self._portfolio())

    def on_show(self) -> None:
        self._portfolio()
        key = tuple(c.id for c in self.ctl.settings.watchlist)
        if key != self._loaded_for:
            self.compute()

    def _portfolio(self) -> None:
        s = self.ctl.summary()
        if not s.holdings:
            for c in (self.c_conc, self.c_stable, self.c_drift):
                c.set("—", t("watch.no_trades"))
            return
        top = max(s.holdings, key=lambda h: h.value_fiat or 0)
        self.c_conc.set(fmt.pct(s.weights.get(top.position.coin_id), 1, False),
                        t("ins.top_sub", coin=top.position.symbol))
        stable = sum(w for cid, w in s.weights.items()
                     if any(h.position.coin_id == cid and Coin(cid, h.position.symbol, "").is_stable
                            for h in s.holdings))
        self.c_stable.set(fmt.pct(stable, 1, False), t("ins.stable_sub"))
        targets = self.ctl.store.targets()
        rows = drift(s.weights, targets, s.value_fiat)
        if rows:
            d = rows[0]
            names = self.ctl.store.target_symbols() | {OTHER: t("targets.other")}
            sign = "+" if d.diff_pct > 0 else ""
            self.c_drift.set(f"{sign}{fmt.num(d.diff_pct, 1)} pp",
                             t("ins.drift_sub", coin=names.get(d.bucket, d.bucket)),
                             P["warn"] if abs(d.diff_pct) > 5 else None)
        else:
            self.c_drift.set("—", t("hold.no_targets"))

    def compute(self) -> None:
        coins = [c for c in self.ctl.settings.watchlist]
        self._loaded_for = tuple(c.id for c in coins)
        self.status.setText(t("ins.loading", n=len(coins)))
        self.ctl.run(self._fetch(coins), self._show, lambda e: self.status.setText(t("chart.failed", err=e)))

    async def _fetch(self, coins: list[Coin]) -> list[tuple[Coin, list[float]]]:
        ref = {r.coin.id: r.price_usd for r in self.ctl.snap.rows} if self.ctl.snap else {}
        sem = asyncio.Semaphore(3)

        async def one(c: Coin) -> tuple[Coin, list[float]]:
            async with sem:
                try:
                    cs = await self.ctl.service.candles(c, "usd", Interval.D1, 300, ref_usd=ref.get(c.id))
                    return c, closes(cs.candles)
                except Exception:  # noqa: BLE001 - one coin without data must not hide the rest
                    return c, []

        return list(await asyncio.gather(*(one(c) for c in coins)))

    def _show(self, data: list[tuple[Coin, list[float]]]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(data))
        for i, (coin, cl) in enumerate(data):
            st = ins.tech_state(cl) if len(cl) > 2 else None
            self.table.setItem(i, 0, text_item(coin.symbol))
            if coin.is_stable:
                # Pegged to the dollar: trend and momentum readings would only describe noise.
                self.table.setItem(i, 1, text_item(t("ins.sum.stable"), P["text_mid"]))
                for c in range(2, 8):
                    self.table.setItem(i, c, NumItem("—", None, P["muted"]))
                continue
            if not st:
                for c in range(1, 8):
                    self.table.setItem(i, c, NumItem("—", None, P["muted"]))
                continue
            self.table.setItem(i, 1, text_item(summary_text(st), P["text"]))
            rsi_color = {"overbought": P["warn"], "oversold": P["info"]}.get(st.rsi_state, P["text_mid"])
            rsi_txt = f"{fmt.num(st.rsi, 0)}  {t('ins.rsi.' + st.rsi_state) if st.rsi_state else ''}".strip()
            self.table.setItem(i, 2, NumItem(rsi_txt, st.rsi, rsi_color))
            trend_color = {"above_both": P["up"], "below_both": P["down"]}.get(st.trend, P["text_mid"])
            self.table.setItem(i, 3, text_item(t("ins.trend." + st.trend) if st.trend else t("ins.short"),
                                               trend_color))
            self.table.setItem(i, 4, text_item(t("ins.macd." + st.macd_state) if st.macd_state else "—",
                                               P["up"] if st.macd_state == "bullish" else P["down"]))
            self.table.setItem(i, 5, NumItem(fmt.pct(st.volatility, 0, False), st.volatility, P["text_mid"]))
            dd = ins.max_drawdown(cl[-90:])
            self.table.setItem(i, 6, NumItem(fmt.pct(dd, 1), dd, P["text_mid"]))
            ch = (cl[-1] / cl[-91] - 1) * 100 if len(cl) > 91 else None
            self.table.setItem(i, 7, NumItem(fmt.pct(ch, 1), ch, change_color(None if ch is None else round(ch, 1))))
        self.table.setSortingEnabled(True)
        self.table.setFixedHeight(46 + 40 * len(data))

        series = {c.symbol: cl[-31:] for c, cl in data if len(cl) >= 31 and not c.is_stable}
        names, m = ins.correlation_matrix(series)
        self.heat.set_data(names, m)
        off = m[~np.eye(len(names), dtype=bool)] if len(names) > 1 else np.array([])
        off = off[~np.isnan(off)]
        self.c_corr.set(fmt.num(float(off.mean()), 2) if off.size else "—", t("ins.avg_corr_sub"))
        self.status.setText("")
