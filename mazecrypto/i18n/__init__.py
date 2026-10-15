"""Tiny JSON-based translation layer (English and Turkish).

``t("key", name=value)`` looks the key up in the active language, falls back to
English, then to the key itself, and formats ``{name}`` placeholders.
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent
LANGS = ("en", "tr")
_active = "en"


@cache
def _table(lang: str) -> dict[str, str]:
    try:
        return json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def set_language(lang: str) -> str:
    """English is the default; Turkish is opt-in from the settings."""
    global _active
    _active = lang if lang in LANGS else "en"
    return _active


def language() -> str:
    return _active


def upper_first(text: str) -> str:
    """Capitalise the first letter, minding the Turkish dotted/dotless i."""
    if not text:
        return text
    turkish = {"i": "İ", "ı": "I"} if _active == "tr" else {}
    return turkish.get(text[0], text[0].upper()) + text[1:]


def t(key: str, **kw: object) -> str:
    table = _table(_active)
    s = table[key] if key in table else _table("en").get(key, key)
    if kw:
        try:
            return s.format(**kw)
        except (KeyError, IndexError, ValueError):
            return s
    return s
