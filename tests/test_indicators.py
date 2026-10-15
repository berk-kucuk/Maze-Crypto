import math

import numpy as np
import pytest

from mazecrypto.core import indicators as ind


def test_sma_basic():
    out = ind.sma([1, 2, 3, 4, 5], 3)
    assert np.isnan(out[:2]).all()
    assert out[2:].tolist() == [2.0, 3.0, 4.0]


def test_sma_too_short():
    assert np.isnan(ind.sma([1, 2], 5)).all()


def test_ema_seeded_with_sma():
    out = ind.ema([1, 2, 3, 4, 5, 6], 3)
    assert out[2] == pytest.approx(2.0)
    # k = 0.5 -> 4*0.5 + 2*0.5 = 3
    assert out[3] == pytest.approx(3.0)
    assert out[5] == pytest.approx(5.0)


def test_rsi_extremes():
    up = list(range(1, 40))
    assert ind.rsi(up)[-1] == pytest.approx(100.0)
    down = list(range(40, 1, -1))
    assert ind.rsi(down)[-1] == pytest.approx(0.0)
    flat = [5.0] * 30
    assert ind.rsi(flat)[-1] == pytest.approx(50.0)


def test_rsi_known_value():
    # Wilder example from StockCharts. Their 70.53 comes from unrounded closes;
    # with the two-decimal closes printed in the table the result is 70.46.
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
              45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
    out = ind.rsi(closes, 14)
    assert out[14] == pytest.approx(70.46, abs=0.01)
    assert np.isnan(out[13])


def test_macd_shapes_and_sign():
    closes = np.linspace(100, 200, 80)
    m = ind.macd(closes)
    assert len(m.macd) == len(m.signal) == len(m.hist) == 80
    assert ind.last(m.macd) > 0          # rising market -> fast EMA above slow
    assert np.isnan(m.signal[:33]).all()
    assert not math.isnan(m.signal[33])


def test_correlation_of_returns():
    a = [100, 102, 101, 105, 107, 106, 110]
    assert ind.correlation(a, a) == pytest.approx(1.0)
    b = [2 * x for x in a]
    assert ind.correlation(a, b) == pytest.approx(1.0)
    inv = [100]
    for r in ind.returns(a):
        inv.append(inv[-1] * (1 - r))
    assert ind.correlation(a, inv) == pytest.approx(-1.0, abs=1e-6)
    assert ind.correlation([1, 1, 1], [1, 2, 3]) is None
    assert ind.correlation([1, 2], [1, 2]) is None


def test_volatility_and_pct_change():
    assert ind.volatility([100, 100, 100]) == 0.0
    assert ind.volatility([100]) is None
    assert ind.pct_change([100, 110, 121], 2) == pytest.approx(21.0)
    assert ind.pct_change([100], 1) is None
