"""Trade import/export (CSV, JSON) and a yearly realised profit/loss statement."""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from mazecrypto.core.models import Trade
from mazecrypto.core.portfolio import EPS
from mazecrypto.core.store import check_trade

FIELDS = ("date", "side", "coin_id", "symbol", "amount", "cost", "fee", "fiat", "usd_rate", "platform", "note")


def _row(t: Trade) -> dict:
    return {"date": datetime.fromtimestamp(t.ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "side": t.side,
            "coin_id": t.coin_id, "symbol": t.symbol, "amount": repr(t.amount), "cost": repr(t.cost),
            "fee": repr(t.fee), "fiat": t.fiat, "usd_rate": "" if t.usd_rate is None else repr(t.usd_rate),
            "platform": t.platform, "note": t.note}


def _safe_cell(value: str) -> str:
    # Spreadsheet apps execute cells starting with = + - @ — neutralise them.
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def to_csv(trades: list[Trade]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    for t in trades:
        row = _row(t)
        row["platform"], row["note"] = _safe_cell(row["platform"]), _safe_cell(row["note"])
        w.writerow(row)
    return buf.getvalue()


def to_json(trades: list[Trade]) -> str:
    return json.dumps({"format": "maze-crypto-trades", "version": 1, "trades": [_row(t) for t in trades]},
                      ensure_ascii=False, indent=2)


def _parse_date(s: str) -> int:
    s = s.strip()
    for f in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return int(datetime.strptime(s, f).replace(tzinfo=UTC).timestamp())
        except ValueError:
            continue
    raise ValueError(f"bad date: {s!r}")


def _from_row(r: dict) -> Trade:
    def strip_guard(v: str) -> str:
        return v[1:] if v[:2] in ("'=", "'+", "'-", "'@") else v

    return check_trade(Trade(
        ts=_parse_date(r["date"]), coin_id=r["coin_id"], symbol=r["symbol"], amount=float(r["amount"]),
        cost=float(r["cost"]), fiat=r["fiat"], fee=float(r.get("fee") or 0), side=(r.get("side") or "buy").lower(),
        platform=strip_guard(r.get("platform") or ""), note=strip_guard(r.get("note") or ""),
        usd_rate=float(r["usd_rate"]) if r.get("usd_rate") else None))


def from_csv(text: str) -> list[Trade]:
    """Parse trades; raises ValueError naming the first bad line."""
    reader = csv.DictReader(io.StringIO(text))
    missing = {"date", "coin_id", "symbol", "amount", "cost", "fiat"} - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"missing columns: {', '.join(sorted(missing))}")
    out = []
    for n, r in enumerate(reader, start=2):
        try:
            out.append(_from_row(r))
        except (ValueError, KeyError, TypeError) as e:
            raise ValueError(f"line {n}: {e}") from None
    return out


def from_json(text: str) -> list[Trade]:
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("format") != "maze-crypto-trades":
        raise ValueError("not a Maze Crypto trade file")
    return [_from_row({k: str(v) if v is not None else "" for k, v in r.items()}) for r in data["trades"]]


@dataclass(frozen=True, slots=True)
class Disposal:
    ts: int
    symbol: str
    amount: float
    proceeds: float
    cost_basis: float

    @property
    def gain(self) -> float:
        return self.proceeds - self.cost_basis


def yearly_report(trades: list[Trade], year: int, fiat: str) -> tuple[list[Disposal], list[Trade]]:
    """Sells in ``year`` with their average-cost basis, plus all of that year's trades.

    Only trades in ``fiat`` are included: mixing currencies would need
    historical exchange rates the app does not have.
    """
    pos: dict[str, tuple[float, float]] = {}
    disposals: list[Disposal] = []
    in_year: list[Trade] = []
    for t in sorted(trades, key=lambda t: (t.ts, t.id or 0)):
        if t.fiat != fiat:
            continue
        y = datetime.fromtimestamp(t.ts, UTC).year
        amount, cost = pos.get(t.coin_id, (0.0, 0.0))
        if t.side == "buy":
            pos[t.coin_id] = (amount + t.amount, cost + t.total_cost)
        elif amount > EPS:
            qty = min(t.amount, amount)
            basis = cost * qty / amount
            pos[t.coin_id] = (amount - qty, cost - basis)
            if y == year:
                disposals.append(Disposal(t.ts, t.symbol, qty, t.total_cost, basis))
        if y == year:
            in_year.append(t)
    return disposals, in_year


def report_csv(disposals: list[Disposal], fiat: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["date", "symbol", "amount", f"proceeds_{fiat}", f"cost_basis_{fiat}", f"gain_{fiat}"])
    for d in disposals:
        w.writerow([datetime.fromtimestamp(d.ts, UTC).strftime("%Y-%m-%d"), d.symbol, repr(d.amount),
                    f"{d.proceeds:.2f}", f"{d.cost_basis:.2f}", f"{d.gain:.2f}"])
    w.writerow(["TOTAL", "", "", f"{sum(d.proceeds for d in disposals):.2f}",
                f"{sum(d.cost_basis for d in disposals):.2f}", f"{sum(d.gain for d in disposals):.2f}"])
    return buf.getvalue()
