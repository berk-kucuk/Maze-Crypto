"""Find the accounts on a Ledger the way Ledger Live does: account 0, 1, 2 …
on Ledger Live's derivation paths until the first one never used (which is
offered too, as the "new account"). Device calls run in a thread; the usage
checks go through the app's normal, allow-listed wallet providers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from mazecrypto.core import hd
from mazecrypto.core.wallet import Wallet
from mazecrypto.hw import ledger as lw

MAX_ACCOUNTS = 8
PROBE_ADDRESSES = 5


async def _account_used(svc, k: hd.ExtKey, chain: str, script: str) -> bool | None:
    try:
        recv = k.child(0)
        for i in range(PROBE_ADDRESSES):
            c = recv.child(i) if recv else None
            if c and await svc.address_tx_count(chain, hd.address_of(c.pub, chain, script)) > 0:
                return True
        return False
    except Exception:  # noqa: BLE001 - unknown usage, not a failure of the discovery
        return None


async def _address_used(svc, chain: str, address: str) -> tuple[bool | None, float | None]:
    try:
        b = (await svc.balance(Wallet("probe", chain, address), 300.0)).value
        native = b.assets[0].amount if b.assets else None
        return ((b.tx_count or 0) > 0 or any(a.amount > 0 for a in b.assets)), native
    except Exception:  # noqa: BLE001
        return None, None


async def discover(lg: lw.Ledger, chain: str, svc, on_found: Callable[[list[lw.Found]], None]) -> list[lw.Found]:
    out: list[lw.Found] = []
    if chain == "monero":
        from mazecrypto.providers import monero

        a, key = await asyncio.to_thread(lg.monero_account)
        problem = await asyncio.to_thread(monero.check_view_key, a, key.hex())
        if problem:  # a declined export gives a dummy key: it must match the address
            raise lw.LedgerError("xmr_view_declined" if problem == "mismatch" else "protocol")
        out.append(lw.Found("monero", 0, a, [], view_key=key))
        on_found(list(out))
        return out
    if chain in ("bitcoin", "litecoin"):
        for script in lw.scripts_for(chain):
            for n in range(MAX_ACCOUNTS):
                p = lw.path(chain, n, script)
                raw = hd.parse(await asyncio.to_thread(lg.account_xpub, chain, p))
                if raw is None:
                    raise lw.LedgerError("protocol")
                used = await _account_used(svc, raw, chain, script)
                out.append(lw.Found(chain, n, hd.display(raw, chain, script), p, script, used))
                on_found(list(out))
                if used is not True:
                    break
    else:
        get = {"ethereum": lg.eth_address, "solana": lg.sol_address, "tron": lg.tron_address}[chain]
        for n in range(MAX_ACCOUNTS):
            p = lw.path(chain, n)
            a = await asyncio.to_thread(get, p)
            used, bal = await _address_used(svc, chain, a)
            out.append(lw.Found(chain, n, a, p, used=used, balance=bal))
            on_found(list(out))
            if used is not True:
                break
    return out
