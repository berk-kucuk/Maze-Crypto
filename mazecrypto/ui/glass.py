"""Glass surfaces in the Maze style, painted with QPainter (no compositor needed).

* :class:`Aurora` — the window background: OLED black with two very faint
  coloured lights that drift slowly, only while the window is focused.
* :class:`Glass` — a translucent card with a hairline rim that is brighter
  along the top edge, like lit glass. Everything readable sits on these.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from mazecrypto.ui.theme import R_LG, P


def _alpha(hex_color: str, alpha: int) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c


class Aurora(QWidget):
    """Window background with slow, faint gold and violet light.

    With ``corner_radius`` the background is a rounded rectangle with a hairline
    rim, for a frameless window whose own background is transparent; square
    again while the window fills the screen (maximised or full screen).
    """

    def __init__(self, parent: QWidget | None = None, animated: bool = True, corner_radius: int = 0):
        super().__init__(parent)
        self.corner_radius = corner_radius
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._phase = 0.0
        self._animated = animated
        self._timer = QTimer(self)
        self._timer.setInterval(80)          # ~12 fps is plenty for a drift this slow
        self._timer.timeout.connect(self._tick)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.window().installEventFilter(self)
        self._sync()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._timer.stop()

    def eventFilter(self, _obj, e) -> bool:
        if e.type() in (QEvent.Type.ActivationChange, QEvent.Type.WindowStateChange):
            self._sync()
            self.update()
        return False

    def _sync(self) -> None:
        w = self.window()
        want = self._animated and self.isVisible() and w.isActiveWindow() and not w.isMinimized()
        if want and not self._timer.isActive():
            self._timer.start()
        elif not want and self._timer.isActive():
            self._timer.stop()

    def _tick(self) -> None:
        self._phase += 0.005
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        win = self.window()
        radius = 0 if win.isMaximized() or win.isFullScreen() else self.corner_radius
        if radius:
            clip = QPainterPath()
            clip.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
            p.setClipPath(clip)
        p.fillRect(r, QColor(P["bg"]))
        w, h = r.width(), r.height()
        ph = self._phase
        lights = (
            (0.12 + 0.05 * math.sin(ph), -0.08 + 0.05 * math.cos(ph * 0.8), 0.60, _alpha(P["glow"], 22)),
            (0.95 + 0.04 * math.cos(ph * 0.7), 0.95 + 0.05 * math.sin(ph * 1.1), 0.55, _alpha(P["glow2"], 14)),
            (0.55 + 0.06 * math.sin(ph * 0.5), 0.45 + 0.05 * math.cos(ph * 0.6), 0.40, QColor(255, 255, 255, 6)),
        )
        p.setPen(Qt.PenStyle.NoPen)
        for fx, fy, fr, col in lights:
            c = QPointF(fx * w, fy * h)
            rad = fr * max(w, h)
            g = QRadialGradient(c, rad)
            g.setColorAt(0.0, col)
            mid = QColor(col)
            mid.setAlpha(col.alpha() // 3)
            g.setColorAt(0.5, mid)
            end = QColor(col)
            end.setAlpha(0)
            g.setColorAt(1.0, end)
            p.setBrush(QBrush(g))
            p.drawEllipse(c, rad, rad)
        if radius:
            # A hairline rim, so the rounded edge reads against any desktop.
            p.setClipping(False)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 30), 1))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        p.end()


class Glass(QFrame):
    """Translucent rounded card. Use ``.lay`` (a QVBoxLayout) for its content."""

    def __init__(self, parent: QWidget | None = None, padding: int = 18, radius: int = R_LG,
                 tint: str | None = None, horizontal: bool = False):
        super().__init__(parent)
        self.radius = radius
        self.tint = QColor(tint) if tint else None
        self.lay = QHBoxLayout(self) if horizontal else QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding, padding, padding)
        self.lay.setSpacing(10)

    def set_tint(self, tint: str | None) -> None:
        self.tint = QColor(tint) if tint else None
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        fill = QLinearGradient(r.topLeft(), r.bottomLeft())
        fill.setColorAt(0.0, QColor(255, 255, 255, 17))
        fill.setColorAt(1.0, QColor(255, 255, 255, 8))
        p.fillPath(path, fill)
        if self.tint is not None:
            t = QLinearGradient(r.topLeft(), r.bottomRight())
            c0 = QColor(self.tint)
            c0.setAlpha(34)
            c1 = QColor(self.tint)
            c1.setAlpha(6)
            t.setColorAt(0.0, c0)
            t.setColorAt(1.0, c1)
            p.fillPath(path, t)
        rim = QLinearGradient(r.topLeft(), r.bottomLeft())
        rim.setColorAt(0.0, QColor(255, 255, 255, 46))
        rim.setColorAt(0.15, QColor(255, 255, 255, 20))
        rim.setColorAt(1.0, QColor(255, 255, 255, 12))
        p.setPen(QPen(QBrush(rim), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, self.radius, self.radius)
        p.end()


def icon(name: str, color: str | None = None) -> QIcon:
    """Phosphor icon via qtawesome; an empty icon when qtawesome is missing."""
    try:
        import qtawesome as qta

        return qta.icon(name, color=color or P["text_mid"], color_active=P["accent"])
    except Exception:  # noqa: BLE001 - icons are decoration, never fatal
        return QIcon()
