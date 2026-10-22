"""A Ledger over USB, read-only: which app is open, and public keys.

Talks to the device's generic HID interface through Linux hidraw — no driver,
no library: 64-byte reports on channel 0x0101 (the same framing Ledger Live
uses). Access needs Ledger's udev rules (the ``ledger-udev`` package).

Nothing here can sign: the only commands are "which app", "open/quit app" and
"give me a public key / address", optionally shown on the device screen.
"""

from __future__ import annotations

import contextlib
import os
import select
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from mazecrypto.core import address as addr
from mazecrypto.core import hd

VENDOR = 0x2C97
CHANNEL = 0x0101
PACKET = 64
H = 0x80000000


class LedgerError(Exception):
    """``code`` is a key under ``ledger.err_*``."""

    def __init__(self, code: str, sw: int = 0):
        super().__init__(f"{code} (0x{sw:04x})" if sw else code)
        self.code = code
        self.sw = sw

    @classmethod
    def of(cls, sw: int) -> LedgerError:
        if sw in (0x5515, 0x6982):
            return cls("locked", sw)
        if sw in (0x6985, 0x5501):
            return cls("denied", sw)
        if sw in (0x6E00, 0x6E01, 0x6D00, 0x6D02, 0x6511, 0x6A83):
            return cls("wrong_app", sw)
        if sw in (0x6807, 0x6A82, 0x6984):
            return cls("not_installed", sw)
        if sw in (0x6B00, 0x6A80, 0x6A86, 0x6A87):
            return cls("bad_request", sw)
        return cls("status", sw)


def frames(apdu: bytes) -> list[bytes]:
    out, off, seq = [], 0, 0
    while True:
        head = struct.pack(">HBH", CHANNEL, 0x05, seq) + (struct.pack(">H", len(apdu)) if seq == 0 else b"")
        n = min(PACKET - len(head), len(apdu) - off)
        out.append((head + apdu[off : off + n]).ljust(PACKET, b"\0"))
        off += n
        seq += 1
        if off >= len(apdu):
            return out


class Reader:
    def __init__(self) -> None:
        self.buf = b""
        self.total = -1
        self.seq = 0

    def feed(self, pkt: bytes) -> bytes | None:
        if len(pkt) < 5:
            raise LedgerError("protocol")
        ch, tag, seq = struct.unpack(">HBH", pkt[:5])
        if ch != CHANNEL:
            return None
        if tag != 0x05 or seq != self.seq:
            raise LedgerError("protocol")
        i = 5
        if seq == 0:
            self.total = struct.unpack(">H", pkt[5:7])[0]
            i = 7
        self.seq += 1
        self.buf += pkt[i : i + self.total - len(self.buf)]
        return self.buf if len(self.buf) >= self.total else None


@dataclass
class Device:
    path: str
    product: str


def _ledger_hidraw() -> list[Device]:
    """Ledger hidraw nodes on the generic interface (usage page 0xFFA0; FIDO is 0xF1D0)."""
    out = []
    for d in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        try:
            uevent = (d / "device" / "uevent").read_text()
        except OSError:
            continue
        hid_id = next((ln.split("=", 1)[1] for ln in uevent.splitlines() if ln.startswith("HID_ID=")), "")
        name = next((ln.split("=", 1)[1] for ln in uevent.splitlines() if ln.startswith("HID_NAME=")), "Ledger")
        parts = hid_id.split(":")
        if len(parts) != 3 or int(parts[1], 16) != VENDOR:
            continue
        try:
            desc = (d / "device" / "report_descriptor").read_bytes()
        except OSError:
            desc = b""
        if b"\x06\xa0\xff" in desc or not desc:
            out.append(Device(f"/dev/{d.name}", name))
    return out


def find() -> Device | None:
    lst = _ledger_hidraw()
    return lst[0] if lst else None


@dataclass
class Transport:
    dev: Device
    fd: int = field(default=-1)

    def open(self) -> Transport:
        try:
            self.fd = os.open(self.dev.path, os.O_RDWR)
        except PermissionError as e:
            raise LedgerError("permission") from e
        except OSError as e:
            raise LedgerError("no_device") from e
        return self

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def exchange(self, apdu: bytes, timeout: float = 60.0) -> bytes:
        try:
            for f in frames(apdu):
                os.write(self.fd, b"\0" + f)  # report id 0
            r = Reader()
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise LedgerError("timeout")
                ready, _, _ = select.select([self.fd], [], [], min(left, 0.5))
                if not ready:
                    continue
                done = r.feed(os.read(self.fd, PACKET))
                if done is not None:
                    return done
        except OSError as e:
            raise LedgerError("disconnected") from e


def _path(path: list[int]) -> bytes:
    return bytes([len(path)]) + b"".join(struct.pack(">I", i) for i in path)


class Ledger:
    def __init__(self, t: Transport):
        self.t = t

    def close(self) -> None:
        self.t.close()

    def send(self, cla: int, ins: int, p1: int = 0, p2: int = 0, data: bytes = b"", timeout: float = 60.0) -> bytes:
        r = self.t.exchange(bytes([cla, ins, p1, p2, len(data)]) + data, timeout)
        if len(r) < 2:
            raise LedgerError("protocol")
        sw = struct.unpack(">H", r[-2:])[0]
        if sw != 0x9000:
            raise LedgerError.of(sw)
        return r[:-2]

    def app(self) -> tuple[str, str]:
        r = self.send(0xB0, 0x01, timeout=10)
        if not r or r[0] != 1:
            raise LedgerError("protocol")
        n = r[1]
        name = r[2 : 2 + n].decode("ascii", "replace")
        vl = r[2 + n]
        return name, r[3 + n : 3 + n + vl].decode("ascii", "replace")

    def open_app(self, name: str) -> None:
        self.send(0xE0, 0xD8, data=name.encode("ascii"), timeout=120)

    def quit_app(self) -> None:
        with contextlib.suppress(LedgerError):
            self.send(0xB0, 0xA7, timeout=10)

    # Bitcoin app, current protocol
    def btc_xpub(self, path: list[int], display: bool = False) -> str:
        return self.send(0xE1, 0x00, data=bytes([int(display)]) + _path(path), timeout=180 if display else 30).decode()

    # Bitcoin-family legacy protocol (Litecoin app, older Bitcoin app)
    def _legacy(self, path: list[int], display: bool = False, fmt: int = 0) -> tuple[bytes, str, bytes]:
        r = self.send(0xE0, 0x40, int(display), fmt, _path(path), timeout=180 if display else 30)
        pl = r[0]
        pub = r[1 : 1 + pl]
        al = r[1 + pl]
        a = r[2 + pl : 2 + pl + al].decode()
        return pub, a, r[2 + pl + al : 34 + pl + al]

    def legacy_xpub(self, path: list[int]) -> str:
        pub, _a, cc = self._legacy(path)
        fp = int.from_bytes(hd.hash160(hd.compress(self._legacy(path[:-1])[0]))[:4], "big") if len(path) > 1 else 0
        return hd.ExtKey(hd.XPUB, len(path), fp, path[-1], cc, hd.compress(pub)).serialize()

    def account_xpub(self, chain: str, path: list[int]) -> str:
        if chain == "bitcoin":
            try:
                return self.btc_xpub(path)
            except LedgerError as e:
                if e.code != "wrong_app":
                    raise
        return self.legacy_xpub(path)

    def legacy_show(self, path: list[int], script: str) -> str:
        return self._legacy(path, True, {"sh-wpkh": 1, "wpkh": 2, "tr": 3}.get(script, 0))[1]

    def eth_address(self, path: list[int], display: bool = False) -> str:
        r = self.send(0xE0, 0x02, int(display), 0, _path(path), timeout=180 if display else 30)
        pl = r[0]
        al = r[1 + pl]
        h = r[2 + pl : 2 + pl + al].decode().lower().removeprefix("0x")
        if len(h) != 40:
            raise LedgerError("protocol")
        return addr.eip55(h)

    def sol_address(self, path: list[int], display: bool = False) -> str:
        r = self.send(0xE0, 0x05, int(display), 0, _path(path), timeout=180 if display else 30)
        if len(r) != 32:
            raise LedgerError("protocol")
        return addr.b58encode(r)

    def tron_address(self, path: list[int], display: bool = False) -> str:
        r = self.send(0xE0, 0x02, int(display), 0, _path(path), timeout=180 if display else 30)
        pl = r[0]
        al = r[1 + pl]
        a = r[2 + pl : 2 + pl + al].decode()
        res = addr.parse(a)
        if not (isinstance(res, addr.Ok) and res.chains == ("tron",)):
            raise LedgerError("protocol")
        return a

    # Monero app: an options byte (0) before the data; older apps answer CLA 0x00 instead of 0x03.
    _xmr_cla = 0x03

    def _xmr(self, ins: int, p1: int, data: bytes = b"", timeout: float = 30) -> bytes:
        for cla in dict.fromkeys((self._xmr_cla, 0x00, 0x04)):
            try:
                r = self.send(cla, ins, p1, 0, b"\0" + data, timeout)
                self._xmr_cla = cla
                return r
            except LedgerError as e:
                if e.sw != 0x6E00:
                    raise
        raise LedgerError("wrong_app")

    def monero_account(self) -> tuple[str, bytes]:
        """Primary address and private *view* key (the device asks to allow the export; the spend key stays)."""
        self._xmr(0x02, 0, b"0.18.4.0")  # INS_RESET with the client version
        pub = self._xmr(0x20, 1)  # public view key, public spend key
        if len(pub) < 64:
            raise LedgerError("protocol")
        sec = self._xmr(0x20, 2, timeout=180)  # export the view key (confirm on the device)
        if len(sec) < 32:
            raise LedgerError("protocol")
        body = bytes([18]) + pub[32:64] + pub[:32]
        return addr.b58encode_monero(body + addr.keccak256(body)[:4]), sec[:32]

    def monero_show(self) -> None:
        self._xmr(0x21, 0, bytes(8), timeout=180)


# ── networks and Ledger Live's paths ────────────────────────────────────────

NETWORKS = (  # (chain, app on the device, coin)
    ("bitcoin", "Bitcoin", "BTC"),
    ("ethereum", "Ethereum", "ETH"),
    ("solana", "Solana", "SOL"),
    ("tron", "Tron", "TRX"),
    ("monero", "Monero", "XMR"),
    ("litecoin", "Litecoin", "LTC"),
)
APP = {c: a for c, a, _ in NETWORKS}
PURPOSE = {"wpkh": 84, "tr": 86, "sh-wpkh": 49, "pkh": 44}
COIN_TYPE = {"monero": 128, "bitcoin": 0, "litecoin": 2, "ethereum": 60, "solana": 501, "tron": 195}


def scripts_for(chain: str) -> tuple[str, ...]:
    return ("wpkh", "sh-wpkh", "pkh") if chain == "litecoin" else ("wpkh", "tr", "sh-wpkh", "pkh")


def path(chain: str, n: int, script: str = "") -> list[int]:
    coin = COIN_TYPE[chain] | H
    if chain in ("bitcoin", "litecoin"):
        return [PURPOSE[script] | H, coin, n | H]
    if chain == "solana":
        return [44 | H, coin, n | H]
    return [44 | H, coin, n | H, 0, 0]


@dataclass
class Found:
    chain: str
    index: int
    address: str  # Bitcoin/Litecoin: the account key; others: the address
    path: list[int]
    script: str = ""
    used: bool | None = None
    balance: float | None = None
    view_key: bytes | None = None  # Monero: the exported view key, kept in memory until added

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.script}:{self.index}"


def connect(want_app: str, status=lambda _s: None) -> Ledger:
    """Open the device and make sure ``want_app`` runs on it (opening it from the dashboard if needed)."""

    def open_dev() -> Ledger:
        dev = None
        for _ in range(16):  # after an app switch the device re-enumerates
            dev = find()
            if dev:
                break
            time.sleep(0.5)
        if dev is None:
            raise LedgerError("no_device")
        return Ledger(Transport(dev).open())

    lg = open_dev()
    name, _ver = lg.app()
    if name != want_app:
        if name != "BOLOS":
            status("closing")
            lg.quit_app()
            lg.close()
            time.sleep(1.5)
            lg = open_dev()
        status("opening")
        lg.open_app(want_app)
        lg.close()
        time.sleep(2.0)
        lg = open_dev()
        name, _ver = lg.app()
        if name != want_app:
            raise LedgerError("wrong_app")
    return lg


def verify(lg: Ledger, f: Found) -> None:
    """Show the account on the device screen, for the user to compare and approve."""
    if f.chain == "bitcoin":
        try:
            lg.btc_xpub(f.path, display=True)
            return
        except LedgerError as e:
            if e.code != "wrong_app":
                raise
        lg.legacy_show([*f.path, 0, 0], f.script)
    elif f.chain == "litecoin":
        lg.legacy_show([*f.path, 0, 0], f.script)
    elif f.chain == "ethereum":
        lg.eth_address(f.path, True)
    elif f.chain == "solana":
        lg.sol_address(f.path, True)
    elif f.chain == "tron":
        lg.tron_address(f.path, True)
    elif f.chain == "monero":
        lg.monero_show()
