"""Watch-only HD accounts (BIP-32 public derivation).

An account's extended public key — pasted, or read from a Ledger — gives every
receive and change address of that account without any private key being
involved. Scripts: ``pkh`` (BIP-44, 1…/L…), ``sh-wpkh`` (BIP-49, 3…/M…),
``wpkh`` (BIP-84, bc1q…/ltc1q…), ``tr`` (BIP-86 Taproot, bc1p…).
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass

from mazecrypto.core.address import _BECH32M, _CHARSET, _polymod, b58check_ok, b58decode, b58encode_check

# secp256k1
_P = 2**256 - 2**32 - 977
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)

XPUB, YPUB, ZPUB, LTUB, MTUB = 0x0488B21E, 0x049D7CB2, 0x04B24746, 0x019DA462, 0x01B26EF6
SCRIPTS = ("wpkh", "tr", "sh-wpkh", "pkh")

# version -> (networks, implied script or None when the version does not say)
KINDS: dict[int, tuple[tuple[str, ...], str | None]] = {
    XPUB: (("bitcoin",), None),  # BIP-44 legacy and BIP-86 Taproot both export "xpub"
    YPUB: (("bitcoin",), "sh-wpkh"),
    ZPUB: (("bitcoin", "litecoin"), "wpkh"),  # some Litecoin wallets reuse zpub
    LTUB: (("litecoin",), None),
    MTUB: (("litecoin",), "sh-wpkh"),
}

Point = tuple[int, int] | None


def _add(a: Point, b: Point) -> Point:
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and (a[1] + b[1]) % _P == 0:
        return None
    lam = 3 * a[0] * a[0] * pow(2 * a[1], -1, _P) % _P if a == b else (b[1] - a[1]) * pow(b[0] - a[0], -1, _P) % _P
    x = (lam * lam - a[0] - b[0]) % _P
    return x, (lam * (a[0] - x) - a[1]) % _P


def _mul(k: int, p: Point) -> Point:
    r: Point = None
    while k:
        if k & 1:
            r = _add(r, p)
        p = _add(p, p)
        k >>= 1
    return r


def _decode(pub: bytes) -> Point:
    if len(pub) == 33 and pub[0] in (2, 3):
        x = int.from_bytes(pub[1:], "big")
        if x >= _P:
            raise ValueError("off curve")
        y = pow((x**3 + 7) % _P, (_P + 1) // 4, _P)
        if (y * y - x**3 - 7) % _P:
            raise ValueError("off curve")
        if y & 1 != pub[0] & 1:
            y = _P - y
        return x, y
    if len(pub) == 65 and pub[0] == 4:
        x, y = int.from_bytes(pub[1:33], "big"), int.from_bytes(pub[33:], "big")
        if (y * y - x**3 - 7) % _P:
            raise ValueError("off curve")
        return x, y
    raise ValueError("public key")


def _encode(p: Point) -> bytes:
    assert p is not None
    return bytes([2 + (p[1] & 1)]) + p[0].to_bytes(32, "big")


def compress(pub: bytes) -> bytes:
    return _encode(_decode(pub))


def sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def hash160(b: bytes) -> bytes:
    try:
        r = hashlib.new("ripemd160")
    except ValueError:  # OpenSSL without the legacy provider
        return _ripemd160(sha256(b))
    r.update(sha256(b))
    return r.digest()


@dataclass(frozen=True, slots=True)
class ExtKey:
    version: int
    depth: int
    parent_fp: int
    child_no: int
    chain_code: bytes
    pub: bytes  # 33-byte compressed

    @property
    def fingerprint(self) -> int:
        return int.from_bytes(hash160(self.pub)[:4], "big")

    def serialize(self, version: int | None = None) -> str:
        raw = struct.pack(">IBII", version or self.version, self.depth, self.parent_fp, self.child_no)
        return b58encode_check(raw + self.chain_code + self.pub)

    def child(self, i: int) -> ExtKey | None:
        """Non-hardened child; None for the (astronomically rare) invalid index BIP-32 says to skip."""
        if not 0 <= i < 0x80000000:
            raise ValueError("hardened derivation needs the private key")
        h = hmac.new(self.chain_code, self.pub + i.to_bytes(4, "big"), hashlib.sha512).digest()
        il = int.from_bytes(h[:32], "big")
        if il >= _N:
            return None
        k = _add(_mul(il, _G), _decode(self.pub))
        if k is None:
            return None
        return ExtKey(self.version, self.depth + 1, self.fingerprint, i, h[32:], _encode(k))


def parse(s: str) -> ExtKey | None:
    """A checked extended public key, or None (bad checksum, wrong length, unknown version, off the curve)."""
    raw = b58decode(s.strip())
    if raw is None or len(raw) != 82 or not b58check_ok(raw):
        return None
    version, depth, fp, child = struct.unpack(">IBII", raw[:13])
    if version not in KINDS:
        return None
    k = ExtKey(version, depth, fp, child, raw[13:45], raw[45:78])
    try:
        _decode(k.pub)
    except ValueError:
        return None
    return k


def display(k: ExtKey, chain: str, script: str) -> str:
    """The account key in the form Ledger Live and Sparrow show for ``script`` on ``chain``."""
    if chain == "litecoin":
        v = MTUB if script == "sh-wpkh" else ZPUB if script == "wpkh" else LTUB
    else:
        v = YPUB if script == "sh-wpkh" else ZPUB if script == "wpkh" else XPUB
    return k.serialize(v)


def encode_segwit(hrp: str, version: int, program: bytes) -> str:
    data = [version]
    acc = bits = 0
    for b in program:
        acc = (acc << 8) | b
        bits += 8
        while bits >= 5:
            bits -= 5
            data.append((acc >> bits) & 31)
    if bits:
        data.append((acc << (5 - bits)) & 31)
    const = 1 if version == 0 else _BECH32M
    pm = _polymod([ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + data + [0] * 6) ^ const
    return hrp + "1" + "".join(_CHARSET[d] for d in data + [(pm >> (5 * (5 - i))) & 31 for i in range(6)])


def tagged_hash(tag: str, msg: bytes) -> bytes:
    th = sha256(tag.encode())
    return sha256(th + th + msg)


def taproot_output(pub: bytes) -> bytes:
    """BIP-86: the internal key tweaked by its own tagged hash (no script path)."""
    x = pub[1:33]
    p = _decode(b"\x02" + x)  # the even-y lift of the x-only key
    t = int.from_bytes(tagged_hash("TapTweak", x), "big")
    if t >= _N:
        raise ValueError("tweak")
    q = _add(p, _mul(t, _G))
    assert q is not None
    return q[0].to_bytes(32, "big")


def address_of(pub: bytes, chain: str, script: str) -> str:
    ltc = chain == "litecoin"
    if script == "pkh":
        return b58encode_check(bytes([0x30 if ltc else 0x00]) + hash160(pub))
    if script == "sh-wpkh":
        return b58encode_check(bytes([0x32 if ltc else 0x05]) + hash160(b"\x00\x14" + hash160(pub)))
    if script == "wpkh":
        return encode_segwit("ltc" if ltc else "bc", 0, hash160(pub))
    if script == "tr":
        if ltc:
            raise ValueError("Taproot is Bitcoin only")
        return encode_segwit("bc", 1, taproot_output(pub))
    raise ValueError(f"script {script}")


def address(account: ExtKey, chain: str, script: str, change: int, index: int) -> str | None:
    node = account.child(change)
    k = node.child(index) if node else None
    return address_of(k.pub, chain, script) if k else None


def parse_path(path: str) -> list[int]:
    out = []
    for p in path.strip().removeprefix("m").strip("/").split("/"):
        if not p:
            continue
        hard = p.endswith(("'", "h"))
        n = int(p.rstrip("'h"))
        if not 0 <= n < 0x80000000:
            raise ValueError("path")
        out.append(n | 0x80000000 if hard else n)
    return out


def path_str(path: list[int]) -> str:
    return "m" + "".join(f"/{i & 0x7FFFFFFF}{chr(39) if i & 0x80000000 else ''}" for i in path)


# ── RIPEMD-160 (only when OpenSSL's legacy provider is not loaded) ──────────

_RL = [
    *range(16),
    7,
    4,
    13,
    1,
    10,
    6,
    15,
    3,
    12,
    0,
    9,
    5,
    2,
    14,
    11,
    8,
    3,
    10,
    14,
    4,
    9,
    15,
    8,
    1,
    2,
    7,
    0,
    6,
    13,
    11,
    5,
    12,
    1,
    9,
    11,
    10,
    0,
    8,
    12,
    4,
    13,
    3,
    7,
    15,
    14,
    5,
    6,
    2,
    4,
    0,
    5,
    9,
    7,
    12,
    2,
    10,
    14,
    1,
    3,
    8,
    11,
    6,
    15,
    13,
]
_RR = [
    5,
    14,
    7,
    0,
    9,
    2,
    11,
    4,
    13,
    6,
    15,
    8,
    1,
    10,
    3,
    12,
    6,
    11,
    3,
    7,
    0,
    13,
    5,
    10,
    14,
    15,
    8,
    12,
    4,
    9,
    1,
    2,
    15,
    5,
    1,
    3,
    7,
    14,
    6,
    9,
    11,
    8,
    12,
    2,
    10,
    0,
    4,
    13,
    8,
    6,
    4,
    1,
    3,
    11,
    15,
    0,
    5,
    12,
    2,
    13,
    9,
    7,
    10,
    14,
    12,
    15,
    10,
    4,
    1,
    5,
    8,
    7,
    6,
    2,
    13,
    14,
    0,
    3,
    9,
    11,
]
_SL = [
    11,
    14,
    15,
    12,
    5,
    8,
    7,
    9,
    11,
    13,
    14,
    15,
    6,
    7,
    9,
    8,
    7,
    6,
    8,
    13,
    11,
    9,
    7,
    15,
    7,
    12,
    15,
    9,
    11,
    7,
    13,
    12,
    11,
    13,
    6,
    7,
    14,
    9,
    13,
    15,
    14,
    8,
    13,
    6,
    5,
    12,
    7,
    5,
    11,
    12,
    14,
    15,
    14,
    15,
    9,
    8,
    9,
    14,
    5,
    6,
    8,
    6,
    5,
    12,
    9,
    15,
    5,
    11,
    6,
    8,
    13,
    12,
    5,
    12,
    13,
    14,
    11,
    8,
    5,
    6,
]
_SR = [
    8,
    9,
    9,
    11,
    13,
    15,
    15,
    5,
    7,
    7,
    8,
    11,
    14,
    14,
    12,
    6,
    9,
    13,
    15,
    7,
    12,
    8,
    9,
    11,
    7,
    7,
    12,
    7,
    6,
    15,
    13,
    11,
    9,
    7,
    15,
    11,
    8,
    6,
    6,
    14,
    12,
    13,
    5,
    14,
    13,
    13,
    7,
    5,
    15,
    5,
    8,
    11,
    14,
    14,
    6,
    14,
    6,
    9,
    12,
    9,
    12,
    5,
    15,
    8,
    8,
    5,
    12,
    9,
    12,
    5,
    14,
    6,
    8,
    13,
    6,
    5,
    15,
    13,
    11,
    11,
]
_KL = (0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E)
_KR = (0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000)


def _f(j: int, x: int, y: int, z: int) -> int:
    if j < 16:
        return x ^ y ^ z
    if j < 32:
        return (x & y) | (~x & z)
    if j < 48:
        return (x | ~y) ^ z
    if j < 64:
        return (x & z) | (y & ~z)
    return x ^ (y | ~z)


def _rol(x: int, n: int) -> int:
    x &= 0xFFFFFFFF
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _ripemd160(msg: bytes) -> bytes:
    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
    m = msg + b"\x80" + b"\x00" * ((55 - len(msg)) % 64) + struct.pack("<Q", len(msg) * 8)
    for off in range(0, len(m), 64):
        x = struct.unpack("<16I", m[off : off + 64])
        al, bl, cl, dl, el = h
        ar, br, cr, dr, er = h
        for j in range(80):
            t = _rol(al + _f(j, bl, cl, dl) + x[_RL[j]] + _KL[j // 16], _SL[j]) + el
            al, el, dl, cl, bl = el, dl, _rol(cl, 10), bl, t & 0xFFFFFFFF
            t = _rol(ar + _f(79 - j, br, cr, dr) + x[_RR[j]] + _KR[j // 16], _SR[j]) + er
            ar, er, dr, cr, br = er, dr, _rol(cr, 10), br, t & 0xFFFFFFFF
        t = (h[1] + cl + dr) & 0xFFFFFFFF
        h[1] = (h[2] + dl + er) & 0xFFFFFFFF
        h[2] = (h[3] + el + ar) & 0xFFFFFFFF
        h[3] = (h[4] + al + br) & 0xFFFFFFFF
        h[4] = (h[0] + bl + cr) & 0xFFFFFFFF
        h[0] = t
    return struct.pack("<5I", *h)
