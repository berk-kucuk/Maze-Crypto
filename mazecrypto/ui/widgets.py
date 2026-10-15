"""Reusable widgets in the Maze glass style."""
from __future__ import annotations

import contextlib
import math
from collections.abc import Sequence

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mazecrypto.i18n import t
from mazecrypto.ui.glass import Glass, icon
from mazecrypto.ui.theme import P, rgba, tabular


def label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    if wrap:
        lb.setWordWrap(True)
    return lb


def button(text: str, name: str = "", slot=None, tip: str = "", icon_name: str = "") -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    if icon_name:
        color = P["on_accent"] if name == "Primary" else P["accent"] if name == "Link" else P["text"]
        b.setIcon(icon(icon_name, color))
        b.setIconSize(QSize(16, 16))
    return b


def hline() -> QFrame:
    f = QFrame()
    f.setObjectName("Separator")
    return f


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def scrolled(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setWidget(inner)
    return sa


def section(text: str) -> QLabel:
    return label(text.upper(), "SectionTitle")


class Page(QWidget):
    """Standard page: title + subtitle with actions on the right, then content in ``outer``."""

    def __init__(self, title: str, sub: str = ""):
        super().__init__()
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(28, 8, 28, 22)
        self.outer.setSpacing(0)
        head = QHBoxLayout()
        head.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.title_lbl = label(title, "PageTitle")
        self.sub_lbl = label(sub, "PageSub", wrap=True)
        col.addWidget(self.title_lbl)
        col.addWidget(self.sub_lbl)
        self.sub_lbl.setVisible(bool(sub))
        head.addLayout(col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        head.addLayout(self.actions)
        self.outer.addLayout(head)
        self.outer.addSpacing(16)

    def listen(self, signal, slot) -> None:
        """Connect to a long-lived (controller) signal; undone by :meth:`unlisten`.

        Pages are rebuilt when the language, mode or colour changes. A plain
        connect() to a Python callable outlives the deleted widgets and would
        then call into them on the next refresh.
        """
        if not hasattr(self, "_listening"):
            self._listening = []
        self._listening.append((signal, slot))
        signal.connect(slot)

    def unlisten(self) -> None:
        for signal, slot in getattr(self, "_listening", []):
            with contextlib.suppress(TypeError, RuntimeError):
                signal.disconnect(slot)
        self._listening = []

    def set_header(self, title: str, sub: str = "") -> None:
        self.title_lbl.setText(title)
        self.sub_lbl.setText(sub)
        self.sub_lbl.setVisible(bool(sub))

    def section(self, text: str) -> QLabel:
        lb = section(text)
        self.outer.addWidget(lb)
        return lb


class StatCard(Glass):
    def __init__(self, key: str, icon_name: str = ""):
        super().__init__(padding=16)
        self.lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        if icon_name:
            ic = QLabel()
            ic.setPixmap(icon(icon_name, P["muted"]).pixmap(16, 16))
            top.addWidget(ic)
        self.key = label(key, "CardKey")
        top.addWidget(self.key, 1)
        self.lay.addLayout(top)
        self.value = label("—", "CardValue")
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tabular(self.value)
        self.sub = label("", "CardSub", wrap=True)
        self.lay.addWidget(self.value)
        self.lay.addWidget(self.sub)

    def set(self, value: str, sub: str = "", color: str | None = None, sub_color: str | None = None) -> None:
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {color};" if color else "")
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        self.sub.setStyleSheet(f"color: {sub_color};" if sub_color else "")


def card_grid(cards: Sequence[QWidget], spacing: int = 12) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for c in cards:
        lay.addWidget(c, 1)
    return w


class TitledGlass(Glass):
    """Glass card with a small uppercase title row and optional right-side widget."""

    def __init__(self, title: str, right: QWidget | None = None, padding: int = 18):
        super().__init__(padding=padding)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.title = label(title.upper(), "CardTitle")
        row.addWidget(self.title, 1)
        if right is not None:
            row.addWidget(right)
        self.lay.addLayout(row)


class Sparkline(QWidget):
    def __init__(self, values: Sequence[float] = (), parent: QWidget | None = None, fill: bool = True):
        super().__init__(parent)
        self.values = list(values)
        self.fill = fill
        self.setMinimumSize(80, 26)

    def set_values(self, values: Sequence[float]) -> None:
        self.values = [v for v in values if math.isfinite(v)]
        self.update()

    def paintEvent(self, _e) -> None:
        if len(self.values) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lo, hi = min(self.values), max(self.values)
        span = (hi - lo) or 1.0
        r = QRectF(self.rect()).adjusted(3, 5, -3, -5)
        n = len(self.values) - 1
        pts = [QPointF(r.left() + r.width() * i / n, r.bottom() - (v - lo) / span * r.height())
               for i, v in enumerate(self.values)]
        line = QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        color = QColor(P["up"] if self.values[-1] >= self.values[0] else P["down"])
        if self.fill:
            area = QPainterPath(line)
            area.lineTo(r.right(), r.bottom())
            area.lineTo(r.left(), r.bottom())
            g = QLinearGradient(r.topLeft(), r.bottomLeft())
            c0 = QColor(color)
            c0.setAlpha(60)
            c1 = QColor(color)
            c1.setAlpha(0)
            g.setColorAt(0, c0)
            g.setColorAt(1, c1)
            p.fillPath(area, g)
        p.setPen(QPen(color, 1.6))
        p.drawPath(line)


class Segmented(QWidget):
    """Row of mutually exclusive toggle buttons."""

    changed = pyqtSignal(str)

    def __init__(self, options: Sequence[tuple[str, str]], current: str = ""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in options:
            b = button(text, "Seg")
            b.setCheckable(True)
            b.setChecked(key == current)
            self.group.addButton(b)
            self.buttons[key] = b
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            lay.addWidget(b)

    def set(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)

    def current(self) -> str:
        return next((k for k, b in self.buttons.items() if b.isChecked()), "")


class KeyItem(QTableWidgetItem):
    """Empty cell under a cell widget: no text of its own (it would show through
    when the row is selected), but it still sorts and carries the row's data."""

    def __init__(self, key: str, data=None):
        super().__init__("")
        self.key = key
        if data is not None:
            self.setData(Qt.ItemDataRole.UserRole, data)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, KeyItem):
            return self.key.lower() < other.key.lower()
        return super().__lt__(other)


class NumItem(QTableWidgetItem):
    """Table cell that sorts by its numeric value, with None sorted last."""

    def __init__(self, text: str, value: float | None, color: str | None = None,
                 align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignRight):
        super().__init__(text)
        self.num = value
        self.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        if color:
            self.setForeground(QColor(color))

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, NumItem):
            a = -math.inf if self.num is None else self.num
            b = -math.inf if other.num is None else other.num
            return a < b
        return super().__lt__(other)


def text_item(text: str, color: str | None = None, data=None) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    if color:
        it.setForeground(QColor(color))
    if data is not None:
        it.setData(Qt.ItemDataRole.UserRole, data)
    return it


LEVEL_STYLE = {
    "action": (P["accent"], "ph.lightning"),
    "warn": (P["warn"], "ph.warning"),
    "info": (P["info"], "ph.lightbulb"),
    "good": (P["up"], "ph.check-circle"),
}


class TipCard(Glass):
    """One assistant tip: coloured icon, title, explanation and an optional action."""

    actionRequested = pyqtSignal(str)

    def __init__(self, level: str, title: str, body: str, action: str = "", action_label: str = "",
                 compact: bool = False):
        color, icon_name = LEVEL_STYLE.get(level, LEVEL_STYLE["info"])
        super().__init__(padding=14 if compact else 16, tint=color if level in ("action", "warn") else None,
                         horizontal=True)
        self.lay.setSpacing(14)
        ic = QLabel()
        ic.setPixmap(icon(icon_name, color).pixmap(22, 22))
        ic.setFixedSize(36, 36)
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ic.setStyleSheet(f"background: {rgba(color, 0.16)}; border-radius: 18px;")
        self.lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        tl = label(title, "ExplainTitle", wrap=True)
        bl = label(body, "Explain", wrap=True)
        bl.setTextFormat(Qt.TextFormat.PlainText)
        col.addWidget(tl)
        col.addWidget(bl)
        self.lay.addLayout(col, 1)
        if action and action_label:
            b = button(action_label, "Link", lambda: self.actionRequested.emit(action), icon_name="ph.arrow-right")
            b.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
            self.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)


class Dialog(QDialog):
    """Dialog with the app's look and a Cancel / Save footer."""

    def __init__(self, parent: QWidget | None, title: str, ok_text: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(24, 22, 24, 20)
        self.body.setSpacing(10)
        self.body.addWidget(label(title, "PageTitle"))
        self.body.addSpacing(6)
        self.content = QVBoxLayout()
        self.content.setSpacing(10)
        self.body.addLayout(self.content, 1)
        self.error = label("", wrap=True)
        self.error.setStyleSheet(f"color: {P['down']};")
        self.error.hide()
        self.body.addWidget(self.error)
        self.buttons = QDialogButtonBox()
        self.ok = button(ok_text or t("btn.save"), "Primary")
        self.cancel = button(t("btn.cancel"))
        self.buttons.addButton(self.cancel, QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.addButton(self.ok, QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok.setDefault(True)
        self.buttons.accepted.connect(self._try_accept)
        self.buttons.rejected.connect(self.reject)
        self.body.addSpacing(8)
        self.body.addWidget(self.buttons)

    def show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def validate(self) -> bool:      # overridden by subclasses
        return True

    def _try_accept(self) -> None:
        if self.validate():
            self.accept()


def confirm(parent: QWidget, title: str, text: str, ok_text: str | None = None, danger: bool = False) -> bool:
    d = Dialog(parent, title, ok_text or t("btn.ok"))
    if danger:
        d.ok.setObjectName("Danger")
    d.content.addWidget(label(text, "Body", wrap=True))
    d.setMinimumWidth(400)
    return d.exec() == QDialog.DialogCode.Accepted
