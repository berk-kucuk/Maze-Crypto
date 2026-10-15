import numpy as np
import pytest

from mazecrypto import i18n
from mazecrypto.core import presets
from mazecrypto.core.insights import correlation_matrix, max_drawdown, tech_state


def test_correlation_matrix_is_symmetric():
    a = [1, 2, 3, 2, 4, 5]
    keys, m = correlation_matrix({"a": a, "b": [x * 3 for x in a], "short": [1]})
    assert keys == ["a", "b"]
    assert m[0, 1] == pytest.approx(1.0) and m[1, 0] == m[0, 1]
    assert np.allclose(np.diag(m), 1.0)


def test_tech_state():
    s = tech_state(list(np.geomspace(100, 300, 250)))
    assert s.trend == "above_both" and s.macd_state == "bullish" and s.rsi_state == "overbought"
    assert tech_state([1, 2]).rsi is None


def test_max_drawdown():
    assert max_drawdown([100, 120, 60, 90]) == pytest.approx(-50)


def test_i18n_fallback_and_templates():
    i18n.set_language("tr")
    rules = presets.rule_templates()
    assert "maliyetin" in rules["dip_reserve"][0].name
    assert i18n.t("no.such.key") == "no.such.key"
    i18n.set_language("en")
    assert "below cost" in presets.rule_templates()["dip_reserve"][0].name
