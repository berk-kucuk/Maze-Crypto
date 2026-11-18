from datetime import UTC, datetime

import pytest

from mazecrypto.core import export
from mazecrypto.core.models import Trade
from mazecrypto.ui import fmt


def ts(y, m, d):
    return int(datetime(y, m, d, tzinfo=UTC).timestamp())


TRADES = [
    Trade(ts(2025, 1, 5), "bitcoin", "BTC", 0.02, 2000, "TRY", 20, "buy", "BtcTurk", "=HYPERLINK(evil)", 40.0),
    Trade(ts(2026, 3, 1), "bitcoin", "BTC", 0.01, 3000, "TRY", 10, "sell", "BtcTurk", "", 45.0),
    Trade(ts(2026, 4, 1), "ethereum", "ETH", 1, 100, "USD", 0, "buy"),
]


def test_csv_roundtrip_and_formula_guard():
    text = export.to_csv(TRADES)
    assert "'=HYPERLINK" in text
    back = export.from_csv(text)
    assert [(t.symbol, t.amount, t.side, t.note) for t in back] == \
        [(t.symbol, t.amount, t.side, t.note) for t in TRADES]
    assert back[2].usd_rate is None


def test_csv_errors_name_the_line():
    with pytest.raises(ValueError, match="missing columns"):
        export.from_csv("a,b\n1,2\n")
    bad = export.to_csv(TRADES[:1]).replace("0.02", "-1")
    with pytest.raises(ValueError, match="line 2"):
        export.from_csv(bad)


def test_json_roundtrip():
    back = export.from_json(export.to_json(TRADES))
    assert len(back) == 3 and back[0].note == "=HYPERLINK(evil)"
    with pytest.raises(ValueError):
        export.from_json('{"format": "other"}')


def test_yearly_report_average_cost():
    disposals, year_trades = export.yearly_report(TRADES, 2026, "TRY")
    assert len(year_trades) == 1
    (d,) = disposals
    assert d.cost_basis == pytest.approx(1010)            # half of 2020
    assert d.proceeds == pytest.approx(2990)               # 3000 - fee
    assert d.gain == pytest.approx(1980)
    assert "TOTAL" in export.report_csv(disposals, "TRY")
    assert export.yearly_report(TRADES, 2025, "TRY")[0] == []


def test_number_parsing_and_formatting():
    assert fmt.parse_number("1.234,56") == 1234.56
    assert fmt.parse_number("1,234.56") == 1234.56
    assert fmt.parse_number("4500") == 4500
    assert fmt.parse_number("0,5") == 0.5
    with pytest.raises(ValueError):
        fmt.parse_number("abc")
    from mazecrypto import i18n
    i18n.set_language("tr")
    assert fmt.pct(-1.5) == "\u2212%1,50" and fmt.money(4045809, "TRY") == "₺4.045.809,00"
    i18n.set_language("en")
    assert fmt.pct(2) == "+2.00%" and fmt.money(-3.5, "USD") == "\u2212$3.500"
    assert fmt.num(0.000123456) == "0.0001235"


def test_tax_year_is_the_local_calendar_year(monkeypatch):
    """CR4: 00:30 on 1 January in Istanbul is 21:30 on 31 December in UTC.
    The year picker lists it under the new year; the report must agree."""
    import time
    from datetime import datetime
    from mazecrypto.core.models import Trade
    monkeypatch.setenv("TZ", "Europe/Istanbul")
    time.tzset()
    try:
        ts = int(datetime(2027, 1, 1, 0, 30).timestamp())
        trades = [Trade(ts=ts - 86400 * 30, coin_id="bitcoin", symbol="BTC", side="buy",
                        amount=1, cost=100, fiat="TRY"),
                  Trade(ts=ts, coin_id="bitcoin", symbol="BTC", side="sell",
                        amount=1, cost=150, fiat="TRY")]
        disposals, _ = export.yearly_report(trades, 2027, "TRY")
        assert len(disposals) == 1
        assert export.yearly_report(trades, 2026, "TRY")[0] == []
        assert "2027-01-01" in export.report_csv(disposals, "TRY")
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
