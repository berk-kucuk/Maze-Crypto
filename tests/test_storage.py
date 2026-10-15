import math
from datetime import date

import pytest

from mazecrypto.core import settings as cfg
from mazecrypto.core import validate as v
from mazecrypto.core.dca import DcaPlan, Frequency
from mazecrypto.core.models import Coin, Trade
from mazecrypto.core.rules import DriftCondition, Rule, RuleState
from mazecrypto.core.store import Cache, ProfileStore


def test_validate_numbers():
    assert v.number("1.5") == 1.5
    for bad in (math.nan, math.inf, "abc", None, True, [1]):
        with pytest.raises(v.ValidationError):
            v.number(bad)
    with pytest.raises(v.ValidationError):
        v.number(-1)
    with pytest.raises(v.ValidationError):
        v.price(0)
    assert v.optional_number("x") is None
    assert v.number(-2, allow_negative=True) == -2


def test_validate_candle_and_ids():
    c = v.candle(1_700_000_000_000, "1", "2", "0.5", "1.5", "10")
    assert c.ts == 1_700_000_000
    with pytest.raises(v.ValidationError):
        v.candle(1, 1, 0.9, 0.5, 1, 1)          # high below open
    with pytest.raises(v.ValidationError):
        v.symbol("btc/usd")
    with pytest.raises(v.ValidationError):
        v.coin_id("../etc")
    assert v.coin_id("avalanche-2") == "avalanche-2"


def test_trades_crud(tmp_path):
    s = ProfileStore(tmp_path / "p.db")
    tid = s.add_trade(Trade(1, "bitcoin", "btc", 0.1, 1000, "try", 5, "buy", "X", "why", 40.0))
    assert s.trades()[0].symbol == "BTC" and s.trades()[0].fiat == "TRY"
    t = s.trades()[0]
    t.amount = 0.2
    s.update_trade(t)
    assert s.trades()[0].amount == 0.2
    with pytest.raises(v.ValidationError):
        s.add_trade(Trade(1, "bitcoin", "BTC", -1, 1000, "TRY"))
    with pytest.raises(v.ValidationError):
        s.add_trade(Trade(1, "bitcoin", "BTC", 1, math.nan, "TRY"))
    s.delete_trade(tid)
    assert s.trades() == []


def test_profile_kv(tmp_path):
    s = ProfileStore(tmp_path / "p.db")
    s.set_targets({"bitcoin": 50.0})
    assert s.targets() == {"bitcoin": 50.0}
    rules = [Rule("d", DriftCondition(5))]
    s.set_rules(rules)
    assert s.rules()[0].to_dict() == rules[0].to_dict()
    s.set_rule_state({"a": RuleState(True, 5.0)})
    assert s.rule_state()["a"].active is True
    p = DcaPlan("x", date(2026, 1, 1), Frequency.WEEKLY, 3, 10, "EUR", {"bitcoin": 100.0})
    s.set_dca_plan(p)
    assert s.dca_plan() == p
    s.set_dca_done({2, 0})
    assert s.dca_done() == {0, 2}
    s.set_levels("bitcoin", [3.0, 1.0])
    assert s.levels("bitcoin") == [1.0, 3.0]
    s.put("rules", [{"bad": 1}])
    assert s.rules() == []


def test_cache_fresh_and_stale(tmp_path):
    c = Cache(tmp_path / "c.db")
    c.put("k", {"a": 1}, ts=100.0)
    assert c.fresh("k", ttl=10, now=105) == {"a": 1}
    assert c.fresh("k", ttl=10, now=200) is None
    assert c.stale("k") == (100.0, {"a": 1})
    assert c.stale("missing") is None


def test_settings_roundtrip(tmp_path):
    s = cfg.Settings(language="tr", fiat="try", secondary_fiat="usd", exchanges=["btcturk", "binance"],
                     watchlist=[Coin("bitcoin", "BTC", 'Bit"coin'), Coin("bitcoin", "BTC", "dup")])
    path = tmp_path / "config.toml"
    cfg.save(s, path)
    back = cfg.load(path)
    assert back.fiat == "TRY" and back.secondary_fiat == "USD"
    assert [c.name for c in back.watchlist] == ['Bit"coin']
    assert back.exchanges == ["btcturk", "binance"]


def test_settings_broken_file_is_set_aside(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("fiat = [[[")
    assert cfg.load(path).fiat == "USD"
    assert (tmp_path / "config.toml.broken").exists()


def test_settings_reject_bad_proxy():
    s = cfg.Settings()
    s.proxy.url = "ftp://x"
    with pytest.raises(ValueError):
        s.validate()


def test_settings_accent_roundtrip_and_fallback(tmp_path):
    s = cfg.Settings(accent="violet")
    cfg.save(s, tmp_path / "c.toml")
    assert cfg.load(tmp_path / "c.toml").accent == "violet"
    bad = cfg.Settings(accent="neon-blue")
    bad.validate()
    assert bad.accent == "champagne" and cfg.Settings().accent == "champagne"
