"""SQLite storage.

* Profile database (``~/.local/share/maze-crypto/profiles/<name>.db``): the
  user's own data — trades, targets, rules, DCA plan, chart levels.
* Cache database (``~/.cache/maze-crypto/cache.db``): the last good response
  for every network request, so the app can show data while offline.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from mazecrypto.core import validate as v
from mazecrypto.core.dca import DcaPlan
from mazecrypto.core.models import Trade
from mazecrypto.core.rules import Rule, RuleState

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, coin_id TEXT NOT NULL, symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    amount REAL NOT NULL CHECK (amount > 0), cost REAL NOT NULL CHECK (cost >= 0),
    fiat TEXT NOT NULL, fee REAL NOT NULL DEFAULT 0 CHECK (fee >= 0),
    platform TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', usd_rate REAL
);
CREATE INDEX IF NOT EXISTS trades_ts ON trades(ts);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def check_trade(t: Trade) -> Trade:
    t.coin_id = v.coin_id(t.coin_id)
    t.symbol = v.symbol(t.symbol)
    t.fiat = v.fiat(t.fiat)
    if t.side not in ("buy", "sell"):
        raise v.ValidationError("side")
    t.amount = v.number(t.amount, name="amount", allow_zero=False)
    t.cost = v.number(t.cost, name="cost")
    t.fee = v.number(t.fee, name="fee")
    t.usd_rate = None if t.usd_rate is None else v.number(t.usd_rate, name="usd_rate", allow_zero=False)
    t.ts = int(v.number(t.ts, name="ts", allow_zero=False))
    t.platform, t.note = t.platform.strip()[:60], t.note.strip()[:2000]
    return t


class ProfileStore:
    def __init__(self, path: Path):
        self.path = path
        self.con = _connect(path)
        self.con.executescript(SCHEMA)

    def close(self) -> None:
        self.con.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self.con:
            yield self.con

    # trades ─────────────────────────────────────────────────────────────────
    def add_trade(self, t: Trade) -> int:
        t = check_trade(t)
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO trades (ts, coin_id, symbol, side, amount, cost, fiat, fee, platform, note, usd_rate)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (t.ts, t.coin_id, t.symbol, t.side, t.amount, t.cost, t.fiat, t.fee, t.platform, t.note,
                 t.usd_rate))
        t.id = int(cur.lastrowid or 0)
        return t.id

    def update_trade(self, t: Trade) -> None:
        if t.id is None:
            raise ValueError("trade has no id")
        t = check_trade(t)
        with self._tx() as c:
            c.execute("UPDATE trades SET ts=?, coin_id=?, symbol=?, side=?, amount=?, cost=?, fiat=?, fee=?,"
                      " platform=?, note=?, usd_rate=? WHERE id=?",
                      (t.ts, t.coin_id, t.symbol, t.side, t.amount, t.cost, t.fiat, t.fee, t.platform, t.note,
                       t.usd_rate, t.id))

    def delete_trade(self, trade_id: int) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM trades WHERE id=?", (trade_id,))

    def trades(self) -> list[Trade]:
        rows = self.con.execute(
            "SELECT ts, coin_id, symbol, amount, cost, fiat, fee, side, platform, note, usd_rate, id"
            " FROM trades ORDER BY ts, id").fetchall()
        return [Trade(*r) for r in rows]

    # key/value JSON ─────────────────────────────────────────────────────────
    def get(self, key: str, default: Any = None) -> Any:
        row = self.con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return default

    def put(self, key: str, value: Any) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value, ensure_ascii=False)))

    # typed helpers ──────────────────────────────────────────────────────────
    def targets(self) -> dict[str, float]:
        return {str(k): float(val) for k, val in (self.get("targets") or {}).items()}

    def set_targets(self, targets: dict[str, float]) -> None:
        self.put("targets", targets)

    def target_symbols(self) -> dict[str, str]:
        return dict(self.get("target_symbols") or {})

    def set_target_symbols(self, symbols: dict[str, str]) -> None:
        self.put("target_symbols", symbols)

    def rules(self) -> list[Rule]:
        out = []
        for d in self.get("rules") or []:
            try:
                out.append(Rule.from_dict(d))
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def set_rules(self, rules: list[Rule]) -> None:
        self.put("rules", [r.to_dict() for r in rules])

    def rule_state(self) -> dict[str, RuleState]:
        return {k: RuleState.from_dict(d) for k, d in (self.get("rule_state") or {}).items()}

    def set_rule_state(self, state: dict[str, RuleState]) -> None:
        self.put("rule_state", {k: s.to_dict() for k, s in state.items()})

    def dca_plan(self) -> DcaPlan | None:
        d = self.get("dca_plan")
        if not d:
            return None
        try:
            return DcaPlan.from_dict(d)
        except (KeyError, ValueError, TypeError):
            return None

    def set_dca_plan(self, plan: DcaPlan | None) -> None:
        if plan is not None:
            plan.validate()
        self.put("dca_plan", plan.to_dict() if plan else None)

    def dca_done(self) -> set[int]:
        return {int(i) for i in self.get("dca_done") or []}

    def set_dca_done(self, done: set[int]) -> None:
        self.put("dca_done", sorted(done))

    def levels(self, coin_id: str) -> list[float]:
        return [float(x) for x in self.get(f"levels:{coin_id}") or []]

    def set_levels(self, coin_id: str, levels: list[float]) -> None:
        self.put(f"levels:{coin_id}", sorted(levels))


class Cache:
    """Last good response per key. ``fresh()`` honours a TTL; ``stale()`` does not."""

    def __init__(self, path: Path):
        self.con = _connect(path)
        self.con.execute("CREATE TABLE IF NOT EXISTS cache"
                         " (key TEXT PRIMARY KEY, ts REAL NOT NULL, value TEXT NOT NULL)")
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    def put(self, key: str, value: Any, ts: float | None = None) -> None:
        with self.con:
            self.con.execute(
                "INSERT INTO cache (key, ts, value) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET ts=excluded.ts, value=excluded.value",
                (key, time.time() if ts is None else ts, json.dumps(value)))

    def _get(self, key: str) -> tuple[float, Any] | None:
        row = self.con.execute("SELECT ts, value FROM cache WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return float(row[0]), json.loads(row[1])
        except json.JSONDecodeError:
            return None

    def fresh(self, key: str, ttl: float, now: float | None = None) -> Any | None:
        hit = self._get(key)
        if hit is None or (now or time.time()) - hit[0] > ttl:
            return None
        return hit[1]

    def stale(self, key: str) -> tuple[float, Any] | None:
        """Value regardless of age, with the time it was stored."""
        return self._get(key)

    def prune(self, older_than: float) -> None:
        with self.con:
            self.con.execute("DELETE FROM cache WHERE ts < ?", (time.time() - older_than,))
