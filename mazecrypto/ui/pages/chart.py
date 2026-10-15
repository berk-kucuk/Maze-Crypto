"""Candlestick chart with SMA/EMA overlays, volume, RSI, MACD and manual levels."""
from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QKeySequence, QPainter, QPicture, QShortcut
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from mazecrypto.core import indicators as ind
from mazecrypto.core.models import Candle, Coin, Interval
from mazecrypto.i18n import t
from mazecrypto.providers.service import CandleSet
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.glass import Glass, icon
from mazecrypto.ui.theme import P
from mazecrypto.ui.widgets import Page, Segmented, button, label, scrolled

INTERVALS = (("1h", "1H"), ("4h", "4H"), ("1d", "1D"), ("1w", "1W"))


def _pen(color: str, width: float = 1.0, style=Qt.PenStyle.SolidLine) -> pg.mkPen:
    return pg.mkPen(color=color, width=width, style=style)


class _NumFormat:
    """Stands in for the format string of a pyqtgraph line label."""

    def format(self, value: float) -> str:
        return fmt.num(value)


class NumAxis(pg.AxisItem):
    """Value axis with locale-aware numbers instead of 5e+06."""

    def __init__(self, compact: bool = False):
        super().__init__(orientation="right")
        self.compact = compact

    def tickStrings(self, values, scale, spacing):
        if self.compact:
            return [fmt.compact(v) for v in values]
        decimals = max(0, min(8, -int(np.floor(np.log10(spacing))) + 1)) if spacing > 0 else 2
        return [fmt.num(v, decimals if spacing < 1 else 0) for v in values]


class CandleItem(pg.GraphicsObject):
    def __init__(self) -> None:
        super().__init__()
        self._pic = QPicture()
        self._rect = QRectF()

    def set_data(self, candles: list[Candle], width: float) -> None:
        self.prepareGeometryChange()
        pic = QPicture()
        p = QPainter(pic)
        up, down = QColor(P["up"]), QColor(P["down"])
        half = width / 2
        for c in candles:
            col = up if c.close >= c.open else down
            p.setPen(pg.mkPen(col, width=1, cosmetic=True))
            p.drawLine(QPointF(c.ts, c.low), QPointF(c.ts, c.high))
            p.setBrush(pg.mkBrush(col))
            top, bottom = max(c.open, c.close), min(c.open, c.close)
            p.drawRect(QRectF(c.ts - half, bottom, width, max(top - bottom, 1e-12)))
        p.end()
        self._pic = pic
        if candles:
            self._rect = QRectF(candles[0].ts - half, min(c.low for c in candles), candles[-1].ts - candles[0].ts
                                + width, max(c.high for c in candles) - min(c.low for c in candles))
        self.update()

    def paint(self, p, *_args) -> None:
        p.drawPicture(0, 0, self._pic)

    def boundingRect(self) -> QRectF:
        return self._rect


class ChartPage(Page):
    def __init__(self, ctl: Controller):
        super().__init__(t("chart.title"))
        self.ctl = ctl
        self.coin: Coin | None = None
        self.data: CandleSet | None = None
        self.interval = Interval(ctl.settings.chart_interval)
        self.quote_mode = "fiat"
        self._seq = 0
        self.levels: list[pg.InfiniteLine] = []

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.coin_box = QComboBox()
        self.coin_box.setMinimumWidth(220)
        self.coin_box.activated.connect(self._coin_picked)
        bar.addWidget(self.coin_box)
        self.iv_seg = Segmented(INTERVALS, self.interval.value)
        self.iv_seg.changed.connect(self._set_interval)
        self.iv_seg.setToolTip(t("chart.iv_tip"))
        bar.addWidget(self.iv_seg)
        self.q_seg = Segmented((("fiat", ctl.settings.fiat), ("usd", "USD")), "fiat")
        self.q_seg.changed.connect(self._set_quote)
        bar.addWidget(self.q_seg)
        bar.addStretch()
        bar.addWidget(button(t("chart.add_level"), "", self._add_level_at_last, t("chart.level_tip"),
                             "ph.line-segment"))
        bar.addWidget(button(t("chart.clear_levels"), "Link", self._clear_levels))
        self.outer.addLayout(bar)
        self.outer.addSpacing(12)

        # Basic mode shows one average and volume; expert mode every indicator.
        options = [("sma50", t("chart.t.sma50"), True), ("ema21", t("chart.t.ema21"), False),
                   ("sma200", t("chart.t.sma200"), False), ("vol", t("chart.volume"), True)]
        if ctl.expert:
            options = [("sma50", "SMA 50", True), ("sma200", "SMA 200", True), ("ema21", "EMA 21", True),
                       ("vol", t("chart.volume"), True), ("rsi", "RSI 14", True), ("macd", "MACD", True)]
        self.toggles: dict[str, QCheckBox] = {}
        bar2 = QHBoxLayout()
        bar2.setSpacing(18)
        for key, text, on in options:
            cb = QCheckBox(text)
            cb.setChecked(on)
            cb.setToolTip(t(f"chart.help.{key}"))
            cb.toggled.connect(self._apply_visibility)
            self.toggles[key] = cb
            bar2.addWidget(cb)
        bar2.addStretch()

        body = QHBoxLayout()
        body.setSpacing(14)
        chart_card = Glass(padding=12)
        chart_card.lay.addLayout(bar2)
        pg.setConfigOptions(antialias=True, background=None, foreground=P["muted"])
        self.gw = pg.GraphicsLayoutWidget()
        self.gw.setBackground(None)
        self.gw.setStyleSheet("background: transparent;")
        self.gw.ci.setSpacing(0)
        self.gw.ci.setContentsMargins(0, 0, 0, 0)
        chart_card.lay.addWidget(self.gw, 1)
        self.readout = label("", "Small")
        self.readout.setTextFormat(Qt.TextFormat.RichText)
        chart_card.lay.addWidget(self.readout)
        body.addWidget(chart_card, 1)

        side = QWidget()
        self.explain = QVBoxLayout(side)
        self.explain.setContentsMargins(0, 0, 4, 0)
        self.explain.setSpacing(10)
        panel = scrolled(side)
        panel.setFixedWidth(330)
        body.addWidget(panel)
        self.outer.addLayout(body, 1)
        self._build_plots()

        for key, iv in (("H", "1h"), ("4", "4h"), ("D", "1d"), ("W", "1w")):
            sc = QShortcut(QKeySequence(key), self, lambda iv=iv: (self.iv_seg.set(iv), self._set_interval(iv)))
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

        self.listen(ctl.settingsChanged, self._settings_changed)
        self.listen(ctl.snapshotReady, lambda _s: self._fill_coins())
        self._fill_coins()

    # ── plots ────────────────────────────────────────────────────────────────
    def _axis(self) -> pg.DateAxisItem:
        ax = pg.DateAxisItem(orientation="bottom")
        ax.setPen(_pen(P["border2"]))
        ax.setTextPen(_pen(P["muted"]))
        return ax

    def _style(self, plot: pg.PlotItem, show_x: bool) -> None:
        plot.showGrid(x=True, y=True, alpha=0.08)
        plot.getAxis("left").hide()
        plot.showAxis("right")
        for name in ("right", "bottom"):
            ax = plot.getAxis(name)
            ax.setPen(_pen(P["border2"]))
            ax.setTextPen(_pen(P["muted"]))
        plot.getAxis("right").setWidth(78)
        plot.getAxis("bottom").setStyle(showValues=show_x)
        plot.getAxis("bottom").setHeight(22 if show_x else 0)
        plot.setMenuEnabled(False)
        plot.hideButtons()
        plot.vb.setMouseEnabled(x=True, y=False)

    def _build_plots(self) -> None:
        g = self.gw
        self.price = g.addPlot(row=0, col=0, axisItems={"bottom": self._axis(), "right": NumAxis()})
        self.volume = g.addPlot(row=1, col=0, axisItems={"bottom": self._axis(), "right": NumAxis(True)})
        self.rsi = g.addPlot(row=2, col=0, axisItems={"bottom": self._axis()})
        self.macd = g.addPlot(row=3, col=0, axisItems={"bottom": self._axis(), "right": NumAxis(True)})
        for i, stretch in enumerate((6, 1.2, 1.6, 1.6)):
            g.ci.layout.setRowStretchFactor(i, int(stretch * 10))
        for pl in (self.volume, self.rsi, self.macd):
            pl.setXLink(self.price)
        for pl in (self.price, self.volume, self.rsi, self.macd):
            self._style(pl, show_x=pl is self.macd)
        self.price.vb.setAutoVisible(y=True)
        self.volume.vb.setAutoVisible(y=True)
        self.macd.vb.setAutoVisible(y=True)

        self.candles = CandleItem()
        self.price.addItem(self.candles)
        self.sma50 = self.price.plot(pen=_pen(P["accent2"], 1.3))
        self.sma200 = self.price.plot(pen=_pen(P["violet"], 1.3))
        self.ema21 = self.price.plot(pen=_pen(P["accent"], 1.1))
        # The last price is in the readout below the chart; a label here would hide the newest candles.
        self.last_line = pg.InfiniteLine(angle=0, pen=_pen(P["dim"], 1, Qt.PenStyle.DotLine), movable=False)
        self.price.addItem(self.last_line)

        self.vol_bars = pg.BarGraphItem(x=[], height=[], width=1)
        self.volume.addItem(self.vol_bars)

        self.rsi_line = self.rsi.plot(pen=_pen(P["accent"], 1.2))
        for lvl in (30, 70):
            self.rsi.addItem(pg.InfiniteLine(lvl, angle=0, pen=_pen(P["border2"], 1, Qt.PenStyle.DashLine)))
        self.rsi.setYRange(0, 100, padding=0.05)
        self.rsi.getAxis("right").setTicks([[(30, "30"), (50, "50"), (70, "70")]])

        self.macd_hist = pg.BarGraphItem(x=[], height=[], width=1)
        self.macd.addItem(self.macd_hist)
        self.macd_line = self.macd.plot(pen=_pen(P["accent"], 1.2))
        self.macd_sig = self.macd.plot(pen=_pen(P["violet"], 1.2))

        self.cost_line = pg.InfiniteLine(angle=0, pen=_pen(P["accent"], 1.2, Qt.PenStyle.DashLine), movable=False,
                                         label=t("chart.your_avg"), labelOpts={"position": 0.5,
                                                                               "anchors": [(0.5, 1), (0.5, 1)],
                                                                               "color": P["accent"],
                                                                               "fill": P["base"]})
        self.cost_line.setToolTip(t("chart.your_avg_tip"))
        self.cost_line.hide()
        self.price.addItem(self.cost_line, ignoreBounds=True)
        self.vline = pg.InfiniteLine(angle=90, pen=_pen(P["border2"], 1), movable=False)
        self.price.addItem(self.vline, ignoreBounds=True)
        self._proxy = pg.SignalProxy(self.price.scene().sigMouseMoved, rateLimit=40, slot=self._mouse_moved)
        self.price.scene().sigMouseClicked.connect(self._mouse_clicked)

        self.legend = pg.TextItem(anchor=(0, 0))
        self.price.addItem(self.legend, ignoreBounds=True)
        self.price.sigRangeChanged.connect(self._place_legend)
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        tg = {k: False for k in ("sma50", "sma200", "ema21", "vol", "rsi", "macd")}
        tg |= {k: cb.isChecked() for k, cb in self.toggles.items()}
        self.sma50.setVisible(tg["sma50"])
        self.sma200.setVisible(tg["sma200"])
        self.ema21.setVisible(tg["ema21"])
        rows = [(self.volume, tg["vol"]), (self.rsi, tg["rsi"]), (self.macd, tg["macd"])]
        for plot, on in rows:
            plot.setVisible(on)
            plot.setMaximumHeight(16777215 if on else 0)
        # The lowest visible plot carries the date axis.
        visible = [self.price] + [p for p, on in rows if on]
        for pl in (self.price, self.volume, self.rsi, self.macd):
            last = pl is visible[-1]
            pl.getAxis("bottom").setStyle(showValues=last)
            pl.getAxis("bottom").setHeight(22 if last else 0)
        self._update_legend()

    # ── coin / interval selection ────────────────────────────────────────────
    def _fill_coins(self) -> None:
        current = self.coin.id if self.coin else None
        coins = self.ctl.tracked_coins()
        self.coin_box.blockSignals(True)
        self.coin_box.clear()
        for c in coins:
            self.coin_box.addItem(f"{c.symbol}  ·  {c.name}", c)
        self.coin_box.blockSignals(False)
        if current:
            idx = next((i for i, c in enumerate(coins) if c.id == current), -1)
            if idx >= 0:
                self.coin_box.setCurrentIndex(idx)
        elif coins and self.coin is None:
            self.coin = coins[0]
            self.coin_box.setCurrentIndex(0)

    def _coin_picked(self, idx: int) -> None:
        coin = self.coin_box.itemData(idx)
        if coin:
            self.show_coin(coin)

    def show_coin(self, coin: Coin) -> None:
        self.coin = coin
        self._fill_coins()
        self.load()

    def _set_interval(self, iv: str) -> None:
        self.interval = Interval(iv)
        self.load()

    def _set_quote(self, mode: str) -> None:
        self.quote_mode = mode
        self.load()

    def _settings_changed(self) -> None:
        fiat = self.ctl.settings.fiat
        self.q_seg.buttons["fiat"].setText(fiat)
        self.q_seg.buttons["fiat"].setVisible(fiat != "USD")
        if fiat == "USD":
            self.quote_mode = "usd"
            self.q_seg.set("usd")
        self._fill_coins()

    def on_show(self) -> None:
        if self.coin and (self.data is None or self._loaded_key != self._key()):
            self.load()

    # ── loading ──────────────────────────────────────────────────────────────
    _loaded_key: tuple = ()

    def _key(self) -> tuple:
        return (self.coin.id if self.coin else "", self.interval.value, self.quote_mode)

    def load(self) -> None:
        if not self.coin:
            return
        self._seq += 1
        seq, coin = self._seq, self.coin
        ref = None
        if self.ctl.snap and (row := self.ctl.snap.row(coin.id)):
            ref = row.price_usd
        self.set_header(f"{coin.symbol} · {coin.name}", t("chart.loading"))
        self.ctl.run(self.ctl.service.candles(coin, self.quote_mode, self.interval, 500, ref_usd=ref),
                     lambda cs: self._loaded(seq, cs), lambda e: self._failed(seq, e))

    def _failed(self, seq: int, e: Exception) -> None:
        if seq == self._seq:
            self.set_header(f"{self.coin.symbol} · {self.coin.name}" if self.coin else "", t("chart.failed", err=e))

    def _loaded(self, seq: int, cs: CandleSet) -> None:
        if seq != self._seq or not self.coin:
            return
        self.data = cs
        self._loaded_key = self._key()
        c = cs.candles
        ts = np.array([x.ts for x in c], dtype=float)
        close = np.array([x.close for x in c])
        w = self.interval.seconds * 0.7
        self.candles.set_data(c, w)

        def line(item, arr):
            ok = ~np.isnan(arr)
            item.setData(ts[ok], arr[ok])

        line(self.sma50, ind.sma(close, 50))
        line(self.sma200, ind.sma(close, 200))
        line(self.ema21, ind.ema(close, 21))
        self.last_line.setValue(close[-1])

        up = np.array([x.close >= x.open for x in c])
        brushes = [pg.mkBrush(QColor(P["up"] if u else P["down"]).darker(220)) for u in up]
        self.vol_bars.setOpts(x=ts, height=[x.volume for x in c], width=w, brushes=brushes, pen=None)
        line(self.rsi_line, ind.rsi(close))
        m = ind.macd(close)
        h = np.nan_to_num(m.hist)
        self.macd_hist.setOpts(x=ts, height=h, width=w, pen=None,
                               brushes=[pg.mkBrush(QColor(P["up"] if v >= 0 else P["down"]).darker(180)) for v in h])
        line(self.macd_line, m.macd)
        line(self.macd_sig, m.signal)

        show = min(len(c), 160)
        self.price.setXRange(ts[-show] - w, ts[-1] + w * 3, padding=0)
        self.price.setLimits(xMin=ts[0] - self.interval.seconds * 5, xMax=ts[-1] + self.interval.seconds * 40)
        self._load_levels()
        stale = t("chart.stale", age=fmt.age(cs.stale_age)) if cs.stale_age else ""
        conv = t("chart.converted") if cs.converted else ""
        sub = "  ·  ".join(x for x in (f"{cs.source} · {cs.quote}", self.interval.value.upper(),
                                       t("chart.candles", n=len(c)), conv, stale) if x)
        self.set_header(f"{self.coin.symbol} · {self.coin.name}", sub)
        self._update_legend()
        self._show_readout(len(c) - 1)
        self._render_explain(close)

    # ── plain-language explanation ───────────────────────────────────────────
    def _row(self, icon_name: str, color: str, title: str, text: str) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(icon(icon_name, color).pixmap(18, 18))
        ic.setFixedWidth(20)
        lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(title, "ExplainTitle", wrap=True))
        col.addWidget(label(text, "Explain", wrap=True))
        lay.addLayout(col, 1)
        return w

    def _clear_explain(self) -> None:
        while self.explain.count():
            it = self.explain.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _render_explain(self, close: np.ndarray) -> None:
        """What the chart shows, in sentences, plus how to read it."""
        self._clear_explain()
        coin = self.coin.symbol if self.coin else ""
        unit = t(f"chart.unit.{self.interval.value}")
        n = min(30, len(close) - 1)
        card = Glass(padding=16)
        card.lay.setSpacing(12)
        card.lay.addWidget(label(t("chart.x.title").upper(), "CardTitle"))

        if n > 0:
            ch = (close[-1] / close[-1 - n] - 1) * 100
            card.lay.addWidget(self._row("ph.trend-up" if ch >= 0 else "ph.trend-down",
                                         P["up"] if ch >= 0 else P["down"],
                                         t("chart.x.change_t", pct=fmt.pct(ch, 1)),
                                         t("chart.x.change_b", coin=coin, pct=fmt.pct(ch, 1), n=n, unit=unit)))
        s50 = ind.last(ind.sma(close, 50))
        s200 = ind.last(ind.sma(close, 200))
        if s50 is not None:
            above = close[-1] > s50
            text = t("chart.x.trend_up_b" if above else "chart.x.trend_down_b", coin=coin, unit=unit)
            if s200 is not None:
                text += " " + t("chart.x.long_up" if s50 > s200 else "chart.x.long_down")
            card.lay.addWidget(self._row("ph.path", P["accent2"],
                                         t("chart.x.trend_up_t" if above else "chart.x.trend_down_t"), text))
        r = ind.last(ind.rsi(close))
        if r is not None:
            state = "hot" if r >= 70 else "cold" if r <= 30 else "calm"
            color = {"hot": P["warn"], "cold": P["info"], "calm": P["text_mid"]}[state]
            card.lay.addWidget(self._row("ph.gauge", color, t(f"chart.x.rsi_{state}_t", rsi=fmt.num(r, 0)),
                                         t(f"chart.x.rsi_{state}_b", coin=coin)))
        if self.ctl.expert:
            m = ind.macd(close)
            h = ind.last(m.hist)
            if h is not None:
                card.lay.addWidget(self._row("ph.activity", P["violet"],
                                             t("chart.x.macd_up_t" if h > 0 else "chart.x.macd_down_t"),
                                             t("chart.x.macd_b")))
        rets = ind.returns(close[-61:])
        if len(rets) > 5:
            typical = float(np.std(rets, ddof=1) * 100)
            card.lay.addWidget(self._row("ph.arrows-down-up", P["text_mid"],
                                         t("chart.x.vol_t", pct=fmt.num(typical, 1)),
                                         t("chart.x.vol_b", coin=coin, unit=t(f"chart.unit1.{self.interval.value}"),
                                           pct=fmt.num(typical, 1))))
        cost = self._avg_cost()
        if cost:
            diff = (close[-1] / cost - 1) * 100
            card.lay.addWidget(self._row("ph.wallet", P["accent"], t("chart.x.cost_t"),
                                         t("chart.x.cost_above" if diff >= 0 else "chart.x.cost_below",
                                           avg=fmt.money(cost, self.data.quote if self.data else ""),
                                           pct=fmt.num(abs(diff), 1))))
        card.lay.addWidget(label(t("chart.x.note"), "Small", wrap=True))
        self.explain.addWidget(card)

        guide = Glass(padding=16)
        guide.lay.setSpacing(10)
        guide.lay.addWidget(label(t("chart.g.title").upper(), "CardTitle"))
        topics = ["candles", "average", "volume", "levels"] + (["rsi", "macd"] if self.ctl.expert else ["rsi"])
        for key in topics:
            guide.lay.addWidget(label(t(f"chart.g.{key}_t"), "ExplainTitle"))
            guide.lay.addWidget(label(t(f"chart.g.{key}_b"), "Explain", wrap=True))
        self.explain.addWidget(guide)
        self.explain.addStretch()

    def _avg_cost(self) -> float | None:
        """The user's average buy price in the chart's currency, if they hold the coin."""
        if not self.coin or not self.data:
            return None
        pos = self.ctl.positions().get(self.coin.id)
        if not pos or pos.amount <= 0:
            self.cost_line.hide()
            return None
        if self.data.quote == self.ctl.settings.fiat:
            avg = pos.avg_cost_fiat
        elif self.data.quote == "USD":
            avg = pos.avg_cost_usd
        else:
            avg = None
        if avg:
            self.cost_line.setValue(avg)
            self.cost_line.show()
        else:
            self.cost_line.hide()
        return avg

    # ── legend / readout ─────────────────────────────────────────────────────
    def _update_legend(self) -> None:
        parts = []
        for key, name, color in (("ema21", "EMA 21", P["accent"]), ("sma50", "SMA 50", P["accent2"]),
                                 ("sma200", "SMA 200", P["violet"])):
            if self.toggles[key].isChecked():
                parts.append(f'<span style="color:{color}">━ {name}</span>')
        body = "&nbsp;&nbsp;".join(parts)
        self.legend.setHtml(f'<div style="font-family: Inter, sans-serif; font-size: 12px">{body}</div>')
        self._place_legend()

    def _place_legend(self, *_a) -> None:
        (x0, _x1), (_y0, y1) = self.price.vb.viewRange()
        self.legend.setPos(x0, y1)

    def _mouse_moved(self, evt) -> None:
        if not self.data or not self.data.candles:
            return
        pos = evt[0]
        if not self.price.sceneBoundingRect().contains(pos):
            return
        x = self.price.vb.mapSceneToView(pos).x()
        ts = [c.ts for c in self.data.candles]
        i = int(np.clip(np.searchsorted(ts, x), 0, len(ts) - 1))
        if i > 0 and abs(ts[i - 1] - x) < abs(ts[i] - x):
            i -= 1
        self.vline.setValue(ts[i])
        self._show_readout(i)

    def _show_readout(self, i: int) -> None:
        if not self.data:
            return
        c = self.data.candles[i]
        q = self.data.quote
        chg = (c.close / c.open - 1) * 100 if c.open else None
        color = P["up"] if c.close >= c.open else P["down"]
        when = time.strftime("%Y-%m-%d %H:%M" if self.interval.seconds < 86400 else "%Y-%m-%d", time.localtime(c.ts))
        vals = "&nbsp;&nbsp;".join(f'{k} <span style="color:{P["text"]}">{fmt.num(v)}</span>'
                                   for k, v in (("O", c.open), ("H", c.high), ("L", c.low), ("C", c.close)))
        self.readout.setText(f'{when}&nbsp;&nbsp;{vals}&nbsp;&nbsp;<span style="color:{color}">{fmt.pct(chg)}</span>'
                             f'&nbsp;&nbsp;VOL {fmt.compact(c.volume)}&nbsp;&nbsp;·&nbsp;&nbsp;{q}')

    # ── support / resistance levels ──────────────────────────────────────────
    def _level_key(self) -> str:
        return f"{self.coin.id}:{self.data.quote}" if self.coin and self.data else ""

    def _load_levels(self) -> None:
        for ln in self.levels:
            self.price.removeItem(ln)
        self.levels = []
        for y in self.ctl.store.levels(self._level_key()):
            self._make_level(y)

    def _make_level(self, y: float) -> None:
        ln = pg.InfiniteLine(y, angle=0, movable=True, pen=_pen(P["warn"], 1, Qt.PenStyle.DashLine),
                             hoverPen=_pen(P["warn"], 2), label=_NumFormat(),
                             labelOpts={"position": 0.005, "anchors": [(0, 1), (0, 1)], "color": P["warn"],
                                        "fill": P["bg"], "movable": True})
        ln.setToolTip(t("chart.level_line_tip"))
        ln.sigPositionChangeFinished.connect(lambda _l: self._save_levels())
        ln.sigClicked.connect(self._level_clicked)
        self.price.addItem(ln)
        self.levels.append(ln)

    def _level_clicked(self, ln, ev) -> None:
        if ev.double():
            self.price.removeItem(ln)
            self.levels.remove(ln)
            self._save_levels()

    def _save_levels(self) -> None:
        if self._level_key():
            self.ctl.store.set_levels(self._level_key(), [ln.value() for ln in self.levels])

    def _add_level_at_last(self) -> None:
        if self.data and self.data.candles:
            self._make_level(self.data.candles[-1].close)
            self._save_levels()

    def _clear_levels(self) -> None:
        for ln in self.levels:
            self.price.removeItem(ln)
        self.levels = []
        self._save_levels()

    def _mouse_clicked(self, ev) -> None:
        if not self.data or not (ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
            return
        if self.price.sceneBoundingRect().contains(ev.scenePos()):
            self._make_level(self.price.vb.mapSceneToView(ev.scenePos()).y())
            self._save_levels()
            ev.accept()
