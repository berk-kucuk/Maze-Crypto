from datetime import date

from mazecrypto.core import dca as dca_mod
from mazecrypto.core.advisor import AdvisorInput, tips
from mazecrypto.core.insights import TechState
from mazecrypto.core.models import Coin, CoinRow, Trade
from mazecrypto.core.portfolio import build_positions, summarize

BTC = Coin("bitcoin", "BTC", "Bitcoin")
ETH = Coin("ethereum", "ETH", "Ethereum")


def ids(ts):
    return [t.id for t in ts]


def test_empty_start_guides_the_user():
    out = tips(AdvisorInput("USD", today=date(2026, 1, 1)))
    assert ids(out)[:2] == ["start.watch", "start.trades"]
    assert out[-1].id.startswith("learn.")


def portfolio(btc_price=100.0, eth_price=10.0):
    trades = [Trade(1, "bitcoin", "BTC", 1, 100, "USD", usd_rate=1.0),
              Trade(1, "ethereum", "ETH", 10, 100, "USD", usd_rate=None)]
    pos = build_positions(trades, "USD")
    return summarize(pos, {"bitcoin": btc_price, "ethereum": eth_price},
                     {"bitcoin": btc_price, "ethereum": eth_price}), trades


def test_drift_tip_uses_the_users_own_targets_and_plan():
    s, trades = portfolio(btc_price=300.0)          # BTC 75 %, ETH 25 %
    rows = [CoinRow(BTC, 300, 300, change_24h=1.0), CoinRow(ETH, 10, 10, change_24h=-12.0)]
    x = AdvisorInput("USD", rows=rows, watchlist=["bitcoin", "ethereum"], summary=s, trades=2,
                     trades_without_rate=1, targets={"bitcoin": 50.0, "ethereum": 50.0}, plan_amount=100.0,
                     today=date(2026, 1, 1))
    out = {t.id: t for t in tips(x)}
    d = out["balance.drift"]
    assert d.body == "tip.drift_over.b" and d.params["coin"] == "BTC"
    assert d.extra == "tip.drift_split.b" and d.params["split"].startswith("ETH")   # new money goes to ETH
    assert "market.move.ethereum" in out and out["market.move.ethereum"].action == "chart:ethereum"
    assert "risk.concentration" in out and "data.no_rate" in out
    assert "start.trades" not in out and "start.plan" in out


def test_balanced_portfolio_gets_a_good_tip():
    s, _ = portfolio()                              # 50 / 50
    x = AdvisorInput("USD", summary=s, trades=2, watchlist=["bitcoin"], targets={"bitcoin": 50.0, "ethereum": 50.0},
                     today=date(2026, 1, 1))
    assert "balance.ok" in ids(tips(x))


def test_plan_due_and_below_cost_and_rsi():
    s, _ = portfolio(btc_price=80.0)                # BTC 20 % under cost
    plan = dca_mod.DcaPlan("p", date(2026, 1, 1), dca_mod.Frequency.WEEKLY, 4, 50, "USD", {"bitcoin": 100.0},
                           {"bitcoin": "BTC"})
    cur = dca_mod.current(dca_mod.schedule(plan), set(), date(2026, 1, 2))
    tech = {"bitcoin": TechState(75.0, "overbought", "", "", None)}
    x = AdvisorInput("USD", rows=[CoinRow(BTC, 80, 80)], watchlist=["bitcoin"], summary=s, trades=2,
                     current=cur, plan_exists=True, tech=tech, today=date(2026, 1, 2))
    out = {t.id: t for t in tips(x)}
    assert out["plan.due"].level == "action" and out["plan.due"].params["items"].startswith("BTC")
    assert "risk.below_cost.bitcoin" in out and "market.hot.bitcoin" in out
    assert ids(tips(x))[0] == "plan.due"            # the user's own plan comes first


def test_offline_comes_first():
    out = tips(AdvisorInput("USD", watchlist=["bitcoin"], trades=1, offline=True, today=date(2026, 1, 1)))
    assert out[0].id == "data.offline"
