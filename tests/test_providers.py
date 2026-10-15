"""Providers and MarketService against a fake network (httpx.MockTransport)."""
import json

import httpx
import pytest

from mazecrypto.core.models import Coin, Interval
from mazecrypto.core.settings import Settings
from mazecrypto.core.store import Cache
from mazecrypto.providers import fx
from mazecrypto.providers.exchanges import EXCHANGES
from mazecrypto.providers.http import HostNotAllowed, Http, NetError
from mazecrypto.providers.service import MarketService, allowed_hosts

BTC = Coin("bitcoin", "BTC", "Bitcoin")
XMR = Coin("monero", "XMR", "Monero")
USDT = Coin("tether", "USDT", "Tether")

ECB = """<?xml version="1.0"?><gesmes:Envelope xmlns:gesmes="g" xmlns="e"><Cube><Cube time="2026-10-14">
<Cube currency="USD" rate="1.25"/><Cube currency="TRY" rate="50"/><Cube currency="JPY" rate="nan"/>
</Cube></Cube></gesmes:Envelope>"""
TCMB = """<?xml version="1.0"?><Tarih_Date><Currency CurrencyCode="USD"><Unit>1</Unit>
<ForexBuying>40.0</ForexBuying><ForexSelling>42.0</ForexSelling></Currency></Tarih_Date>"""

MARKETS = [
    {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 100000, "ath": 125000,
     "ath_change_percentage": -20, "price_change_percentage_24h_in_currency": 1.5, "market_cap": 2e12},
    {"id": "monero", "symbol": "xmr", "name": "Monero", "current_price": 500, "ath": 600},
    {"id": "tether", "symbol": "usdt", "name": "Tether", "current_price": 1.0},
]


def make_handler(down: set[str] | None = None, overrides: dict | None = None):
    down = down or set()
    overrides = overrides or {}
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        calls.append(f"{host}{path}")
        if host in down:
            raise httpx.ConnectError("offline", request=req)
        key = f"{host}{path}"
        if key in overrides:
            body = overrides[key]
            return httpx.Response(200, text=body) if isinstance(body, str) else httpx.Response(200, json=body)
        if path.endswith("/coins/markets"):
            return httpx.Response(200, json=MARKETS)
        if host == "www.tcmb.gov.tr":
            return httpx.Response(200, text=TCMB)
        if host == "www.ecb.europa.eu":
            return httpx.Response(200, text=ECB)
        if path == "/api/v2/ticker":
            return httpx.Response(200, json={"data": [
                {"numeratorSymbol": "BTC", "denominatorSymbol": "TRY", "last": 4_100_000},
                {"numeratorSymbol": "USDT", "denominatorSymbol": "TRY", "last": 41.2},
                {"numeratorSymbol": "BAD", "denominatorSymbol": "TRY", "last": "NaN"},
            ]})
        if path == "/api/v3/ticker/price":
            return httpx.Response(200, json=[
                {"symbol": "BTCUSDT", "price": "100100"},
                {"symbol": "XMRUSDT", "price": "118.7"},           # frozen delisted pair
                {"symbol": "ETHFDUSD", "price": "2500"},
                {"symbol": "BTCTRY", "price": "-5"},
            ])
        if path == "/api/v3/klines":
            sym = req.url.params["symbol"]
            base = 100000.0 if sym.startswith("BTC") else 118.7
            rows = [[(1_790_000_000 + i * 86400) * 1000, base, base * (1.03 if sym.startswith("BTC") else 1),
                     base * (0.99 if sym.startswith("BTC") else 1),
                     base * (1 + (i % 3) / 100 if sym.startswith("BTC") else 1), 1] for i in range(31)]
            return httpx.Response(200, json=rows)
        if path.endswith("/market_chart"):
            return httpx.Response(200, json={"prices": [[(1_790_000_000 + i * 86400) * 1000, 500 + (i % 4)]
                                                        for i in range(31)]})
        return httpx.Response(404)

    return handler, calls


def service(tmp_path, handler, **settings_kw):
    s = Settings(**settings_kw)
    http = Http(allowed_hosts(), transport=httpx.MockTransport(handler), retries=0)
    http.allowed = dict.fromkeys(http.allowed, 0.0)     # no spacing in tests
    return MarketService(s, Cache(tmp_path / "cache.db"), http)


async def test_allowlist_blocks_unknown_hosts():
    http = Http({"api.binance.com": 0}, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(HostNotAllowed):
        await http.get_json("https://evil.example.com/x")
    with pytest.raises(HostNotAllowed):
        await http.get_json("http://api.binance.com/plain-http")
    assert await http.get_json("https://api.binance.com/ok") == {}


async def test_http_errors_and_bad_json():
    http = Http({"a.test": 0}, retries=1, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, text="not json") if r.url.path == "/j" else httpx.Response(503)))
    with pytest.raises(NetError):
        await http.get_json("https://a.test/j")
    with pytest.raises(NetError):
        await http.get_json("https://a.test/down")


def test_fx_parsers():
    rates = fx.parse_ecb(ECB)
    assert rates["TRY"] == pytest.approx(40.0) and rates["EUR"] == pytest.approx(0.8)
    assert "JPY" not in rates
    assert fx.parse_tcmb(TCMB)["TRY"] == pytest.approx(41.0)
    with pytest.raises(ValueError):
        fx.parse_ecb('<!DOCTYPE x [<!ENTITY a "b">]><x/>')


async def test_binance_parser_skips_invalid():
    handler, _ = make_handler()
    http = Http(allowed_hosts(), transport=httpx.MockTransport(handler))
    t = await EXCHANGES["binance"]().tickers(http)
    assert t[("BTC", "USDT")] == 100100 and t[("ETH", "FDUSD")] == 2500
    assert ("BTC", "TRY") not in t


async def test_refresh_fallbacks_and_sanity(tmp_path):
    handler, _ = make_handler()
    svc = service(tmp_path, handler, fiat="TRY", exchanges=["btcturk", "binance"])
    snap = await svc.refresh([BTC, XMR, USDT])
    btc, xmr, usdt = snap.rows
    assert snap.fx["TRY"] == pytest.approx(41.0)          # TCMB preferred for TRY
    assert (btc.price_fiat, btc.fiat_source) == (4_100_000, "BtcTurk")
    assert (btc.price_usd, btc.usd_source) == (100100, "Binance")
    assert btc.ath_change_pct == pytest.approx((100100 / 125000 - 1) * 100)
    # Binance's frozen XMRUSDT is 76 % off CoinGecko -> rejected
    assert (xmr.price_usd, xmr.usd_source) == (500, "CoinGecko")
    assert xmr.price_fiat == pytest.approx(500 * 41.0) and "FX" in xmr.fiat_source
    assert usdt.price_fiat == 41.2
    assert not snap.offline and snap.stale_age is None

    await svc.enrich(snap)
    assert btc.corr_btc_30d == 1.0
    assert xmr.corr_btc_30d is not None                   # came from CoinGecko, not the frozen pair
    assert usdt.corr_btc_30d is None and usdt.spark == []
    await svc.close()


async def test_exchange_down_falls_back_to_coingecko(tmp_path):
    handler, _ = make_handler(down={"api.btcturk.com"})
    svc = service(tmp_path, handler, fiat="TRY", exchanges=["btcturk"])
    snap = await svc.refresh([BTC])
    assert snap.rows[0].price_fiat == pytest.approx(100000 * 41.0)
    assert snap.errors
    await svc.close()


async def test_offline_uses_stale_cache_then_survives_empty_cache(tmp_path):
    handler, _ = make_handler()
    svc = service(tmp_path, handler, fiat="EUR", exchanges=["binance"])
    first = await svc.refresh([BTC])
    await svc.close()

    everything = set(allowed_hosts())
    off, _ = make_handler(down=everything)
    svc = service(tmp_path, off, fiat="EUR", exchanges=["binance"])
    svc.cache.con.execute("UPDATE cache SET ts = ts - 7200")   # make every entry expire
    snap = await svc.refresh([BTC])
    assert snap.offline and snap.stale_age and snap.stale_age >= 7200
    assert snap.rows[0].price_usd == first.rows[0].price_usd and snap.rows[0].stale
    await svc.close()

    svc = service(tmp_path / "fresh", off, fiat="EUR", exchanges=["binance"])
    snap = await svc.refresh([BTC])                            # nothing cached, nothing online
    assert snap.offline and snap.rows[0].price_usd is None
    await svc.close()


async def test_candles_fallback_to_usd_times_fx(tmp_path):
    handler, _ = make_handler()
    svc = service(tmp_path, handler, fiat="TRY", exchanges=["binance"])
    await svc.refresh([BTC])
    cs = await svc.candles(BTC, "fiat", Interval.D1, limit=31, ref_usd=100100)
    # Binance has no valid BTCTRY ticker here -> USD candles converted with the rate
    assert cs.converted and cs.quote == "TRY" and cs.source == "Binance"
    assert cs.candles[-1].close == pytest.approx(100000 * 41.0)
    await svc.close()


async def test_kraken_and_btcturk_parsers(tmp_path):
    handler, _ = make_handler(overrides={
        "api.kraken.com/0/public/AssetPairs": {"error": [], "result": {
            "XXBTZEUR": {"wsname": "XBT/EUR"}, "BAD": {"wsname": ""}}},
        "api.kraken.com/0/public/Ticker": {"error": [], "result": {
            "XXBTZEUR": {"c": ["90000.1", "1"]}, "UNKNOWN": {"c": ["1", "1"]}}},
        "api.kraken.com/0/public/OHLC": {"error": [], "result": {"XXBTZEUR": [
            [1790000000, "1", "2", "0.5", "1.5", "1.2", "10", 3], [1790003600, "1.5", "2", "1", "1.8", "1.2", "5", 1]],
            "last": 1}},
        "graph-api.btcturk.com/v1/klines/history": {"s": "ok", "t": [1, 2], "o": [1, 2], "h": [2, 3],
                                                    "l": [1, 2], "c": [2, 3], "v": [1]},
    })
    http = Http(allowed_hosts(), transport=httpx.MockTransport(handler))
    k = EXCHANGES["kraken"]()
    assert await k.tickers(http) == {("BTC", "EUR"): 90000.1}
    cs = await k.klines(http, "BTC", "EUR", Interval.H1, 10)
    assert [c.close for c in cs] == [1.5, 1.8]
    with pytest.raises(ValueError):           # ragged columns are rejected
        await EXCHANGES["btcturk"]().klines(http, "BTC", "TRY", Interval.H1, 10)


def test_mock_payloads_are_json_serialisable():
    json.dumps(MARKETS)


async def test_cache_only_snapshot_makes_no_requests(tmp_path):
    handler, calls = make_handler()
    svc = service(tmp_path, handler, fiat="EUR", exchanges=["binance"])
    await svc.refresh([BTC])
    n = len(calls)
    snap = await svc.refresh([BTC], cache_only=True)
    assert len(calls) == n and snap.rows[0].price_usd == 100100
    await svc.close()


async def test_unreachable_host_fails_fast(tmp_path):
    handler, calls = make_handler(down={"api.binance.com"})
    http = Http({"api.binance.com": 0}, transport=httpx.MockTransport(handler), retries=2)
    with pytest.raises(NetError):
        await http.get_json("https://api.binance.com/a")
    with pytest.raises(NetError, match="unreachable"):
        await http.get_json("https://api.binance.com/b")
    assert len(calls) == 1                        # no retries, second call never sent
