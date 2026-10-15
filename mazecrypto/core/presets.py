"""Starting points the user can pick from. Nothing here is applied silently."""
from __future__ import annotations

from datetime import date

from mazecrypto.core.dca import DcaPlan, Frequency
from mazecrypto.core.models import Coin
from mazecrypto.core.portfolio import OTHER
from mazecrypto.core.rules import CostChangeCondition, DriftCondition, MoveCondition, RsiCondition, Rule
from mazecrypto.i18n import t

BTC = Coin("bitcoin", "BTC", "Bitcoin")
ETH = Coin("ethereum", "ETH", "Ethereum")
SOL = Coin("solana", "SOL", "Solana")
USDT = Coin("tether", "USDT", "Tether")

WATCHLISTS: dict[str, list[Coin]] = {
    "majors": [BTC, ETH, SOL, Coin("binancecoin", "BNB", "BNB"), Coin("ripple", "XRP", "XRP"),
               Coin("cardano", "ADA", "Cardano"), Coin("dogecoin", "DOGE", "Dogecoin"),
               Coin("chainlink", "LINK", "Chainlink"), Coin("avalanche-2", "AVAX", "Avalanche"), USDT],
    "minimal": [BTC, ETH, USDT],
    "empty": [],
}

FIATS = ("USD", "EUR", "TRY", "GBP", "CHF", "JPY", "CAD", "AUD", "PLN", "SEK", "NOK", "BRL", "INR", "KRW")


def rule_templates(coin: Coin = BTC, reserve: Coin = USDT) -> dict[str, list[Rule]]:
    """Rule bundles shown in the rule editor. The user adjusts every number."""
    c, r = coin.symbol, reserve.symbol
    return {
        "dip_reserve": [
            Rule(t("tpl.cost_drop.name", coin=c, pct=15), CostChangeCondition(coin.id, c, -15.0),
                 t("tpl.dip_half.msg", reserve=r)),
            Rule(t("tpl.cost_drop.name", coin=c, pct=25), CostChangeCondition(coin.id, c, -25.0),
                 t("tpl.dip_rest.msg", reserve=r)),
        ],
        "rebalance": [Rule(t("tpl.drift.name", pct=5), DriftCondition(5.0), t("tpl.drift.msg"))],
        "oversold": [Rule(t("tpl.rsi.name", coin=c), RsiCondition(coin.id, c, "1d", "below", 30.0), "")],
        "big_move": [Rule(t("tpl.move.name", coin=c), MoveCondition(coin.id, c, 24, 10.0), "")],
    }


def target_template(name: str) -> dict[str, float]:
    return {
        "btc_heavy": {BTC.id: 60.0, ETH.id: 20.0, USDT.id: 10.0, OTHER: 10.0},
        "balanced": {BTC.id: 40.0, ETH.id: 25.0, SOL.id: 10.0, USDT.id: 10.0, OTHER: 15.0},
        "empty": {},
    }[name]


def dca_template(start: date, fiat: str, amount: float = 100.0, periods: int = 12) -> DcaPlan:
    return DcaPlan("DCA", start, Frequency.WEEKLY, periods, amount, fiat,
                   {BTC.id: 70.0, ETH.id: 30.0}, {BTC.id: BTC.symbol, ETH.id: ETH.symbol})
