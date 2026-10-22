"""Wallet addresses: validation, secret detection, portfolio merge, providers with fallbacks."""

import json

import httpx
import pytest

from mazecrypto.core import address as addr
from mazecrypto.core.models import Trade
from mazecrypto.core.portfolio import build_positions, summarize
from mazecrypto.core.store import Cache
from mazecrypto.core.wallet import Leg, Transfer, Wallet, merge_activity, merge_wallets
from mazecrypto.providers.http import Http
from mazecrypto.providers.service import allowed_hosts
from mazecrypto.providers.wallets import WalletService


def ok(s: str) -> addr.Ok:
    r = addr.parse(s)
    assert isinstance(r, addr.Ok), (s, r)
    return r


def test_keccak():
    assert addr.keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert addr.keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"
    assert len(addr.keccak256(bytes(range(256)) * 2)) == 32  # more than one 136-byte block


def test_bitcoin_and_litecoin():
    assert ok("bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh").chains == ("bitcoin",)
    assert ok("BC1QXY2KGDYGJRSQTZQ2N0YRF2493P83KKFJHX0WLH").address == "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
    assert ok("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa").chains == ("bitcoin",)
    assert ok("3KMmeqPeQcngyTehdfSwsGqvxfU7J7qtc8").chains == ("bitcoin", "litecoin")
    assert ok("bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0").chains == ("bitcoin",)  # taproot
    assert ok("bitcoin:bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh?amount=0.1").address.startswith("bc1q")
    assert ok("LcNS6c8RddAMjewDrUAAi8BzecKoosnkN3").chains == ("litecoin",)
    assert addr.parse("bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlj") == addr.Bad("checksum")
    assert addr.parse("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb") == addr.Bad("checksum")
    assert addr.parse("tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx") == addr.Bad("testnet")
    assert addr.parse("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh") == addr.Bad("checksum")  # v0 in bech32m


def test_evm_solana_tron_monero():
    r = ok("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed")
    assert r.address == "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed" and "hyperliquid" in r.chains
    assert addr.parse("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeD") == addr.Bad("checksum")
    assert ok("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM").chains == ("solana",)
    assert isinstance(addr.parse("9WzDXwBbmkg8ZTbNMqUxvQRAyrZz"), addr.Bad)
    assert ok("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t").chains == ("tron",)
    assert addr.tron_from_hex("41a614f803b6fd780986a42c78ec9c7f77e6ded13c") == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    xmr = "888tNkZrPN6JsEgekjMnABU4TBzc2Dt29EPAvkRxbANsAnjyPbb3iQ1YBRk1UXcdRsiKc9dhwMVgN5S9cQUiyoogDavup3H"
    assert ok(xmr).chains == ("monero",)
    assert addr.parse(xmr[:-1] + "J") == addr.Bad("checksum")


def test_secrets_are_refused():
    seed = "abandon " * 11 + "about"
    assert addr.parse(seed) == addr.Secret("seed")
    assert addr.parse(seed.replace(" ", "\n")) == addr.Secret("seed")
    key = "4c0883a69102937d6231471b5dbb6204fe5129617082792ae468d01a3f362318"
    assert addr.parse(key) == addr.parse("0x" + key) == addr.Secret("private_key")
    assert addr.parse("5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ") == addr.Secret("private_key")
    assert addr.parse("KwdMAjGmerYanjeui5SHS7JkmpZvVipYvB2LJGU1ZxJwYvP98617") == addr.Secret("private_key")
    assert addr.parse(
        "xprv9s21ZrQH143K3QTDL4LXw2F7HEK3wJUD2nW2nRk4stbPy6cq3jPPqjiChkVvvNKmPGJxWUtg6LnF5kejMRNNU3TGtRBeJgk33yuGBxrMPHi"
    ) == addr.Secret("xprv")
    assert addr.parse("[" + ",".join(map(str, range(1, 65))) + "]") == addr.Secret("private_key")
    assert addr.parse(
        "xpub661MyMwAqRbcFtXgS5sYJABqqG9YLmC4Q1Rdap9gSE8NqtwybGhePY2gZ29ESFjqJoCu1Rupje8YtGqsefD265TMg7usUDFdp6W1EGMcet8"
    ).chains == ("bitcoin",)


def test_hd_accounts():
    from mazecrypto.core import hd

    # BIP-84 / BIP-86 test vectors ("abandon … about").
    z = hd.parse(
        "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"
    )
    assert hd.address(z, "bitcoin", "wpkh", 0, 0) == "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
    assert hd.address(z, "bitcoin", "wpkh", 1, 0) == "bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el"
    x = hd.parse(
        "xpub6BgBgsespWvERF3LHQu6CnqdvfEvtMcQjYrcRzx53QJjSxarj2afYWcLteoGVky7D3UKDP9QyrLprQ3VCECoY49yfdDEHGCtMMj92pReUsQ"
    )
    assert hd.address(x, "bitcoin", "tr", 0, 0) == "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr"
    assert hd._ripemd160(b"abc").hex() == "8eb208f7e05d987a9b044a8e98c6b087f15a0bfc"


def test_wallet_amounts_keep_trade_cost():
    trades = [
        Trade(1000, "bitcoin", "BTC", 1.0, 100.0, "USD", usd_rate=1.0, id=1),
        Trade(2000, "bitcoin", "BTC", 1.0, 300.0, "USD", usd_rate=1.0, id=2),
    ]
    pos = merge_wallets(build_positions(trades, "USD", 1.0), {"bitcoin": ("BTC", 0.5), "solana": ("SOL", 10.0)})
    assert pos["bitcoin"].amount == 0.5 and pos["bitcoin"].avg_cost_fiat == pytest.approx(200.0)
    assert not pos["solana"].cost_known and pos["solana"].avg_cost_fiat is None
    s = summarize(pos, {"bitcoin": 400.0, "solana": 20.0}, {"bitcoin": 400.0, "solana": 20.0})
    assert s.value_fiat == pytest.approx(400.0)
    assert s.pnl_fiat == pytest.approx(100.0)  # only BTC's cost is known: 0.5 × 400 − 100
    assert s.unknown_cost == ["SOL"]
    assert next(h for h in s.holdings if h.position.coin_id == "solana").pnl_fiat is None


def test_merge_activity():
    a = Transfer("ethereum", "0x1", 100, "ok", [Leg("ethereum", "ETH", -1.0)], 0.001, "ETH", wallet_ids={"a"})
    b = Transfer("ethereum", "0x1", 100, "ok", [Leg("ethereum", "ETH", 1.0)], wallet_ids={"b"})
    p = Transfer("bitcoin", "f00", 0, "pending", [Leg("bitcoin", "BTC", 0.1)], wallet_ids={"c"})
    m = merge_activity([[a], [b, p]])
    assert [x.status for x in m] == ["pending", "ok"]
    assert m[1].direction == "self" and m[1].wallet_ids == {"a", "b"} and m[1].fee == 0.001
    utxo = Transfer("bitcoin", "x", 5, "ok", [Leg("bitcoin", "BTC", -0.0001)], 0.0001, fee_included=True)
    assert utxo.direction == "self"


def _service(tmp_path, handler) -> WalletService:
    return WalletService(Http(allowed_hosts(), transport=httpx.MockTransport(handler)), Cache(tmp_path / "c.db"))


async def test_bitcoin_falls_back_to_the_next_explorer(tmp_path):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.host)
        if req.url.host == "blockstream.info":
            raise httpx.ConnectError("blocked", request=req)
        return httpx.Response(
            200,
            json={
                "chain_stats": {"funded_txo_sum": 150_000_000, "spent_txo_sum": 50_000_000, "tx_count": 3},
                "mempool_stats": {},
            },
        )

    svc = _service(tmp_path, handler)
    w = Wallet("1", "bitcoin", "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh")
    f = await svc.balance(w, ttl=0)
    assert f.value.assets[0].amount == pytest.approx(1.0) and f.error is None
    assert seen == ["blockstream.info", "mempool.space"]


async def test_evm_rpc_rejects_the_wrong_chain_and_uses_the_next(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        batch = json.loads(req.content)
        chain_id = "0x1" if req.url.host == "base-rpc.publicnode.com" else "0x2105"  # first node answers for mainnet
        out = []
        for c in batch:
            res = (
                chain_id
                if c["method"] == "eth_chainId"
                else "0xde0b6b3a7640000"
                if c["method"] == "eth_getBalance"
                else "0x" + "0" * 63 + "1"
            )
            out.append({"jsonrpc": "2.0", "id": c["id"], "result": res})
        return httpx.Response(200, json=out)

    svc = _service(tmp_path, handler)
    f = await svc.balance(Wallet("1", "base", "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"), ttl=0)
    eth = next(a for a in f.value.assets if a.coin_id == "ethereum")
    assert eth.amount == pytest.approx(1.0)


async def test_nothing_answers_keeps_the_last_balance(tmp_path):
    up = {"on": True}

    def handler(req: httpx.Request) -> httpx.Response:
        if not up["on"]:
            raise httpx.ConnectError("down", request=req)
        return httpx.Response(
            200, json={"chain_stats": {"funded_txo_sum": 10**8, "spent_txo_sum": 0}, "mempool_stats": {}}
        )

    svc = _service(tmp_path, handler)
    w = Wallet("1", "litecoin", "LcNS6c8RddAMjewDrUAAi8BzecKoosnkN3")
    await svc.balance(w, ttl=0)
    up["on"] = False
    f = await svc.balance(w, ttl=0)
    assert f.value.assets[0].amount == pytest.approx(1.0) and f.error and f.stale_age is not None


async def test_hyperliquid_sums_spot_perp_staking_and_evm(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if req.url.host == "rpc.hyperliquid.xyz":
            return httpx.Response(200, json=[{"id": 0, "result": hex(2 * 10**18)}, {"id": 1, "result": "0x3e7"}])
        kind = body["type"]
        if kind == "spotClearinghouseState":
            return httpx.Response(
                200,
                json={
                    "balances": [
                        {"coin": "HYPE", "total": "10"},
                        {"coin": "USDC", "total": "5"},
                        {"coin": "PURR", "total": "1000"},
                    ]
                },
            )
        if kind == "clearinghouseState":
            return httpx.Response(200, json={"marginSummary": {"accountValue": "100"}})
        return httpx.Response(200, json={"delegated": "3", "undelegated": "0", "totalPendingWithdrawal": "1"})

    svc = _service(tmp_path, handler)
    f = await svc.balance(Wallet("1", "hyperliquid", "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"), ttl=0)
    got = {a.coin_id: a.amount for a in f.value.assets}
    assert got == {"hyperliquid": pytest.approx(16.0), "usd-coin": pytest.approx(105.0)}
    assert f.value.ignored == 1  # PURR: no coin behind it


async def test_meme_airdrops_do_not_count(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/tokens"):

            def tok(sym, cap, vol, rate, value, dec="18"):
                return {
                    "token": {
                        "address_hash": "0x" + sym.lower().ljust(40, "0")[:40],
                        "symbol": sym,
                        "decimals": dec,
                        "exchange_rate": rate,
                        "circulating_market_cap": cap,
                        "volume_24h": vol,
                        "reputation": "ok",
                    },
                    "value": value,
                }

            return httpx.Response(
                200,
                json={
                    "items": [
                        tok("WHITE", "0", "300", "0.00003", str(10**28)),  # no market cap: an airdrop
                        tok("NEXO", "800000000", "5000000", "0.8", str(2 * 10**26)),  # 20 % of the market: meaningless
                        tok("KNCL", "1600000", "7600", "0.15", str(10**21)),  # too little volume
                        tok("LDO", "900000000", "20000000", "1.2", str(10**20)),  # a real holding
                    ],
                    "next_page_params": None,
                },
            )
        return httpx.Response(200, json={"coin_balance": "0"})

    svc = _service(tmp_path, handler)
    f = await svc.balance(Wallet("1", "ethereum", "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"), ttl=0)
    syms = {a.symbol for a in f.value.assets}
    assert syms == {"ETH", "LDO"} and f.value.ignored == 3
