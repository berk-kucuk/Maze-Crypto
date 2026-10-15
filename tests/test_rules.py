import numpy as np
import pytest

from mazecrypto.core.rules import (
    CostChangeCondition,
    DriftCondition,
    MoveCondition,
    PriceCondition,
    RsiCondition,
    Rule,
    RuleContext,
    SmaCrossCondition,
    condition_from_dict,
    data_needs,
    evaluate,
)

H = 3600


def ctx(**kw):
    return RuleContext(**kw)


def test_cost_change_drop_and_gain():
    c = CostChangeCondition("bitcoin", "BTC", -15)
    assert c.evaluate(ctx(prices_fiat={"bitcoin": 84}, avg_cost_fiat={"bitcoin": 100})).active is True
    assert c.evaluate(ctx(prices_fiat={"bitcoin": 90}, avg_cost_fiat={"bitcoin": 100})).active is False
    assert c.evaluate(ctx(prices_fiat={"bitcoin": 90})).active is None
    up = CostChangeCondition("bitcoin", "BTC", 50)
    assert up.evaluate(ctx(prices_fiat={"bitcoin": 151}, avg_cost_fiat={"bitcoin": 100})).active is True


def test_price_condition_currency():
    c = PriceCondition("bitcoin", "BTC", "above", 100_000, "usd")
    assert c.evaluate(ctx(prices_usd={"bitcoin": 100_001})).active is True
    assert c.evaluate(ctx(prices_fiat={"bitcoin": 10**7})).active is None


def test_drift_condition():
    c = DriftCondition(5.0)
    r = c.evaluate(ctx(weights={"bitcoin": 57.0, "ethereum": 43.0}, targets={"bitcoin": 50.0, "ethereum": 50.0}))
    assert r.active is True and abs(r.value) == pytest.approx(7)
    ok = c.evaluate(ctx(weights={"bitcoin": 52.0, "ethereum": 48.0}, targets={"bitcoin": 50.0, "ethereum": 50.0}))
    assert ok.active is False
    assert c.evaluate(ctx()).active is None


def test_indicator_conditions():
    falling = list(np.linspace(200, 100, 60))
    r = RsiCondition("bitcoin", "BTC", "1d", "below", 30)
    assert r.evaluate(ctx(closes={("bitcoin", "1d"): falling})).active is True
    rising = list(np.linspace(100, 300, 250))
    g = SmaCrossCondition("bitcoin", "BTC", "1d", 50, 200, "golden")
    assert g.evaluate(ctx(closes={("bitcoin", "1d"): rising})).active is True
    assert g.evaluate(ctx(closes={("bitcoin", "1d"): rising[:100]})).active is None
    m = MoveCondition("bitcoin", "BTC", 24, 10, "down")
    hourly = [100.0] * 30 + [85.0]
    assert m.evaluate(ctx(closes={("bitcoin", "1h"): hourly})).active is True
    assert data_needs([Rule("x", r), Rule("y", m)]) == {("bitcoin", "1d"), ("bitcoin", "1h")}


def test_engine_fires_on_edge_once_and_respects_cooldown():
    rule = Rule("dip", CostChangeCondition("bitcoin", "BTC", -15), cooldown_hours=24)
    state: dict = {}
    low = ctx(prices_fiat={"bitcoin": 80}, avg_cost_fiat={"bitcoin": 100})
    high = ctx(prices_fiat={"bitcoin": 95}, avg_cost_fiat={"bitcoin": 100})
    assert len(evaluate([rule], low, state, now=1000)) == 1
    assert evaluate([rule], low, state, now=1060) == []           # still true: no repeat
    assert evaluate([rule], high, state, now=1120) == []          # resets
    assert evaluate([rule], low, state, now=1180) == []           # re-armed but in cooldown
    assert evaluate([rule], high, state, now=2000) == []
    assert len(evaluate([rule], low, state, now=1000 + 25 * H)) == 1


def test_missing_data_keeps_state_and_disabled_rules_are_skipped():
    rule = Rule("dip", CostChangeCondition("bitcoin", "BTC", -15))
    state: dict = {}
    evaluate([rule], ctx(prices_fiat={"bitcoin": 80}, avg_cost_fiat={"bitcoin": 100}), state, now=0 + 30 * H)
    evaluate([rule], ctx(), state, now=31 * H)
    assert state[rule.id].active is True
    rule.enabled = False
    assert evaluate([rule], ctx(prices_fiat={"bitcoin": 1}, avg_cost_fiat={"bitcoin": 100}), state, 99 * H) == []


def test_rule_roundtrip_and_bad_kind():
    rule = Rule("x", RsiCondition("solana", "SOL", "4h", "above", 70), "note", cooldown_hours=6)
    back = Rule.from_dict(rule.to_dict())
    assert back.to_dict() == rule.to_dict()
    with pytest.raises(ValueError):
        condition_from_dict({"kind": "exec", "cmd": "rm"})
    # Unknown fields are ignored, wrong types are coerced.
    c = condition_from_dict({"kind": "drift", "pct": "7", "junk": 1})
    assert isinstance(c, DriftCondition) and c.pct == 7.0
