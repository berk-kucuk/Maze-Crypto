"""About: version, data sources, privacy, and the not-investment-advice notice."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from mazecrypto import __version__
from mazecrypto.core.settings import config_path, data_dir
from mazecrypto.i18n import t
from mazecrypto.providers.service import allowed_hosts
from mazecrypto.ui.widgets import Page, TitledGlass, label, scrolled


class AboutPage(Page):
    def __init__(self):
        super().__init__(t("about.title"), f"Maze Crypto {__version__} · {t('app.tagline')}")
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(14)
        lay.addWidget(label(t("about.disclaimer"), "Disclaimer", wrap=True))

        def card(title: str, body: str) -> TitledGlass:
            g = TitledGlass(title)
            b = label(body, "Body", wrap=True)
            b.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            g.lay.addWidget(b)
            return g

        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(card(t("about.what"), t("about.what_body")), 1)
        row.addWidget(card(t("about.privacy"), t("about.privacy_body")), 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(card(t("about.keys"), t("about.keys_body")), 1)
        row.addWidget(card(t("about.ai"), t("about.ai_body")), 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addWidget(card(t("about.sources"), "\n".join(f"·  {h}" for h in sorted(allowed_hosts()))), 1)
        row.addWidget(card(t("about.files"), f"{t('about.config')}:\n{config_path()}\n\n{t('about.data')}:\n"
                                             f"{data_dir()}"), 1)
        lay.addLayout(row)
        lay.addStretch()
        self.outer.addWidget(scrolled(inner), 1)
