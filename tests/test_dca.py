from datetime import date

import pytest

from mazecrypto.core.dca import (
    DcaExtra,
    DcaPlan,
    Frequency,
    add_months,
    current,
    due_date,
    progress,
    schedule,
)


def plan(**kw):
    base = dict(name="p", start=date(2026, 10, 5), frequency=Frequency.WEEKLY, periods=5, amount=4500.0,
                fiat="TRY", allocation={"bitcoin": 50.0, "ethereum": 50.0},
                symbols={"bitcoin": "BTC", "ethereum": "ETH"},
                extras=[DcaExtra(0, "tether", "USDT", 2500.0)])
    base.update(kw)
    return DcaPlan(**base)


def test_schedule_with_extra_in_first_period():
    s = schedule(plan())
    assert len(s) == 5
    assert s[0].total == pytest.approx(7000)
    assert {i.symbol for i in s[0].items} == {"BTC", "ETH", "USDT"}
    assert s[1].total == pytest.approx(4500)
    assert s[4].due == date(2026, 11, 2)


def test_frequencies():
    assert due_date(plan(frequency=Frequency.BIWEEKLY), 2) == date(2026, 11, 2)
    assert due_date(plan(start=date(2026, 1, 31), frequency=Frequency.MONTHLY), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)


def test_validation():
    plan().validate()
    with pytest.raises(ValueError):
        plan(allocation={"bitcoin": 60.0}).validate()
    with pytest.raises(ValueError):
        plan(extras=[DcaExtra(9, "tether", "USDT", 1)]).validate()
    plan(allocation={}, follow_targets=True).validate()


def test_progress_and_current_card():
    s = schedule(plan())
    assert current(s, set(), date(2026, 10, 1)) == (s[0], "upcoming")
    assert current(s, set(), date(2026, 10, 7)) == (s[0], "due")
    assert current(s, set(), date(2026, 10, 13)) == (s[0], "overdue")
    assert current(s, {0}, date(2026, 10, 13)) == (s[1], "due")
    assert current(s, {0, 1, 2, 3, 4}, date(2027, 1, 1)) is None
    p = progress(s, {0, 1})
    assert (p.done, p.total) == (2, 5)
    assert p.invested == pytest.approx(11500)
    assert p.remaining == pytest.approx(25000 - 11500 + 2500 - 2500)


def test_follow_targets_uses_portfolio():
    p = plan(allocation={}, follow_targets=True, extras=[])
    s = schedule(p, values={"bitcoin": 10000.0, "ethereum": 0.0}, targets={"bitcoin": 50.0, "ethereum": 50.0})
    assert [(i.coin_id, round(i.amount)) for i in s[0].items] == [("ethereum", 4500)]


def test_roundtrip():
    p = plan()
    assert DcaPlan.from_dict(p.to_dict()) == p
