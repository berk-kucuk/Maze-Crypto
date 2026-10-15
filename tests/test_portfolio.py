import pytest

from mazecrypto.core.models import Trade
from mazecrypto.core.portfolio import (
    OTHER,
    bucket_weights,
    build_positions,
    drift,
    split_contribution,
    summarize,
    validate_targets,
)


def buy(ts, coin, amount, cost, fee=0.0, usd_rate=40.0, fiat="TRY"):
    return Trade(ts, coin, coin.upper()[:4], amount, cost, fiat, fee, "buy", usd_rate=usd_rate)


def sell(ts, coin, amount, cost, fee=0.0, usd_rate=40.0, fiat="TRY"):
    return Trade(ts, coin, coin.upper()[:4], amount, cost, fiat, fee, "sell", usd_rate=usd_rate)


def test_average_cost_includes_fees():
    pos = build_positions([buy(1, "bitcoin", 0.01, 1000, fee=10), buy(2, "bitcoin", 0.01, 2000, fee=10)], "TRY")
    p = pos["bitcoin"]
    assert p.amount == pytest.approx(0.02)
    assert p.cost_fiat == pytest.approx(3020)
    assert p.avg_cost_fiat == pytest.approx(151000)
    assert p.fees_fiat == pytest.approx(20)


def test_usd_basis_is_historical():
    # Same TRY spent at two different exchange rates -> different USD basis.
    pos = build_positions([buy(1, "eth", 1, 4000, usd_rate=40), buy(2, "eth", 1, 4000, usd_rate=50)], "TRY")
    assert pos["eth"].cost_usd == pytest.approx(100 + 80)


def test_sell_books_realised_and_keeps_average():
    pos = build_positions([buy(1, "sol", 10, 1000), sell(2, "sol", 4, 600)], "TRY")
    p = pos["sol"]
    assert p.amount == pytest.approx(6)
    assert p.avg_cost_fiat == pytest.approx(100)        # unchanged by a sell
    assert p.realized_fiat == pytest.approx(600 - 400)


def test_sell_everything_resets():
    pos = build_positions([buy(1, "sol", 2, 100), sell(2, "sol", 2, 150)], "TRY")
    assert pos["sol"].amount == 0 and pos["sol"].cost_fiat == 0
    assert pos["sol"].realized_fiat == pytest.approx(50)


def test_sell_without_position_raises():
    with pytest.raises(ValueError):
        build_positions([sell(1, "sol", 1, 10)], "TRY")
    with pytest.raises(ValueError):
        build_positions([buy(1, "sol", 1, 10), sell(2, "sol", 1.5, 10)], "TRY")


def test_missing_usd_rate_makes_usd_basis_unknown():
    pos = build_positions([buy(1, "btc", 1, 10, usd_rate=None)], "TRY")
    assert pos["btc"].cost_usd is None and pos["btc"].avg_cost_usd is None


def test_foreign_currency_trade_goes_through_usd():
    t = buy(1, "btc", 1, 100, fiat="USD", usd_rate=1.0)
    pos = build_positions([t], "TRY", base_per_usd=40.0)
    assert pos["btc"].cost_fiat == pytest.approx(4000)
    with pytest.raises(ValueError):
        build_positions([buy(1, "btc", 1, 100, fiat="EUR", usd_rate=None)], "TRY", 40.0)


def test_summary_pnl_and_weights():
    pos = build_positions([buy(1, "bitcoin", 1, 100, usd_rate=10), buy(1, "ethereum", 10, 100, usd_rate=10)], "TRY")
    s = summarize(pos, {"bitcoin": 150, "ethereum": 5}, {"bitcoin": 15, "ethereum": 0.5})
    assert s.value_fiat == pytest.approx(200)
    assert s.cost_fiat == pytest.approx(200)
    assert s.pnl_fiat == pytest.approx(0)
    assert s.weights == {"bitcoin": pytest.approx(75), "ethereum": pytest.approx(25)}
    btc = next(h for h in s.holdings if h.position.coin_id == "bitcoin")
    assert btc.pnl_pct == pytest.approx(50)
    assert btc.below_cost_pct == pytest.approx(-50)
    assert s.pnl_usd == pytest.approx(0)
    assert s.holdings[0].position.coin_id == "bitcoin"


def test_targets_validation():
    assert validate_targets({"a": 50, "b": 0}) == {"a": 50.0}
    with pytest.raises(ValueError):
        validate_targets({"a": 60, "b": 50})


def test_drift_and_other_bucket():
    targets = {"bitcoin": 50.0, "tether": 10.0, OTHER: 40.0}
    weights = {"bitcoin": 60.0, "pepe": 30.0, "dogecoin": 10.0}
    assert bucket_weights(weights, targets) == {"bitcoin": 60.0, "tether": 0.0, OTHER: 40.0}
    rows = {d.bucket: d for d in drift(weights, targets, 1000.0)}
    assert rows["bitcoin"].diff_pct == pytest.approx(10)
    assert rows["bitcoin"].rebalance_fiat == pytest.approx(-100)
    assert rows["tether"].rebalance_fiat == pytest.approx(100)
    assert rows[OTHER].diff_pct == pytest.approx(0)


def test_split_contribution_fills_underweight_first():
    targets = {"bitcoin": 50.0, "ethereum": 50.0}
    split = split_contribution(100, {"bitcoin": 300, "ethereum": 100}, targets)
    assert split == {"ethereum": pytest.approx(100)}
    split = split_contribution(100, {"bitcoin": 100, "ethereum": 100}, targets)
    assert split == {"bitcoin": pytest.approx(50), "ethereum": pytest.approx(50)}
    assert sum(split_contribution(250, {"bitcoin": 50}, targets).values()) == pytest.approx(250)
    assert split_contribution(0, {}, targets) == {}
