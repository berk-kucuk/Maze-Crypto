#!/usr/bin/env python3
"""Render mazecrypto/assets/maze-crypto.svg into the PNG icon set.

    python3 tools/make_icons.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "mazecrypto" / "assets" / "maze-crypto.svg"
OUT = ROOT / "mazecrypto" / "assets" / "icons"
SIZES = (32, 48, 64, 128, 256, 512)


def main() -> None:
    QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])
    svg = QSvgRenderer(str(SVG))
    if not svg.isValid():
        sys.exit(f"invalid SVG: {SVG}")
    OUT.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        svg.render(p, QRectF(0, 0, size, size))
        p.end()
        img.save(str(OUT / f"maze-crypto-{size}.png"))
    print(f"wrote {len(SIZES)} icons to {OUT}")


if __name__ == "__main__":
    main()
