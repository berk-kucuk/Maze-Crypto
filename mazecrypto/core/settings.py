"""User preferences in ``~/.config/maze-crypto/config.toml``.

Only preferences live here (language, currency, exchanges, watchlist, proxy).
Portfolio data — trades, targets, rules, DCA plans — lives in the profile's
SQLite database, see :mod:`mazecrypto.core.store`.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from mazecrypto.core import validate as v
from mazecrypto.core.models import Coin

APP_DIR = "maze-crypto"
LANGUAGES = ("en", "tr")
INTERVALS = ("1h", "4h", "1d", "1w")
ACCENTS = ("mono", "champagne", "gold", "violet")     # presets live in ui/theme.py
PROFILE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _xdg(var: str, fallback: str) -> Path:
    override = os.environ.get("MAZE_CRYPTO_HOME")
    if override:
        return Path(override) / fallback.rsplit("/", 1)[-1]
    return Path(os.environ.get(var) or Path.home() / fallback) / APP_DIR


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache")


@dataclass(slots=True)
class ProxySettings:
    enabled: bool = False
    url: str = "socks5h://127.0.0.1:9050"   # socks5h: DNS goes through the proxy too

    def validate(self) -> None:
        u = urlparse(self.url)
        if u.scheme not in ("socks5", "socks5h", "http", "https") or not u.hostname or not u.port:
            raise ValueError("proxy must look like socks5://host:port")


@dataclass(slots=True)
class Settings:
    language: str = "en"
    fiat: str = "USD"
    secondary_fiat: str = ""          # optional second column, e.g. USD next to TRY
    exchanges: list[str] = field(default_factory=lambda: ["binance"])
    watchlist: list[Coin] = field(default_factory=list)
    refresh_seconds: int = 60
    notifications: bool = True
    chart_interval: str = "1d"
    profile: str = "default"
    onboarded: bool = False
    expert: bool = False              # basic mode unless the user opts in
    accent: str = "champagne"         # interface colour: mono | champagne | gold | violet
    ai_enabled: bool = True           # local Ollama assistant, shown only when Ollama runs
    ai_model: str = ""                # "" = Maze-AI's model, else the first one installed
    proxy: ProxySettings = field(default_factory=ProxySettings)

    def validate(self) -> None:
        if self.language not in LANGUAGES:
            self.language = "en"
        self.fiat = v.fiat(self.fiat)
        if self.secondary_fiat:
            self.secondary_fiat = v.fiat(self.secondary_fiat)
            if self.secondary_fiat == self.fiat:
                self.secondary_fiat = ""
        self.refresh_seconds = max(15, min(3600, int(self.refresh_seconds)))
        if self.chart_interval not in INTERVALS:
            self.chart_interval = "1d"
        self.ai_model = self.ai_model.strip()[:80]
        if self.accent not in ACCENTS:
            self.accent = "champagne"
        if not PROFILE_RE.match(self.profile):
            raise ValueError("profile name")
        self.proxy.validate()
        seen: set[str] = set()
        self.watchlist = [c for c in self.watchlist if not (c.id in seen or seen.add(c.id))]

    def to_dict(self) -> dict:
        return {
            "language": self.language, "fiat": self.fiat, "secondary_fiat": self.secondary_fiat,
            "exchanges": list(self.exchanges), "refresh_seconds": self.refresh_seconds,
            "notifications": self.notifications, "chart_interval": self.chart_interval,
            "profile": self.profile, "onboarded": self.onboarded, "expert": self.expert, "accent": self.accent,
            "ai_enabled": self.ai_enabled, "ai_model": self.ai_model,
            "proxy": {"enabled": self.proxy.enabled, "url": self.proxy.url},
            "watchlist": [c.to_dict() for c in self.watchlist],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Settings:
        s = cls()
        for key in ("language", "fiat", "secondary_fiat", "refresh_seconds", "notifications",
                    "chart_interval", "profile", "onboarded", "expert", "accent", "ai_enabled", "ai_model"):
            if key in d:
                setattr(s, key, type(getattr(s, key))(d[key]))
        if isinstance(d.get("exchanges"), list):
            s.exchanges = [str(x) for x in d["exchanges"]]
        p = d.get("proxy") or {}
        s.proxy = ProxySettings(bool(p.get("enabled", False)), str(p.get("url", s.proxy.url)))
        wl = []
        for c in d.get("watchlist") or []:
            try:
                wl.append(Coin(v.coin_id(c["id"]), v.symbol(c["symbol"]), str(c.get("name", ""))[:60]))
            except (KeyError, TypeError, v.ValidationError):
                continue
        s.watchlist = wl
        s.validate()
        return s


# ── minimal TOML writer (the stdlib only reads TOML) ─────────────────────────


def _toml_value(x: object) -> str:
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, (int, float)):
        return repr(x)
    if isinstance(x, str):
        esc = x.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{esc}"'
    if isinstance(x, list) and all(not isinstance(i, dict) for i in x):
        return "[" + ", ".join(_toml_value(i) for i in x) + "]"
    raise TypeError(f"cannot write {type(x).__name__} to TOML")


def dumps_toml(d: dict) -> str:
    lines: list[str] = []
    tables, arrays = {}, {}
    for k, val in d.items():
        if isinstance(val, dict):
            tables[k] = val
        elif isinstance(val, list) and val and all(isinstance(i, dict) for i in val):
            arrays[k] = val
        elif isinstance(val, list) and not val:
            lines.append(f"{k} = []")
        else:
            lines.append(f"{k} = {_toml_value(val)}")
    for k, t in tables.items():
        lines += ["", f"[{k}]"] + [f"{kk} = {_toml_value(vv)}" for kk, vv in t.items()]
    for k, arr in arrays.items():
        for item in arr:
            lines += ["", f"[[{k}]]"] + [f"{kk} = {_toml_value(vv)}" for kk, vv in item.items()]
    return "\n".join(lines) + "\n"


def config_path() -> Path:
    return config_dir() / "config.toml"


def load(path: Path | None = None) -> Settings:
    path = path or config_path()
    try:
        with path.open("rb") as f:
            return Settings.from_dict(tomllib.load(f))
    except FileNotFoundError:
        return Settings()
    except (tomllib.TOMLDecodeError, ValueError, TypeError):
        # A broken config must not stop the app; keep it for the user to inspect.
        path.replace(path.with_suffix(".toml.broken"))
        return Settings()


def save(settings: Settings, path: Path | None = None) -> None:
    settings.validate()
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text("# Maze Crypto settings — edited by the app, safe to edit by hand.\n"
                   + dumps_toml(settings.to_dict()), encoding="utf-8")
    tmp.replace(path)
