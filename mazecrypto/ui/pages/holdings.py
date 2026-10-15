"""Holdings: positions with P/L, target vs actual allocation, trade log, import/export."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMenu,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mazecrypto.core import export
from mazecrypto.core.portfolio import OTHER, Drift, drift, split_contribution
from mazecrypto.i18n import t
from mazecrypto.ui import fmt
from mazecrypto.ui.controller import Controller
from mazecrypto.ui.glass import Glass
from mazecrypto.ui.theme import P, change_color, tabular
from mazecrypto.ui.widgets import (
    NumItem,
    Page,
    StatCard,
    button,
    card_grid,
    confirm,
    label,
    scrolled,
    text_item,
)


def _table(headers: list[str]) -> QTableWidget:
    tb = QTableWidget(0, len(headers))
    tb.setHorizontalHeaderLabels([h for h in headers])
    tb.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tb.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    tb.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tb.verticalHeader().hide()
    tb.verticalHeader().setDefaultSectionSize(34)
    tb.setShowGrid(False)
    tb.setSortingEnabled(True)
    tabular(tb)
    h = tb.horizontalHeader()
    h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    h.setStretchLastSection(True)
    h.setHighlightSections(False)
    return tb


class AllocationBars(QWidget):
    """Actual weight as a bar, target as a tick, deviation and rebalance amount as text."""

    ROW = 38

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, Drift]] = []
        self.fiat = "USD"
        self.limit = 5.0

    def set_rows(self, rows: list[tuple[str, Drift]], fiat: str, limit: float) -> None:
        self.rows, self.fiat, self.limit = rows, fiat, limit
        self.setMinimumHeight(max(1, len(rows)) * self.ROW + 8)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(self.font())
        f.setPointSizeF(9)
        p.setFont(f)
        w = self.width()
        name_w, text_w = 90, 300
        track = QRectF(name_w, 0, max(60, w - name_w - text_w - 20), 6)
        scale = max([100.0] + [max(d.actual_pct, d.target_pct) for _n, d in self.rows])
        for i, (name, d) in enumerate(self.rows):
            y = i * self.ROW + 14
            p.setPen(QColor(P["text"]))
            p.drawText(QRectF(0, y - 8, name_w, 18), Qt.AlignmentFlag.AlignVCenter, name)
            tr = QRectF(track.x(), y - 2, track.width(), 6)
            p.fillRect(tr, QColor(P["raised"]))
            off = abs(d.diff_pct) > self.limit
            fill = QColor(P["warn"] if off else P["accent"])
            p.fillRect(QRectF(tr.x(), tr.y(), tr.width() * d.actual_pct / scale, tr.height()), fill)
            tx = tr.x() + tr.width() * d.target_pct / scale
            p.fillRect(QRectF(tx - 1, tr.y() - 5, 2, tr.height() + 10), QColor(P["text"]))
            p.setPen(QColor(P["warn"] if off else P["text_mid"]))
            reb = fmt.signed_money(d.rebalance_fiat, self.fiat) if d.target_pct else "—"
            txt = (f"{fmt.pct(d.actual_pct, 1, False)} / {fmt.pct(d.target_pct, 1, False)}   "
                   f"{'+' if d.diff_pct > 0 else ''}{fmt.num(d.diff_pct, 1)} pp   {reb}")
            p.drawText(QRectF(tr.right() + 20, y - 8, text_w, 18), Qt.AlignmentFlag.AlignVCenter, txt)


class HoldingsPage(Page):
    def __init__(self, ctl: Controller):
        super().__init__(t("hold.title"), t("hold.sub"))
        self.ctl = ctl
        self.actions.addWidget(button(t("hold.targets"), "", self._edit_targets, icon_name="ph.target"))
        io_btn = button(t("hold.io"), "", icon_name="ph.export")
        menu = QMenu(io_btn)
        menu.addAction(t("hold.export_csv"), lambda: self._export("csv"))
        menu.addAction(t("hold.export_json"), lambda: self._export("json"))
        menu.addAction(t("hold.export_tax"), self._export_tax)
        menu.addSeparator()
        menu.addAction(t("hold.import"), self._import)
        io_btn.setMenu(menu)
        io_btn.setVisible(ctl.expert)
        self.actions.addWidget(io_btn)
        self.actions.addWidget(button(t("hold.add"), "Primary", self.add_trade, "Ctrl+N", "ph.plus"))

        self._first_sort = True
        self.c_value, self.c_cost = StatCard(t("hold.value"), "ph.wallet"), StatCard(t("hold.cost"), "ph.receipt")
        self.c_pnl = StatCard(t("hold.pnl"), "ph.trend-up")
        self.c_usd = StatCard(t("hold.pnl_usd"), "ph.currency-dollar")
        self.c_real = StatCard(t("hold.realized"), "ph.check-circle")
        cards = [self.c_value, self.c_cost, self.c_pnl] + ([self.c_usd, self.c_real] if ctl.expert else [])
        self.outer.addWidget(card_grid(cards))
        self.outer.addSpacing(16)

        self.tabs = QTabWidget()
        tab_card = Glass(padding=12)
        tab_card.lay.addWidget(self.tabs)
        self.outer.addWidget(tab_card, 1)

        self.pos = _table([t("col.coin"), t("hold.amount"), t("hold.avg"), t("hold.price"), t("hold.value"),
                           t("hold.pnl"), "%", t("hold.pnl_usd"), t("hold.weight"), t("hold.target")])
        self.pos.cellDoubleClicked.connect(self._pos_double)
        self.pos.setColumnHidden(7, not ctl.expert)
        self.tabs.addTab(self.pos, t("hold.tab_positions"))

        alloc = QWidget()
        al = QVBoxLayout(alloc)
        al.setContentsMargins(0, 16, 0, 0)
        al.setSpacing(10)
        self.alloc_hint = label("", "Small", wrap=True)
        al.addWidget(self.alloc_hint)
        self.bars = AllocationBars()
        al.addWidget(self.bars)
        al.addWidget(label(t("hold.split_title").upper(), "SectionTitle"))
        row = QHBoxLayout()
        self.split_in = QLineEdit()
        self.split_in.setPlaceholderText(t("hold.split_ph", fiat=ctl.settings.fiat))
        self.split_in.setFixedWidth(220)
        self.split_in.textChanged.connect(self._split)
        row.addWidget(self.split_in)
        self.split_out = label("", "Mid", wrap=True)
        row.addWidget(self.split_out, 1)
        al.addLayout(row)
        al.addWidget(label(t("hold.split_note"), "Small", wrap=True))
        al.addStretch()
        self.tabs.addTab(scrolled(alloc), t("hold.tab_alloc"))

        self.trades = _table([t("trade.date"), t("trade.side"), t("col.coin"), t("hold.amount"), t("trade.cost"),
                              t("trade.fee"), t("hold.unit"), t("trade.platform"), t("trade.note")])
        self.trades.cellDoubleClicked.connect(lambda r, _c: self._edit_trade(r))
        self.trades.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.trades.customContextMenuRequested.connect(self._trade_menu)
        self.tabs.addTab(self.trades, t("hold.tab_trades"))

        self.listen(ctl.snapshotReady, lambda _s: self.render())
        self.listen(ctl.portfolioChanged, self.render)
        self.listen(ctl.settingsChanged, self.render)
        self.render()

    # ── rendering ────────────────────────────────────────────────────────────
    def render(self) -> None:
        fiat = self.ctl.settings.fiat
        s = self.ctl.summary()
        self.c_value.set(fmt.money(s.value_fiat, fiat, 2) if s.holdings else "—",
                         fmt.money(s.value_usd, "USD", 2) if s.value_usd is not None and fiat != "USD" else "")
        self.c_cost.set(fmt.money(s.cost_fiat, fiat, 2) if s.holdings else "—",
                        t("hold.n_trades", n=len(self.ctl.store.trades())))
        self.c_pnl.set(fmt.signed_money(s.pnl_fiat, fiat) if s.holdings else "—", fmt.pct(s.pnl_pct),
                       change_color(s.pnl_fiat), change_color(s.pnl_fiat))
        self.c_usd.set(fmt.signed_money(s.pnl_usd, "USD"), t("hold.usd_basis"), change_color(s.pnl_usd))
        self.c_real.set(fmt.signed_money(s.realized_fiat, fiat) if s.realized_fiat else "—", "",
                        change_color(s.realized_fiat))

        targets = self.ctl.store.targets()
        self.pos.setSortingEnabled(False)
        self.pos.setRowCount(len(s.holdings))
        for i, h in enumerate(s.holdings):
            p = h.position
            cid = p.coin_id
            tgt = targets.get(cid)
            cells = [
                text_item(p.symbol, data=cid),
                NumItem(fmt.amount(p.amount), p.amount),
                NumItem(fmt.money(p.avg_cost_fiat, fiat), p.avg_cost_fiat, P["text_mid"]),
                NumItem(fmt.money(h.price_fiat, fiat), h.price_fiat),
                NumItem(fmt.money(h.value_fiat, fiat, 2), h.value_fiat),
                NumItem(fmt.signed_money(h.pnl_fiat, fiat), h.pnl_fiat, change_color(h.pnl_fiat)),
                NumItem(fmt.pct(h.pnl_pct), h.pnl_pct, change_color(h.pnl_pct)),
                NumItem(fmt.signed_money(h.pnl_usd, "USD"), h.pnl_usd, change_color(h.pnl_usd)),
                NumItem(fmt.pct(s.weights.get(cid), 1, False), s.weights.get(cid)),
                NumItem(fmt.pct(tgt, 1, False) if tgt else "—", tgt, P["muted"]),
            ]
            for c, it in enumerate(cells):
                self.pos.setItem(i, c, it)
        self.pos.setSortingEnabled(True)
        if not self.pos.horizontalHeader().isSortIndicatorShown() or self._first_sort:
            self._first_sort = False
            self.pos.sortItems(4, Qt.SortOrder.DescendingOrder)

        names = {cid: sym for cid, sym in self.ctl.store.target_symbols().items()}
        names |= {h.position.coin_id: h.position.symbol for h in s.holdings}
        names[OTHER] = t("targets.other")
        rows = [(names.get(d.bucket, d.bucket), d) for d in drift(s.weights, targets, s.value_fiat)]
        rows.sort(key=lambda r: -r[1].target_pct)
        limit = 5.0
        self.bars.set_rows(rows, fiat, limit)
        self.alloc_hint.setText(t("hold.alloc_hint") if targets else t("hold.no_targets"))
        self._split()

        trades = sorted(self.ctl.store.trades(), key=lambda x: -x.ts)
        self.trades.setSortingEnabled(False)
        self.trades.setRowCount(len(trades))
        for i, tr in enumerate(trades):
            side_color = P["up"] if tr.side == "buy" else P["down"]
            cells = [
                NumItem(fmt.date(tr.ts), tr.ts, None, Qt.AlignmentFlag.AlignLeft),
                text_item(t(f"trade.{tr.side}").upper(), side_color),
                text_item(tr.symbol),
                NumItem(fmt.amount(tr.amount), tr.amount),
                NumItem(fmt.money(tr.cost, tr.fiat, 2), tr.cost),
                NumItem(fmt.money(tr.fee, tr.fiat, 2) if tr.fee else "—", tr.fee, P["muted"]),
                NumItem(fmt.money(tr.cost / tr.amount, tr.fiat), tr.cost / tr.amount, P["text_mid"]),
                text_item(tr.platform, P["text_mid"]),
                text_item(tr.note.replace("\n", " ")[:80], P["muted"]),
            ]
            cells[0].setData(Qt.ItemDataRole.UserRole, tr.id)
            for c, it in enumerate(cells):
                self.trades.setItem(i, c, it)
        self.trades.setSortingEnabled(True)

    def _split(self) -> None:
        targets = self.ctl.store.targets()
        try:
            amount = fmt.parse_number(self.split_in.text())
        except ValueError:
            self.split_out.setText("")
            return
        if not targets:
            self.split_out.setText(t("hold.no_targets"))
            return
        s = self.ctl.summary()
        values = {h.position.coin_id: h.value_fiat or 0.0 for h in s.holdings}
        split = split_contribution(amount, values, targets)
        names = self.ctl.store.target_symbols() | {OTHER: t("targets.other")}
        self.split_out.setText("   ".join(f"{names.get(k, k)} {fmt.money(v, self.ctl.settings.fiat, 0)}"
                                           for k, v in sorted(split.items(), key=lambda kv: -kv[1])))

    # ── actions ──────────────────────────────────────────────────────────────
    def on_show(self) -> None:
        self.render()

    def open_detail(self, detail: str) -> None:
        if detail == "add":
            self.add_trade()
        elif detail == "targets":
            self._edit_targets()
        elif detail in ("alloc", "trades"):
            self.tabs.setCurrentIndex(1 if detail == "alloc" else 2)

    def add_trade(self) -> None:
        from mazecrypto.ui.dialogs.trade_editor import TradeDialog

        if TradeDialog(self.ctl, self).exec():
            self.ctl.save_trades_changed()
            self.tabs.setCurrentIndex(2)

    def _trade_at(self, row: int):
        it = self.trades.item(row, 0)
        tid = it.data(Qt.ItemDataRole.UserRole) if it else None
        return next((x for x in self.ctl.store.trades() if x.id == tid), None)

    def _edit_trade(self, row: int) -> None:
        from mazecrypto.ui.dialogs.trade_editor import TradeDialog

        tr = self._trade_at(row)
        if tr and TradeDialog(self.ctl, self, tr).exec():
            self.ctl.save_trades_changed()

    def _delete_trade(self, row: int) -> None:
        tr = self._trade_at(row)
        if not tr:
            return
        rest = [x for x in self.ctl.store.trades() if x.id != tr.id]
        from mazecrypto.core.portfolio import build_positions

        try:
            build_positions(rest, self.ctl.settings.fiat, self.ctl.snap.fx.get(self.ctl.settings.fiat)
                            if self.ctl.snap else None)
        except ValueError:
            confirm(self, t("trade.delete"), t("trade.err_delete_breaks"), t("btn.ok"))
            return
        if confirm(self, t("trade.delete"), t("trade.delete_q", sym=tr.symbol, date=fmt.date(tr.ts)),
                   t("btn.delete"), danger=True):
            self.ctl.store.delete_trade(tr.id)
            self.ctl.save_trades_changed()

    def _trade_menu(self, pos) -> None:
        row = self.trades.rowAt(pos.y())
        if row < 0:
            return
        m = QMenu(self)
        m.addAction(t("btn.edit"), lambda: self._edit_trade(row))
        m.addAction(t("btn.delete"), lambda: self._delete_trade(row))
        m.exec(self.trades.viewport().mapToGlobal(pos))

    def _pos_double(self, row: int, _col: int) -> None:
        it = self.pos.item(row, 0)
        coin = next((c for c in self.ctl.tracked_coins() if c.id == it.data(Qt.ItemDataRole.UserRole)), None)
        win = self.window()
        if coin and hasattr(win, "open_chart"):
            win.open_chart(coin)

    def _edit_targets(self) -> None:
        from mazecrypto.ui.dialogs.targets_editor import TargetsDialog

        if TargetsDialog(self.ctl, self).exec():
            self.ctl.refresh()
            self.tabs.setCurrentIndex(1)

    def _export(self, kind: str) -> None:
        trades = self.ctl.store.trades()
        default = str(Path.home() / f"maze-crypto-trades-{date.today().isoformat()}.{kind}")
        path, _ = QFileDialog.getSaveFileName(self, t("hold.export_title"), default, f"*.{kind}")
        if path:
            Path(path).write_text(export.to_csv(trades) if kind == "csv" else export.to_json(trades), "utf-8")

    def _export_tax(self) -> None:
        fiat = self.ctl.settings.fiat
        years = sorted({date.fromtimestamp(x.ts).year for x in self.ctl.store.trades()}, reverse=True)
        if not years:
            return
        year, ok = QInputDialog.getItem(self, t("hold.export_tax"), t("hold.tax_year"), [str(y) for y in years],
                                        0, False)
        if not ok:
            return
        disposals, _ = export.yearly_report(self.ctl.store.trades(), int(year), fiat)
        default = str(Path.home() / f"maze-crypto-{year}-realized-{fiat}.csv")
        path, _ = QFileDialog.getSaveFileName(self, t("hold.export_title"), default, "*.csv")
        if path:
            Path(path).write_text(export.report_csv(disposals, fiat), "utf-8")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("hold.import"), str(Path.home()), "*.csv *.json")
        if not path:
            return
        try:
            text = Path(path).read_text("utf-8")
            trades = export.from_json(text) if path.endswith(".json") else export.from_csv(text)
        except (OSError, ValueError) as e:
            confirm(self, t("hold.import"), t("hold.import_err", err=e), t("btn.ok"))
            return
        if trades and confirm(self, t("hold.import"), t("hold.import_q", n=len(trades)), t("btn.add")):
            for tr in trades:
                tr.id = None
                self.ctl.store.add_trade(tr)
            self.ctl.save_trades_changed()
