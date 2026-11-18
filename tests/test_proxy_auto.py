"""Watched addresses and xpubs are not looked up from the user's own IP when
a Tor is running here (audit CR1). The proxy used to be off unless switched on
by hand, so every explorer saw all of one person's addresses come from one IP.
"""
from __future__ import annotations

import socket


from mazecrypto.core import settings as cfg
from mazecrypto.core.store import Cache
from mazecrypto.providers.service import MarketService


def _probe(open_ports):
    return lambda host, port: (host, port) in open_ports


def test_explicit_proxy_wins():
    s = cfg.Settings()
    s.proxy = cfg.ProxySettings(True, "socks5h://10.0.0.2:1080")
    assert s.effective_proxy(_probe({("127.0.0.1", 9050)})) == "socks5h://10.0.0.2:1080"


def test_a_running_tor_is_used_on_its_own():
    s = cfg.Settings()
    assert s.effective_proxy(_probe({("127.0.0.1", 9050)})) == "socks5h://127.0.0.1:9050"
    assert s.effective_proxy(_probe({("127.0.0.1", 9150)})) == "socks5h://127.0.0.1:9150"


def test_the_tor_set_for_monero_comes_first():
    s = cfg.Settings()
    s.tor_host, s.tor_port = "127.0.0.1", 9250
    assert s.effective_proxy(_probe({("127.0.0.1", 9250), ("127.0.0.1", 9150)})) == "socks5h://127.0.0.1:9250"


def test_direct_only_without_tor_or_when_switched_off():
    s = cfg.Settings()
    assert s.effective_proxy(_probe(set())) is None
    s.proxy.auto_tor = False
    assert s.effective_proxy(_probe({("127.0.0.1", 9050)})) is None


def test_existing_configs_get_the_protection():
    """A config saved before auto_tor existed has no such key."""
    old = cfg.Settings().to_dict()
    old["proxy"] = {"enabled": False, "url": "socks5h://127.0.0.1:9050"}
    assert cfg.Settings.from_dict(old).proxy.auto_tor is True


def test_round_trip_keeps_the_choice():
    s = cfg.Settings()
    s.proxy.auto_tor = False
    assert cfg.Settings.from_dict(s.to_dict()).proxy.auto_tor is False


def test_the_market_service_uses_it(tmp_path):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        s = cfg.Settings()
        s.tor_port = port
        svc = MarketService(s, Cache(tmp_path / "c.db"))
        assert svc.http.proxy == f"socks5h://127.0.0.1:{port}"
    finally:
        srv.close()
