"""Monero view-only wallets, scanned on this computer, only over Tor.

A view-only wallet is the primary address plus the private *view* key: enough
to recognise every payment to the wallet and its subaddresses, never enough to
spend. The node serves the same pruned blocks to everyone (``/get_blocks.bin``);
the outputs are found here, with libsodium's Ed25519 and OpenSSL's Keccak.

Network: every byte goes through the Tor SOCKS port in the settings. Nothing
is sent when that port is closed; onion nodes are reached as onion services
(plain HTTP inside the Tor circuit), a clearnet node only over HTTPS and only
after check.torproject.org confirms the exit is Tor.

Spends: a view key cannot compute key images, so spends are recognised the
way they almost always look — a transaction that returns change to the wallet
and has one of its unspent outputs in a ring spent that output. A send of the
whole balance without change is not seen. This is the same logic as the
Android app.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import hashlib
import json
import os
import random
import socket
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from mazecrypto.core import address as addr
from mazecrypto.core.wallet import Asset, Leg, Transfer, Wallet, WalletBalance

# ── crypto: libsodium + OpenSSL ─────────────────────────────────────────────

L = 2**252 + 27742317777372353535851937790883648493


def _sodium() -> ctypes.CDLL:
    name = ctypes.util.find_library("sodium") or "libsodium.so.26"
    lib = ctypes.CDLL(name)
    if lib.sodium_init() < 0:
        raise OSError("libsodium init failed")
    return lib


_lib: ctypes.CDLL | None = None


def sodium() -> ctypes.CDLL:
    global _lib
    if _lib is None:
        _lib = _sodium()
    return _lib


def keccak(b: bytes) -> bytes:
    try:
        return hashlib.new("keccak-256", b).digest()
    except ValueError:
        return addr.keccak256(b)


def reduce32(b: bytes) -> bytes:
    return (int.from_bytes(b, "little") % L).to_bytes(32, "little")


def hs(b: bytes) -> bytes:
    return reduce32(keccak(b))


def varint(n: int) -> bytes:
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


class Ed:
    """Ed25519 group operations on compressed points (libsodium, main subgroup only)."""

    @staticmethod
    def mul(k: bytes, p: bytes) -> bytes | None:
        q = ctypes.create_string_buffer(32)
        if sodium().crypto_scalarmult_ed25519_noclamp(q, k, p) != 0:
            return None
        return q.raw

    @staticmethod
    def mul_g(k: bytes) -> bytes | None:
        q = ctypes.create_string_buffer(32)
        if sodium().crypto_scalarmult_ed25519_base_noclamp(q, k) != 0:
            return None
        return q.raw

    @staticmethod
    def add(p: bytes, q: bytes) -> bytes | None:
        r = ctypes.create_string_buffer(32)
        return r.raw if sodium().crypto_core_ed25519_add(r, p, q) == 0 else None

    @staticmethod
    def sub(p: bytes, q: bytes) -> bytes | None:
        r = ctypes.create_string_buffer(32)
        return r.raw if sodium().crypto_core_ed25519_sub(r, p, q) == 0 else None


H_POINT = bytes.fromhex("8b655970153799af2aeadc9ff1add0ea6c7251d54154cfa92c173a0dd39c1f94")
GENESIS = bytes.fromhex("418015bb9ae982a1975da7d79277c2705727a56894ba0fb246adaabb1f4632e3")
EIGHT = (8).to_bytes(32, "little")


def derivation(view_key: bytes, r: bytes) -> bytes | None:
    """8·a·R (R is a tx public key, in the prime-order subgroup: (8a mod L)·R is the same point)."""
    k8 = ((int.from_bytes(view_key, "little") * 8) % L).to_bytes(32, "little")
    return Ed.mul(k8, r)


# ── view wallet ─────────────────────────────────────────────────────────────

SUB_ACCOUNTS, SUB_MAIN, SUB_OTHER = 3, 100, 20


def check_view_key(address: str, key_hex: str) -> str | None:
    """None when ``key_hex`` is the private view key of ``address``; else "primary", "format",
    "spend_key" (the *spend* key was given: refuse and forget it) or "mismatch"."""
    d = addr.b58decode_monero(address)
    if d is None or len(d) != 69 or d[0] != 18:
        return "primary"
    k = key_hex.strip().lower()
    if len(k) != 64 or any(c not in "0123456789abcdef" for c in k):
        return "format"
    key = bytes.fromhex(k)
    if int.from_bytes(key, "little") >= L:
        return "format"
    pub = Ed.mul_g(key)
    if pub == d[1:33]:
        return "spend_key"
    if pub != d[33:65]:
        return "mismatch"
    return None


class ViewWallet:
    def __init__(self, address: str, view_key: bytes):
        d = addr.b58decode_monero(address)
        if d is None or len(d) != 69:
            raise ValueError("address")
        self.view_key = view_key
        self.spend_pub = d[1:33]
        self.subs: dict[bytes, tuple[int, int]] = {self.spend_pub: (0, 0)}
        for major in range(SUB_ACCOUNTS):
            for minor in range(SUB_MAIN if major == 0 else SUB_OTHER):
                if major == minor == 0:
                    continue
                m = hs(b"SubAddr\0" + view_key + major.to_bytes(4, "little") + minor.to_bytes(4, "little"))
                mg = Ed.mul_g(m)
                d_sub = Ed.add(self.spend_pub, mg) if mg else None
                if d_sub:
                    self.subs[d_sub] = (major, minor)


# ── wire formats ────────────────────────────────────────────────────────────


class WireError(Exception):
    pass


MAX_BLOCKS = 200
_SIG = bytes([0x01, 0x11, 0x01, 0x01, 0x01, 0x01, 0x02, 0x01, 0x01])


def _epee_varint(n: int) -> bytes:
    if n < 1 << 6:
        return bytes([n << 2])
    if n < 1 << 14:
        return ((n << 2) | 1).to_bytes(2, "little")
    if n < 1 << 30:
        return ((n << 2) | 2).to_bytes(4, "little")
    return ((n << 2) | 3).to_bytes(8, "little")


def epee_request(start_height: int) -> bytes:
    def entry(name: str, body: bytes) -> bytes:
        return bytes([len(name)]) + name.encode() + body

    entries = [
        entry("block_ids", bytes([10]) + _epee_varint(32) + GENESIS),
        entry("start_height", bytes([5]) + start_height.to_bytes(8, "little")),
        entry("prune", bytes([11, 1])),
        entry("no_miner_tx", bytes([11, 0])),
        # keeps each answer a few MB: long Tor downloads are the ones that get cut (older nodes ignore it)
        entry("max_block_count", bytes([5]) + MAX_BLOCKS.to_bytes(8, "little")),
    ]
    return _SIG + _epee_varint(len(entries)) + b"".join(entries)


class _Epee:
    def __init__(self, b: bytes, i: int):
        self.b, self.i = b, i

    def byte(self) -> int:
        if self.i >= len(self.b):
            raise WireError("truncated")
        self.i += 1
        return self.b[self.i - 1]

    def le(self, n: int) -> int:
        if self.i + n > len(self.b):
            raise WireError("truncated")
        v = int.from_bytes(self.b[self.i : self.i + n], "little")
        self.i += n
        return v

    def varint(self) -> int:
        if self.i >= len(self.b):
            raise WireError("truncated")
        return self.le((1, 2, 4, 8)[self.b[self.i] & 3]) >> 2

    def section(self, depth: int = 0) -> dict:
        if depth > 16:
            raise WireError("too deep")
        out = {}
        for _ in range(self.varint()):
            name = self.b[self.i + 1 : self.i + 1 + self.b[self.i]].decode("ascii", "replace")
            self.i += 1 + self.b[self.i]
            t = self.byte()
            if t & 0x80:
                out[name] = [self.value(t & 0x7F, depth) for _ in range(self.varint())]
            else:
                out[name] = self.value(t, depth)
        return out

    def value(self, t: int, depth: int) -> Any:
        if t in (1, 5):
            return self.le(8)
        if t in (2, 6):
            return self.le(4)
        if t in (3, 7):
            return self.le(2)
        if t in (4, 8):
            return self.le(1)
        if t == 9:
            self.le(8)
            return None
        if t == 10:
            n = self.varint()
            if self.i + n > len(self.b):
                raise WireError("truncated")
            v = self.b[self.i : self.i + n]
            self.i += n
            return v
        if t == 11:
            return self.byte() != 0
        if t == 12:
            return self.section(depth + 1)
        raise WireError(f"type {t}")


def epee_read(b: bytes) -> dict:
    if not b.startswith(_SIG):
        raise WireError("not an epee response")
    return _Epee(b, len(_SIG)).section()


@dataclass
class Out:
    amount: int
    key: bytes
    view_tag: int | None


@dataclass
class Tx:
    hash: bytes
    coinbase: bool
    rings: list[list[int]]
    outputs: list[Out]
    pub_keys: list[bytes]
    additional: list[bytes]
    rct_type: int
    fee: int
    ecdh: list[bytes]
    commitments: list[bytes]


class Blob:
    def __init__(self, b: bytes):
        self.b, self.i = b, 0

    def byte(self) -> int:
        if self.i >= len(self.b):
            raise WireError("truncated")
        self.i += 1
        return self.b[self.i - 1]

    def bytes(self, n: int) -> bytes:
        if n < 0 or self.i + n > len(self.b):
            raise WireError("truncated")
        self.i += n
        return self.b[self.i - n : self.i]

    def varint(self) -> int:
        v = shift = 0
        while True:
            c = self.byte()
            v |= (c & 0x7F) << shift
            if not c & 0x80:
                return v
            shift += 7
            if shift > 63:
                raise WireError("varint")

    def count(self, cap: int = 100_000) -> int:
        n = self.varint()
        if n > cap:
            raise WireError("count")
        return n

    def header(self) -> int:
        self.varint()
        self.varint()
        ts = self.varint()
        self.bytes(36)
        return ts

    def tx(self, h: bytes | None) -> Tx:
        start = self.i
        version = self.varint()
        self.varint()
        coinbase = False
        rings: list[list[int]] = []
        for _ in range(self.count()):
            t = self.byte()
            if t == 0xFF:
                self.varint()
                coinbase = True
            elif t == 0x02:
                self.varint()
                acc, ring = 0, []
                for _ in range(self.count(1024)):
                    acc += self.varint()
                    ring.append(acc)
                self.bytes(32)
                rings.append(ring)
            else:
                raise WireError("input type")
        outs = []
        n_out = self.count()
        for _ in range(n_out):
            amount = self.varint()
            t = self.byte()
            if t == 0x02:
                outs.append(Out(amount, self.bytes(32), None))
            elif t == 0x03:
                outs.append(Out(amount, self.bytes(32), self.byte()))
            else:
                raise WireError("output type")
        extra = self.bytes(self.count(1_000_000))
        prefix_end = self.i
        keys, additional = _extra(extra)
        rct = fee = 0
        ecdh: list[bytes] = []
        commits: list[bytes] = []
        if version >= 2:
            rct = self.byte()
            if rct:
                fee = self.varint()
                if rct == 2:
                    self.bytes(32 * len(rings))
                for _ in range(n_out):
                    ecdh.append(self.bytes(8) if rct >= 4 else self.bytes(64)[32:40])
                for _ in range(n_out):
                    commits.append(self.bytes(32))
        if h is None:  # the miner tx: compute its hash
            if version >= 2:
                h = keccak(keccak(self.b[start:prefix_end]) + keccak(self.b[prefix_end : self.i]) + bytes(32))
            else:
                h = keccak(self.b[start : self.i])
        return Tx(h, coinbase, rings, outs, keys, additional, rct, fee, ecdh, commits)

    def hashes(self) -> list[bytes]:
        return [self.bytes(32) for _ in range(self.count())]


def _extra(e: bytes) -> tuple[list[bytes], list[bytes]]:
    keys: list[bytes] = []
    additional: list[bytes] = []
    p = Blob(e)
    try:
        while p.i < len(e):
            t = p.byte()
            if t == 0x00:
                break
            if t == 0x01:
                keys.append(p.bytes(32))
            elif t == 0x02:
                p.bytes(p.byte())
            elif t in (0x03, 0xDE):
                p.bytes(p.varint())
            elif t == 0x04:
                for _ in range(min(p.varint(), 256)):
                    additional.append(p.bytes(32))
            else:
                break
    except WireError:
        pass
    return keys, additional


# ── scanning ────────────────────────────────────────────────────────────────


@dataclass
class Owned:
    tx: str
    h: int
    ts: int
    i: int
    g: int
    a: int
    ma: int = 0
    mi: int = 0
    cb: bool = False
    st: str = ""
    sh: int = 0
    sts: int = 0


@dataclass
class Pay:
    """A transaction that paid the wallet, with its rings (absolute output indices).

    Kept so spends can be worked out in any scan order: a payment found later
    (reading history backwards) can still be matched to a spend seen earlier.
    """

    tx: str
    h: int
    ts: int
    fee: int
    rings: list[list[int]]


@dataclass
class ScanState:
    """Forward: ``next`` is the next block to read up to the tip. Backward (no
    start date): ``low`` is the oldest block read, ``floor`` where to stop,
    ``low_ts`` that block's time."""

    next: int = 0
    tip: int = 0
    outputs: list[Owned] = field(default_factory=list)
    fees: dict[str, int] = field(default_factory=dict)
    low: int = 0
    floor: int = 0
    low_ts: int = 0
    pays: list[Pay] = field(default_factory=list)

    VERSION = 2  # 2: spends kept as Pays; older states are scanned again

    @property
    def back_done(self) -> bool:
        return self.low <= self.floor

    def rewind(self, height: int) -> None:
        """Forget everything from ``height`` on, before reading those blocks again (a reorg may have changed them)."""
        self.outputs = [o for o in self.outputs if o.h < height]
        self.pays = [p for p in self.pays if p.h < height]
        self.next = min(self.next, height)
        self.resolve()

    def resolve(self) -> None:
        """Work out the spends from scratch: a transaction that sends change back to the wallet and has one of
        the wallet's earlier outputs in a ring spent that output (one per ring)."""
        for o in self.outputs:
            o.st, o.sh, o.sts = "", 0, 0
        self.fees = {}
        by_g: dict[int, list[Owned]] = {}
        for o in self.outputs:
            if o.g >= 0:
                by_g.setdefault(o.g, []).append(o)
        for p in sorted(self.pays, key=lambda x: x.h):
            spent = False
            for ring in p.rings:
                cands = [o for g in ring for o in by_g.get(g, ()) if not o.st and o.h < p.h and o.tx != p.tx]
                if cands:
                    o = min(cands, key=lambda x: x.g)
                    o.st, o.sh, o.sts = p.tx, p.h, p.ts
                    spent = True
            if spent:
                self.fees[p.tx] = p.fee

    def to_json(self) -> str:
        return json.dumps(
            {
                "v": self.VERSION,
                "next": self.next,
                "tip": self.tip,
                "low": self.low,
                "floor": self.floor,
                "low_ts": self.low_ts,
                "outputs": [asdict(o) for o in self.outputs],
                "pays": [asdict(p) for p in self.pays],
            }
        )

    @classmethod
    def from_json(cls, s: str) -> ScanState | None:
        d = json.loads(s)
        if d.get("v") != cls.VERSION:
            return None
        st = cls(
            int(d.get("next", 0)),
            int(d.get("tip", 0)),
            [Owned(**o) for o in d.get("outputs", [])],
            {},
            int(d.get("low", 0)),
            int(d.get("floor", 0)),
            int(d.get("low_ts", 0)),
            [Pay(**p) for p in d.get("pays", [])],
        )
        st.resolve()
        return st


def scan_tx(w: ViewWallet, tx: Tx) -> list[tuple[int, int, int, int]]:
    """(output index, amount, account, subaddress index) of each output of ``tx`` that pays ``w``."""
    if not tx.outputs:
        return []
    ders = [d for r in dict.fromkeys(tx.pub_keys) if (d := derivation(w.view_key, r))]
    found = []
    for i, o in enumerate(tx.outputs):
        cands = list(ders)
        if i < len(tx.additional) and (d := derivation(w.view_key, tx.additional[i])):
            cands.append(d)
        vi = varint(i)
        for d in cands:
            if o.view_tag is not None and keccak(b"view_tag" + d + vi)[0] != o.view_tag:
                continue
            s = hs(d + vi)
            sg = Ed.mul_g(s)
            d_sub = Ed.sub(o.key, sg) if sg else None
            sub = w.subs.get(d_sub) if d_sub else None
            if sub is None:
                continue
            if tx.coinbase or tx.rct_type == 0:
                amount = o.amount
            elif tx.rct_type >= 4:
                amount = _decode_amount(tx, i, s)
                if amount is None:
                    break
            else:
                break  # pre-2019 RingCT amount encoding: not supported
            found.append((i, amount, sub[0], sub[1]))
            break
    return found


def _decode_amount(tx: Tx, i: int, s: bytes) -> int | None:
    """The 8-byte encrypted amount, checked against the output's commitment so a garbled one never counts."""
    if i >= len(tx.ecdh) or i >= len(tx.commitments):
        return None
    pad = keccak(b"amount" + s)
    amount = int.from_bytes(bytes(a ^ b for a, b in zip(tx.ecdh[i], pad[:8], strict=True)), "little")
    mask_g = Ed.mul_g(hs(b"commitment_mask" + s))
    if mask_g is None:
        return None
    c = mask_g if amount == 0 else Ed.add(mask_g, Ed.mul(amount.to_bytes(32, "little"), H_POINT) or b"")
    return amount if c == tx.commitments[i] else None


def apply(
    st: ScanState, tx: Tx, found: list[tuple[int, int, int, int]], globals_: list[int], height: int, ts: int
) -> None:
    """Record what ``found`` says about ``tx`` at ``height``; call ``st.resolve()`` after a batch."""
    if not found:
        return
    txid = tx.hash.hex()
    for idx, amount, ma, mi in found:
        if any(o.tx == txid and o.i == idx for o in st.outputs):
            continue
        st.outputs.append(
            Owned(txid, height, ts, idx, globals_[idx] if idx < len(globals_) else -1, amount, ma, mi, tx.coinbase)
        )
    if not tx.coinbase and tx.rings and not any(p.tx == txid for p in st.pays):
        st.pays.append(Pay(txid, height, ts, tx.fee, [list(r) for r in tx.rings]))


# ── the sync service ────────────────────────────────────────────────────────

ONION_NODES = (  # community onion nodes (the list Feather Wallet ships with)
    "loveanvthlsepingenr7opdqy6nlhn5jidwvzzxfmwsw2rry5mn2gfqd.onion:18089",
    "plowsof3t5hogddwabaeiyrno25efmzfxyro2vligremt7sxpsclfaid.onion:18089",
    "plowsoffjexmxalw73tkjmf422gq6575fc7vicuu4javzn2ynnte6tyd.onion:18089",
    "ravfxmrzi3eet62s4nkuu5sgpq6otgtffluk3ylb2kuvimtb34sonryd.onion:18089",
    "rucknium757bokwv3ss35ftgc3gzb7hgbvvglbg3hisp7tsj2fkd2nyd.onion:18081",
    "a6orjo6aiotog3njppja5jwnd3rexzfjiejxnojvw74p3kma45fundid.onion:18089",
    "moneroxmrxw44lku6qniyarpwgznpcwml4drq7vb24ppatlcg4kmxpqd.onion:18089",
    "ip4zpbps7unk6xhlanqtw24f75akfbl3upeckfjqjks7ftfnk4i73oid.onion:18081",
)
REF_HEIGHT, REF_TIME = 2_688_888, 1_660_420_800  # the v15 fork (view tags), 2022-08-13
RESCAN_TAIL = 10
MIN_INTERVAL = 120.0
BACK_STEP = 480  # about what a node sends per request at today's block sizes


def height_at(epoch: int) -> int:
    """Approximate height at ``epoch`` (two minutes a block), a day early to be safe."""
    return max(0, REF_HEIGHT + (epoch - REF_TIME) // 120 - 720)


class TorError(Exception):
    """``code`` is a key under ``xmr.err_*``."""

    def __init__(self, code: str, msg: str = ""):
        super().__init__(msg or code)
        self.code = code


@dataclass
class Progress:
    """``phase`` "scan" = catching up to the tip; "back" = reading history backwards, at ``height`` (time ``ts``)."""

    height: int
    tip: int
    start: int
    node: str
    phase: str = "scan"
    ts: int = 0

    @property
    def fraction(self) -> float:
        return (
            1.0 if self.tip <= self.start else max(0.0, min(1.0, (self.height - self.start) / (self.tip - self.start)))
        )


class Keys:
    """View keys in the desktop keyring (Secret Service: KWallet / GNOME Keyring); a 0600 file only when none runs."""

    def __init__(self, fallback: Path):
        self.fallback = fallback

    def _items(self, conn, wid: str):
        import secretstorage

        col = secretstorage.get_default_collection(conn)
        if col.is_locked():
            col.unlock()
        return col, list(col.search_items({"application": "maze-crypto", "monero-view": wid}))

    def put(self, wid: str, key: bytes) -> None:
        try:
            import secretstorage

            with secretstorage.dbus_init() as conn:
                col, old = self._items(conn, wid)
                for it in old:
                    it.delete()
                col.create_item(
                    f"Maze Crypto · Monero view key {wid}",
                    {"application": "maze-crypto", "monero-view": wid},
                    key.hex().encode(),
                    replace=True,
                )
            return
        except Exception:  # noqa: BLE001 - no Secret Service: fall back to a private file
            pass
        d = self._load()
        d[wid] = key.hex()
        self._save(d)

    def get(self, wid: str) -> bytes | None:
        try:
            import secretstorage

            with secretstorage.dbus_init() as conn:
                _col, items = self._items(conn, wid)
                if items:
                    return bytes.fromhex(items[0].get_secret().decode())
        except Exception:  # noqa: BLE001
            pass
        h = self._load().get(wid)
        return bytes.fromhex(h) if h else None

    def remove(self, wid: str) -> None:
        try:
            import secretstorage

            with secretstorage.dbus_init() as conn:
                for it in self._items(conn, wid)[1]:
                    it.delete()
        except Exception:  # noqa: BLE001
            pass
        d = self._load()
        if d.pop(wid, None) is not None:
            self._save(d)

    def _load(self) -> dict:
        try:
            return json.loads(self.fallback.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, d: dict) -> None:
        self.fallback.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.fallback.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(d))
        os.replace(tmp, self.fallback)


class MoneroSync:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "monero"
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        self.keys = Keys(self.dir / "keys.json")
        self.progress: dict[str, Progress] = {}
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._good: str | None = None
        self._history_req: dict[str, bool] = {}  # "stop here" (False) / "go on" (True) while a scan runs

    # state
    def _file(self, wid: str) -> Path:
        return self.dir / f"{wid}.json"

    def state(self, wid: str) -> ScanState | None:
        try:
            return ScanState.from_json(self._file(wid).read_text())
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _save(self, wid: str, st: ScanState) -> None:
        tmp = self._file(wid).with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(st.to_json())
        os.replace(tmp, self._file(wid))

    def set_history(self, wid: str, resume: bool) -> None:
        """Stop reading history at what has been read so far, or (``resume``) go on further back."""
        self._history_req[wid] = resume
        if wid in self.progress:
            return  # the running scan picks it up after this batch
        st = self.state(wid)
        if st is None:
            return
        self._history_req.pop(wid, None)
        st.floor = 1 if resume else st.low
        self._save(wid, st)

    def forget(self, wid: str) -> None:
        self.keys.remove(wid)
        self._file(wid).unlink(missing_ok=True)

    def balance(self, w: Wallet) -> WalletBalance | None:
        st = self.state(w.id)
        if st is None or not st.next:
            return None
        unspent = [o for o in st.outputs if not o.st]
        total = sum(o.a for o in unspent)
        locked = sum(o.a for o in unspent if st.tip - o.h < (60 if o.cb else 10))
        return WalletBalance(
            [Asset("monero", "XMR", "Monero", total / 1e12, pending=locked / 1e12)],
            time.time(),
            len({o.tx for o in st.outputs}),
        )

    def activity(self, w: Wallet) -> list[Transfer]:
        st = self.state(w.id)
        if st is None:
            return []
        rs: dict[str, list[int]] = {}
        when: dict[str, int] = {}
        for o in st.outputs:
            rs.setdefault(o.tx, [0, 0])[0] += o.a
            when[o.tx] = o.ts
            if o.st:
                rs.setdefault(o.st, [0, 0])[1] += o.a
                when[o.st] = o.sts
        out = [
            Transfer(
                "monero",
                txid,
                when.get(txid, 0),
                "ok",
                [Leg("monero", "XMR", (r - s) / 1e12)],
                st.fees[txid] / 1e12 if s and txid in st.fees else None,
                "XMR",
                True,
                "",
                {w.id},
            )
            for txid, (r, s) in rs.items()
        ]
        out.sort(key=lambda t: -t.ts)
        return out

    # network
    @staticmethod
    def _node(spec: str) -> tuple[str, str, bool]:
        s = spec.strip()
        u = urlparse(s if "://" in s else f"http://{s}")
        host = (u.hostname or "").lower()
        if not host:
            raise TorError("node")
        onion = host.endswith(".onion")
        if u.scheme == "http" and not onion:
            raise TorError("node_http")
        if u.scheme not in ("http", "https"):
            raise TorError("node")
        port = u.port or (443 if u.scheme == "https" else 18081)
        return f"{u.scheme}://{host}:{port}", host, onion

    @staticmethod
    def _port_open(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            return False

    def _client(self, host: str, port: int) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            proxy=f"socks5h://{host}:{port}",
            timeout=httpx.Timeout(180.0, connect=40.0),
            headers={"User-Agent": "monero-wallet"},
            follow_redirects=False,
        )

    async def _pick(
        self, c: httpx.AsyncClient, settings, exclude: frozenset[str] = frozenset()
    ) -> tuple[str, str, int]:
        if not await asyncio.to_thread(self._port_open, settings.tor_host, settings.tor_port):
            raise TorError("tor_off")
        specs = (
            [settings.monero_node]
            if settings.monero_node
            else (
                ([self._good] if self._good else [])
                + random.sample(
                    [n for n in ONION_NODES if n != self._good], len([n for n in ONION_NODES if n != self._good])
                )
            )
        )
        specs = [x for x in specs if x not in exclude] or specs
        last: Exception | None = None
        for spec in specs:
            try:
                base, host, onion = self._node(spec)
                if not onion:
                    r = await c.get("https://check.torproject.org/api/ip")
                    if not r.json().get("IsTor"):
                        raise TorError("not_tor")
                r = await c.get(f"{base}/get_info", timeout=45)
                o = r.json()
                if o.get("status") != "OK" or not o.get("mainnet", True) or o.get("nettype", "mainnet") != "mainnet":
                    raise WireError("node: not a mainnet node")
                self._good = spec
                return base, host, int(o["height"])
            except TorError:
                raise
            except Exception as e:  # noqa: BLE001 - try the next node
                last = e
        raise ConnectionError(f"no Monero node answered: {last}")

    async def test(self, settings) -> str:
        async with self._client(settings.tor_host, settings.tor_port) as c:
            _base, host, h = await self._pick(c, settings)
            return f"{addr.short(host, 10, 6)} · #{h}"

    async def sync_all(
        self, wallets: list[Wallet], settings, force: bool, on_update: Callable[[Wallet, str | None], None]
    ) -> None:
        lst = [w for w in wallets if w.monero_view]
        if not lst or (not force and time.time() - self._last < MIN_INTERVAL) or self._lock.locked():
            return
        async with self._lock:
            self._last = time.time()
            async with self._client(settings.tor_host, settings.tor_port) as c:
                try:
                    base, host, tip = await self._pick(c, settings)
                except Exception as e:  # noqa: BLE001
                    msg = f"tor:{e.code}" if isinstance(e, TorError) else str(e) or type(e).__name__
                    for w in lst:
                        on_update(w, msg)
                    return
                for w in lst:
                    failed: set[str] = set()
                    try:
                        # A node that keeps cutting downloads is swapped for another; the scan resumes where it was.
                        for attempt in range(3):
                            try:
                                await self._sync_one(c, w, base, host, tip, lambda w=w: on_update(w, None))
                                break
                            except (httpx.TransportError, ConnectionError):
                                if attempt == 2 or settings.monero_node:
                                    raise
                                failed.add(self._good or "")
                                self._good = None
                                base, host, tip = await self._pick(c, settings, frozenset(failed))
                        on_update(w, None)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        self._good = None  # try another node next time
                        on_update(w, f"tor:{e.code}" if isinstance(e, TorError) else str(e) or type(e).__name__)
                    finally:
                        self.progress.pop(w.id, None)

    async def _fetch(self, c: httpx.AsyncClient, base: str, height: int) -> dict:
        # Tor circuits drop now and then mid-download: try the same request a few times before giving up.
        for attempt in range(3):
            try:
                r = await c.post(
                    f"{base}/get_blocks.bin",
                    content=epee_request(height),
                    headers={"Content-Type": "application/octet-stream"},
                )
                break
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 * (attempt + 1))
        if r.status_code != 200:
            raise ConnectionError(f"node: HTTP {r.status_code}")
        if len(r.content) > 48 * 1024 * 1024:
            raise ConnectionError("node: response too large")
        res = epee_read(r.content)
        if bytes(res.get("status") or b"") != b"OK":
            raise ConnectionError(f"node: {res.get('status')!r}")
        return res

    async def _sync_one(
        self, c: httpx.AsyncClient, w: Wallet, base: str, host: str, tip: int, batch_done: Callable[[], None]
    ) -> None:
        key = await asyncio.to_thread(self.keys.get, w.id)
        if key is None:
            raise TorError("no_key")
        vw = await asyncio.to_thread(ViewWallet, w.address, key)
        back = w.restore_height <= 0  # no start date: begin at the tip and read history backwards
        st = self.state(w.id) or ScanState()
        if not st.next:
            if back:
                st.next = st.low = tip
                st.floor = 1
            else:
                st.next = max(1, w.restore_height)
        start = st.low if back else max(1, w.restore_height)

        # forward: from where the last scan stopped (a few blocks back, for reorgs) to the tip
        height = max(start, st.next - RESCAN_TAIL)
        st.rewind(height)
        self.progress[w.id] = Progress(height, tip, start, host)
        while height < tip:
            res = await self._fetch(c, base, height)
            blocks = res.get("blocks") or []
            if not blocks:
                break
            first = int(res.get("start_height", height))
            tip = max(tip, int(res.get("current_height", tip)))
            if first < height:
                st.rewind(first)
            await asyncio.to_thread(
                self._scan_batch, vw, st, blocks, res.get("output_indices") or [], first, 0, 1 << 62
            )
            st.resolve()
            height = first + len(blocks)
            st.next, st.tip = height, tip
            self._save(w.id, st)
            self.progress[w.id] = Progress(height, tip, start, host)
            batch_done()
        st.next, st.tip = max(st.next, height), tip
        self._save(w.id, st)

        # backward: older history, one window at a time, until the floor (or until stopped)
        while back:
            req = self._history_req.pop(w.id, None)
            if req is not None:
                st.floor = 1 if req else st.low
                self._save(w.id, st)
            if st.back_done:
                break
            lo = max(st.floor, st.low - BACK_STEP)
            pos = lo
            while pos < st.low:  # fill [lo, low) completely: a node may send fewer blocks than asked for
                res = await self._fetch(c, base, pos)
                blocks = res.get("blocks") or []
                first = int(res.get("start_height", pos))
                if not blocks or first > pos:
                    raise ConnectionError(f"node: missing blocks at {pos}")
                ts = await asyncio.to_thread(
                    self._scan_batch, vw, st, blocks, res.get("output_indices") or [], first, pos, st.low
                )
                if pos == lo and ts:
                    st.low_ts = ts
                pos = first + len(blocks)
            st.resolve()
            st.low = lo
            self._save(w.id, st)
            self.progress[w.id] = Progress(st.low, tip, start, host, "back", st.low_ts)
            batch_done()

    @staticmethod
    def _scan_batch(vw: ViewWallet, st: ScanState, blocks: list, indices: list, start: int, lo: int, hi: int) -> int:
        """Scan the blocks with height in [lo, hi); returns the time of the first one scanned (0 if none)."""
        first_ts = 0
        for bi, entry in enumerate(blocks):
            height = start + bi
            if not lo <= height < hi:
                continue
            b = Blob(entry["block"])
            ts = b.header()
            first_ts = first_ts or ts
            miner = b.tx(None)
            hashes = b.hashes()
            txs = [t if isinstance(t, bytes) else t["blob"] for t in entry.get("txs") or []]
            if len(txs) != len(hashes):
                raise WireError("tx count")
            per_tx = (
                [list(x.get("indices") or []) for x in ((indices[bi] or {}).get("indices") or [])]
                if bi < len(indices)
                else []
            )
            all_tx = [(miner, per_tx[0] if per_tx else [])]
            all_tx += [
                (Blob(tb).tx(hashes[k]), per_tx[k + 1] if k + 1 < len(per_tx) else []) for k, tb in enumerate(txs)
            ]
            for tx, g in all_tx:
                apply(st, tx, scan_tx(vw, tx), g, height, ts)
        return first_ts
