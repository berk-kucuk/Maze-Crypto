"""Maze Crypto design system: tokens, type ramp and the global stylesheet.

Follows the Maze family (Maze-AI, Maze Guard): OLED black, near-black glass
cards with a lit rim, Inter for text, sentence-case headings. One accent
colour marks the primary action, focus and the active page — champagne by
default, selectable in Settings. Green and red only ever mean up/down or
profit/loss; orange means "needs attention".
"""
from __future__ import annotations

import os
from pathlib import Path

# Accent presets. Each avoids the colours that already carry meaning: green
# (up / profit), red (down / loss) and orange (needs attention).
#   accent, accent_hi, accent_bg (opaque tint), on_accent (text on it),
#   glow + glow2 (the two background lights), series3 (third chart line, must differ from accent)
ACCENTS: dict[str, tuple[str, str, str, str, str, str, str]] = {
    "mono":      ("#ededf0", "#ffffff", "#232328", "#0a0a0b", "#ffffff", "#ffffff", "#a78bfa"),
    "champagne": ("#e9d8b4", "#f6ead0", "#29241a", "#17130a", "#e9d8b4", "#ffffff", "#a78bfa"),
    "gold":      ("#f5b841", "#ffd27a", "#2a2010", "#1a1200", "#f5b841", "#a78bfa", "#a78bfa"),
    "violet":    ("#a78bfa", "#c4b5fd", "#1f1a33", "#140f24", "#a78bfa", "#f5b841", "#f5b841"),
}
DEFAULT_ACCENT = "champagne"
ACCENT_NAME = DEFAULT_ACCENT
_A = ACCENTS[DEFAULT_ACCENT]

P = {
    "bg":        "#000000",
    "base":      "#050506",
    "surface":   "#0e0e11",     # opaque panels (menus, tooltips, dialogs)
    "raised":    "#16161a",
    "border":    "#232329",
    "border2":   "#34343c",
    "text":      "#ededf0",
    "text_mid":  "#a1a1aa",
    "muted":     "#71717a",
    "dim":       "#3f3f46",
    "accent":    _A[0],
    "accent_hi": _A[1],
    "accent_bg": _A[2],         # opaque accent tint (selection)
    "on_accent": _A[3],         # text on an accent button
    "glow":      _A[4],         # faint lights behind the window
    "glow2":     _A[5],
    "accent2":   "#2dd4bf",     # teal: second chart series
    "violet":    _A[6],         # third chart series
    "up":        "#34d399",
    "down":      "#f87171",
    "warn":      "#fb923c",
    "info":      "#93c5fd",
}

# Translucent layers for glass, as QSS rgba() strings.
GLASS = "rgba(255, 255, 255, 0.045)"
GLASS_HI = "rgba(255, 255, 255, 0.075)"
GLASS_LINE = "rgba(255, 255, 255, 0.09)"
GLASS_LINE_HI = "rgba(255, 255, 255, 0.16)"

def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {alpha})"


GOLD_SOFT = _rgba(P["accent"], 0.13)       # named for the original gold; follows the accent
GOLD_LINE = _rgba(P["accent"], 0.45)


def set_accent(name: str) -> str:
    """Switch the accent preset; widgets read ``P`` when they paint, the QSS via build_qss()."""
    global ACCENT_NAME, GOLD_SOFT, GOLD_LINE
    name = name if name in ACCENTS else DEFAULT_ACCENT
    a = ACCENTS[name]
    P.update(accent=a[0], accent_hi=a[1], accent_bg=a[2], on_accent=a[3], glow=a[4], glow2=a[5], violet=a[6])
    GOLD_SOFT = _rgba(P["accent"], 0.13)
    GOLD_LINE = _rgba(P["accent"], 0.45)
    ACCENT_NAME = name
    return name

SANS_FAMILIES = ["Inter", "Inter Variable", "Noto Sans", "Cantarell", "DejaVu Sans"]
MONO_FAMILIES = ["JetBrains Mono", "Fira Code", "DejaVu Sans Mono", "Noto Sans Mono"]
SANS = "'Inter', 'Inter Variable', 'Noto Sans', 'DejaVu Sans', sans-serif"
MONO = "'JetBrains Mono', 'Fira Code', 'DejaVu Sans Mono', monospace"

R_SM, R_MD, R_LG = 6, 10, 14
# QSS needs files for images; forward slashes work everywhere.
_CHEVRON = (Path(__file__).resolve().parent.parent / "assets" / "chevron-down.svg").as_posix()


def tabular(widget) -> None:
    """Tabular figures for a widget showing columns of numbers: every digit gets one width."""
    from PyQt6.QtGui import QFont

    f = QFont(widget.font())
    f.setFeature(QFont.Tag("tnum"), 1)
    widget.setFont(f)


def change_color(value: float | None) -> str:
    if value is None or abs(value) < 0.005:      # shows as 0.00
        return P["text_mid"]
    return P["up"] if value > 0 else P["down"]


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


def build_qss() -> str:
    return f"""
* {{ outline: none; }}
QWidget {{
    background: transparent; color: {P['text']};
    font-family: {SANS}; font-size: 10.5pt;
    selection-background-color: {P['accent_bg']}; selection-color: {P['accent_hi']};
}}
/* after the QWidget rule: equal specificity, so order decides */
QMainWindow, QDialog, QMessageBox, QInputDialog, QFileDialog {{ background-color: {P['base']}; }}
QMainWindow#MainWin {{ background: transparent; }}

/* ── chrome ───────────────────────────────────────────── */
#AppName {{ font-size: 12pt; font-weight: 700; letter-spacing: 0.3px; }}
#AppTag {{ font-size: 8.5pt; color: {P['muted']}; }}
QPushButton#WinBtn, QPushButton#WinClose {{ background: transparent; border: none; color: {P['muted']};
    font-size: 11pt; min-width: 40px; min-height: 34px; padding: 0; border-radius: {R_SM}px; }}
QPushButton#WinBtn:hover {{ background-color: {GLASS_HI}; color: {P['text']}; }}
QPushButton#WinClose:hover {{ background-color: #c42b1c; color: #ffffff; }}
#StatusPill {{ font-size: 9pt; font-weight: 600; color: {P['text_mid']}; padding: 4px 10px;
    border-radius: 11px; background: {GLASS}; border: 1px solid {GLASS_LINE}; }}
#StatusPill[state="live"] {{ color: {P['up']}; border-color: {rgba(P['up'], 0.35)};
    background: {rgba(P['up'], 0.08)}; }}
#StatusPill[state="stale"] {{ color: {P['warn']}; border-color: {rgba(P['warn'], 0.35)};
    background: {rgba(P['warn'], 0.08)}; }}
#StatusPill[state="offline"] {{ color: {P['down']}; border-color: {rgba(P['down'], 0.35)};
    background: {rgba(P['down'], 0.08)}; }}
#StatusPill[state="busy"] {{ color: {P['accent']}; border-color: {GOLD_LINE}; background: {GOLD_SOFT}; }}
#ProxyPill {{ font-size: 9pt; font-weight: 600; color: {P['violet']}; padding: 4px 10px; border-radius: 11px;
    background: {rgba(P['violet'], 0.08)}; border: 1px solid {rgba(P['violet'], 0.35)}; }}
#ModePill {{ font-size: 8.5pt; font-weight: 600; color: {P['accent']}; padding: 2px 8px; border-radius: 9px;
    background: {GOLD_SOFT}; }}

QPushButton#NavButton {{ background: transparent; border: none; border-radius: {R_MD}px; color: {P['text_mid']};
    font-size: 10.5pt; font-weight: 500; text-align: left; padding: 9px 12px; }}
QPushButton#NavButton:hover {{ background-color: {GLASS}; color: {P['text']}; }}
QPushButton#NavButton[active="true"] {{ background-color: {GOLD_SOFT}; color: {P['text']}; font-weight: 600; }}
#NavHeading {{ font-size: 8pt; font-weight: 600; letter-spacing: 1.2px; color: {P['dim']}; }}
#SideFoot {{ font-size: 8.5pt; color: {P['muted']}; }}

/* ── type ─────────────────────────────────────────────── */
#PageTitle {{ font-size: 17pt; font-weight: 600; }}
#PageSub {{ font-size: 10pt; color: {P['text_mid']}; }}
#SectionTitle {{ font-size: 8.5pt; font-weight: 600; letter-spacing: 1.1px; color: {P['muted']};
    padding: 14px 2px 6px 2px; }}
#CardTitle {{ font-size: 8.5pt; font-weight: 600; letter-spacing: 1.1px; color: {P['muted']}; }}
#CardKey {{ font-size: 9.5pt; color: {P['text_mid']}; }}
#CardValue {{ font-size: 19pt; font-weight: 600; }}
#CardSub {{ font-size: 9.5pt; color: {P['text_mid']}; }}
#BigValue {{ font-size: 28pt; font-weight: 700; }}
#Muted {{ color: {P['muted']}; }}
#Mid {{ color: {P['text_mid']}; }}
#Small {{ font-size: 9pt; color: {P['muted']}; }}
#Body {{ color: {P['text_mid']}; font-size: 10.5pt; }}
#Strong {{ font-weight: 600; }}
#Mono {{ font-family: {MONO}; font-size: 9.5pt; color: {P['text_mid']}; }}
#Placeholder {{ color: {P['muted']}; font-size: 11pt; }}
#Badge {{ font-size: 8.5pt; font-weight: 600; padding: 2px 8px; border-radius: 9px; color: {P['text_mid']};
    background: {GLASS_HI}; }}
#Disclaimer {{ color: {P['accent_hi']}; background: {rgba(P['accent'], 0.07)};
    border: 1px solid {rgba(P['accent'], 0.25)};
    border-radius: {R_MD}px; padding: 12px 14px; font-size: 10pt; }}
#Separator {{ background-color: {GLASS_LINE}; min-height: 1px; max-height: 1px; }}
#Explain {{ color: {P['text_mid']}; font-size: 10pt; }}
#ExplainTitle {{ font-weight: 600; font-size: 10.5pt; }}

/* ── buttons ──────────────────────────────────────────── */
QPushButton {{ font-size: 10pt; font-weight: 600; color: {P['text']}; background: {GLASS_HI};
    border: 1px solid {GLASS_LINE}; border-radius: {R_MD}px; padding: 8px 16px; }}
QPushButton:hover {{ background: rgba(255, 255, 255, 0.11); border-color: {GLASS_LINE_HI}; }}
QPushButton:pressed {{ background: rgba(255, 255, 255, 0.05); }}
QPushButton:focus {{ border-color: {GOLD_LINE}; }}
QPushButton#Primary {{ background: {P['accent']}; color: {P['on_accent']}; border: 1px solid {P['accent']}; }}
QPushButton#Primary:hover {{ background: {P['accent_hi']}; border-color: {P['accent_hi']}; }}
QPushButton#Danger {{ background: {rgba(P['down'], 0.10)}; color: {P['down']}; border-color: {rgba(P['down'], 0.35)}; }}
QPushButton#Danger:hover {{ background: {rgba(P['down'], 0.18)}; }}
QPushButton#Seg {{ padding: 5px 12px; border-radius: 8px; color: {P['text_mid']}; background: transparent;
    border: 1px solid transparent; font-weight: 600; }}
QPushButton#Seg:hover {{ color: {P['text']}; background: {GLASS}; }}
QPushButton#Seg:checked {{ color: {P['on_accent']}; background: {P['accent']}; border-color: {P['accent']}; }}
QPushButton#Link {{ background: transparent; border: none; padding: 4px 6px; color: {P['accent']}; font-weight: 600; }}
QPushButton#Link:hover {{ color: {P['accent_hi']}; }}
QPushButton#Chip {{ background: {GLASS}; border: 1px solid {GLASS_LINE}; border-radius: 14px; padding: 5px 12px;
    font-weight: 500; color: {P['text_mid']}; }}
QPushButton#Chip:hover {{ color: {P['text']}; border-color: {GOLD_LINE}; }}
QPushButton:disabled {{ color: {P['dim']}; background: transparent; border-color: {GLASS_LINE}; }}
QPushButton::menu-indicator {{ subcontrol-position: right center; right: 8px; width: 0; }}

/* ── inputs ───────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background: rgba(255, 255, 255, 0.04); border: 1px solid {GLASS_LINE}; border-radius: {R_MD}px;
    padding: 7px 10px; color: {P['text']}; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QDateEdit:hover, QComboBox:hover {{
    border-color: {GLASS_LINE_HI}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {GOLD_LINE}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {P['dim']}; }}
QComboBox::drop-down, QDateEdit::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right;
    border: none; width: 28px; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url("{_CHEVRON}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background-color: {P['surface']}; border: 1px solid {P['border2']};
    border-radius: {R_SM}px; selection-background-color: {P['accent_bg']}; selection-color: {P['accent_hi']};
    padding: 4px; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 0; border: none; }}
QCheckBox {{ spacing: 9px; color: {P['text']}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {GLASS_LINE_HI};
    background: rgba(255, 255, 255, 0.04); }}
QCheckBox::indicator:checked {{ background: {P['accent']}; border-color: {P['accent']}; }}
QCheckBox::indicator:hover {{ border-color: {GOLD_LINE}; }}
QListWidget::indicator, QTableWidget::indicator {{ width: 14px; height: 14px; border-radius: 4px;
    border: 1px solid {GLASS_LINE_HI}; background: rgba(255, 255, 255, 0.04); }}
QListWidget::indicator:checked, QTableWidget::indicator:checked {{ background: {P['accent']};
    border-color: {P['accent']}; }}
QCalendarWidget QWidget {{ background-color: {P['surface']}; alternate-background-color: {P['surface']}; }}
QCalendarWidget QToolButton {{ color: {P['text']}; background: transparent; border: none; padding: 4px; }}
QCalendarWidget QAbstractItemView:enabled {{ color: {P['text']}; selection-background-color: {P['accent']};
    selection-color: {P['on_accent']}; }}

/* ── tables / lists (they sit inside glass cards) ─────── */
QTableView, QTableWidget, QListWidget {{ background: transparent; border: none; gridline-color: transparent;
    font-size: 10.5pt; }}
QTableView::item, QTableWidget::item {{ padding: 0 8px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); }}
QTableView::item:selected, QTableWidget::item:selected, QListWidget::item:selected {{
    background-color: {GOLD_SOFT}; color: {P['text']}; }}
QTableView::item:hover, QTableWidget::item:hover, QListWidget::item:hover {{ background-color: {GLASS}; }}
QListWidget::item {{ padding: 8px 10px; border-radius: {R_SM}px; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: transparent; color: {P['muted']}; border: none;
    border-bottom: 1px solid {GLASS_LINE}; padding: 8px 8px; font-size: 9pt; font-weight: 600; }}
QHeaderView::section:hover {{ color: {P['text']}; }}
QTableCornerButton::section {{ background: transparent; border: none; }}

/* ── scroll / tooltip / menu / tabs ───────────────────── */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 1px 2px 0; }}
QScrollBar::handle:vertical {{ background: {P['border']}; border-radius: 4px; min-height: 36px; margin: 0 2px; }}
QScrollBar::handle:vertical:hover {{ background: {P['border2']}; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 2px 1px 2px; }}
QScrollBar::handle:horizontal {{ background: {P['border']}; border-radius: 4px; min-width: 36px; margin: 2px 0; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background-color: {P['raised']}; color: {P['text']}; border: 1px solid {P['border2']};
    padding: 6px 9px; border-radius: {R_SM}px; font-size: 9.5pt; }}
QMenu {{ background-color: #121216; border: 1px solid {P['border2']}; border-radius: {R_MD}px; padding: 6px; }}
QMenu::item {{ background: transparent; padding: 7px 26px 7px 12px; border-radius: {R_SM}px; }}
QMenu::item:selected {{ background-color: {P['raised']}; color: {P['accent_hi']}; }}
QMenu::separator {{ height: 1px; background: {P['border']}; margin: 5px 8px; }}
QProgressBar {{ background: {GLASS_HI}; border: none; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {P['accent']}; border-radius: 3px; }}
QTabBar::tab {{ background: transparent; color: {P['text_mid']}; padding: 7px 14px; margin-right: 4px;
    border-radius: 8px; font-weight: 600; }}
QTabBar::tab:hover {{ color: {P['text']}; background: {GLASS}; }}
QTabBar::tab:selected {{ color: {P['on_accent']}; background: {P['accent']}; }}
QTabWidget::pane {{ border: none; }}
"""


set_accent(os.environ.get("MAZE_CRYPTO_ACCENT", DEFAULT_ACCENT))
QSS = build_qss()
