"""Public wallet addresses: recognising, checking and normalising what the user
pastes — and refusing anything that is a secret.

Every format is checked by its own checksum (Base58Check, bech32/bech32m,
EIP-55, Monero's Keccak checksum), so a typo or a truncated paste is caught
before an explorer is ever asked about it. Recovery phrases, private keys and
extended keys are recognised and rejected; the caller must drop the text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Networks that use Ethereum-style 0x addresses; Hyperliquid (HyperCore + HyperEVM) included.
EVM_CHAINS = ("ethereum", "base", "arbitrum", "optimism", "polygon", "bsc", "avalanche", "hyperliquid")

_SCHEMES = {"bitcoin", "litecoin", "ethereum", "solana", "tron", "monero"}
_HEX40 = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HEX64 = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
_WORD = re.compile(r"^[a-zA-Z]{2,12}$")
_XPRV = ("xprv", "yprv", "zprv", "tprv", "uprv", "vprv", "ltpv", "ttpv")
_XPUB = ("xpub", "ypub", "zpub", "ltub", "mtub")
_TPUB = ("tpub", "upub", "vpub")


@dataclass(frozen=True, slots=True)
class Ok:
    """A valid public address; ``chains`` are the networks it can belong to, most likely first."""

    address: str
    chains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Secret:
    """``seed``, ``private_key`` or ``xprv``. Never store, never send, clear the field."""

    kind: str


@dataclass(frozen=True, slots=True)
class Account:
    """A watch-only HD account key (xpub / ypub / zpub / Ltub / Mtub).

    ``script`` is None when the version does not say (a plain xpub can be BIP-44
    legacy or BIP-86 Taproot): ask.
    """

    xpub: str
    chains: tuple[str, ...]
    script: str | None


@dataclass(frozen=True, slots=True)
class Bad:
    """Not usable: ``empty``, ``testnet``, ``checksum`` or ``unknown``."""

    reason: str


Result = Ok | Secret | Account | Bad


def clean(text: str) -> str:
    """Strip a payment-URI wrapper (bitcoin:…?amount=, ethereum:0x…@1/transfer) and stray whitespace."""
    s = text.strip().strip("\"'<>")
    scheme, sep, rest = s.partition(":")
    if sep and scheme.lower() in _SCHEMES:
        s = rest.removeprefix("//")
        for ch in "?@/":
            s = s.split(ch, 1)[0]
    return s.strip()


def secret_kind(text: str) -> str | None:
    """Does ``text`` look like a secret? Checked on the raw text, before anything else."""
    words = text.split()
    # BIP-39 phrases have 12-24 words, Monero's 25 (or 13 for MyMonero); be generous.
    if len(words) >= 11 and sum(1 for w in words if _WORD.match(w)) >= len(words) - 1:
        return "seed"
    s = clean(text)
    if _HEX64.match(s):
        return "private_key"
    if s.lower().startswith(_XPRV):
        return "xprv"
    if s.startswith("[") and s.endswith("]"):
        # A Solana keypair exported as a JSON byte array.
        nums = [x.strip() for x in s[1:-1].split(",")]
        if len(nums) >= 32 and all(n.isdigit() and int(n) <= 255 for n in nums):
            return "private_key"
    b = b58decode(s)
    if b is not None:
        # WIF private keys (Bitcoin 0x80, Litecoin 0xB0, testnet 0xEF), with a valid checksum.
        if len(b) in (37, 38) and b58check_ok(b) and b[0] in (0x80, 0xB0, 0xEF):
            return "private_key"
        if len(b) == 64:  # a Solana secret key (seed + public key)
            return "private_key"
    return None


def parse(text: str) -> Result:
    if not text or not text.strip():
        return Bad("empty")
    kind = secret_kind(text)
    if kind:
        return Secret(kind)
    s = clean(text)
    if not s or len(s) > 120 or any(c.isspace() for c in s):
        return Bad("unknown")
    lower = s.lower()
    if lower.startswith(_TPUB):
        return Bad("testnet")
    if lower.startswith(_XPUB):
        from mazecrypto.core import hd

        k = hd.parse(s)
        if k is None:
            return Bad("checksum")
        chains, script = hd.KINDS[k.version]
        return Account(s, chains, script)

    # EVM: 0x + 40 hex. Mixed case must carry a valid EIP-55 checksum.
    if _HEX40.match(s):
        body = s[2:]
        mixed = any(c.isupper() for c in body) and any(c.islower() for c in body)
        if mixed and eip55(body.lower()) != s:
            return Bad("checksum")
        return Ok(eip55(body.lower()), EVM_CHAINS)

    if lower.startswith(("tb1", "bcrt1", "tltc1", "rltc1")):
        return Bad("testnet")
    if lower.startswith(("bc1", "ltc1")):
        hrp = "bc" if lower.startswith("bc1") else "ltc"
        if s != lower and s != s.upper():
            return Bad("checksum")  # bech32 is never mixed case
        if segwit_ok(lower, hrp):
            return Ok(lower, ("bitcoin",) if hrp == "bc" else ("litecoin",))
        return Bad("checksum")

    # Monero: its own base58 flavour; standard (4…), subaddress (8…), integrated (4…, 106 chars).
    if len(s) in (95, 106) and s[0] in "48" and all(c in B58 for c in s):
        return Ok(s, ("monero",)) if monero_ok(s) else Bad("checksum")

    b = b58decode(s)
    if b is None:
        return Bad("unknown")
    if len(b) == 25:
        if not b58check_ok(b):
            return Bad("checksum")
        return {
            0x00: Ok(s, ("bitcoin",)),
            0x05: Ok(s, ("bitcoin", "litecoin")),  # P2SH "3…": used by both
            0x30: Ok(s, ("litecoin",)),
            0x32: Ok(s, ("litecoin",)),
            0x41: Ok(s, ("tron",)),
            0x6F: Bad("testnet"),
            0xC4: Bad("testnet"),
            0x3A: Bad("testnet"),
        }.get(b[0], Bad("unknown"))
    if len(b) == 32 and 32 <= len(s) <= 44:
        return Ok(s, ("solana",))
    return Bad("unknown")


def key(chain: str, address: str) -> str:
    """The same address in a form that compares equal however it was typed."""
    if chain in EVM_CHAINS or address.lower().startswith(("bc1", "ltc1")):
        return f"{chain}:{address.lower()}"
    return f"{chain}:{address}"


def short(address: str, head: int = 6, tail: int = 4) -> str:
    """First and last characters: enough to recognise, short enough for a row."""
    if len(address) <= head + tail + 1:
        return address
    return address[:head] + "…" + (address[-tail:] if tail else "")


# ── EIP-55 ─────────────────────────────────────────────────────────────────


def eip55(lower_hex: str) -> str:
    """Mixed-case checksum encoding of a lowercase 40-hex address."""
    h = keccak256(lower_hex.encode("ascii")).hex()
    return "0x" + "".join(c.upper() if c.isalpha() and int(h[i], 16) >= 8 else c for i, c in enumerate(lower_hex))


# ── Monero ─────────────────────────────────────────────────────────────────


def monero_ok(s: str) -> bool:
    data = b58decode_monero(s)
    if data is None or len(data) not in (69, 77):
        return False
    prefix = data[0]
    # 18 standard, 19 integrated, 42 subaddress (mainnet).
    if prefix not in (18, 19, 42) or (prefix == 19) != (len(data) == 77):
        return False
    return keccak256(data[:-4])[:4] == data[-4:]


# ── TRON hex ↔ base58 ──────────────────────────────────────────────────────


def tron_from_hex(hex_addr: str) -> str | None:
    """TronGrid reports addresses as 41-prefixed hex; show them the way wallets do (T…)."""
    h = hex_addr.removeprefix("0x")
    if len(h) != 42 or not h.startswith("41") or any(c not in "0123456789abcdefABCDEF" for c in h):
        return None
    return b58encode_check(bytes.fromhex(h))


# ── Base58 ─────────────────────────────────────────────────────────────────

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(B58)}


def b58decode(s: str) -> bytes | None:
    if not s or len(s) > 120:
        return None
    n = 0
    for c in s:
        d = _B58_INDEX.get(c)
        if d is None:
            return None
        n = n * 58 + d
    zeros = len(s) - len(s.lstrip("1"))
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * zeros + body


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = []
    while n:
        n, r = divmod(n, 58)
        out.append(B58[r])
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + "".join(reversed(out))


def _sha256d(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def b58check_ok(b: bytes) -> bool:
    return len(b) >= 5 and _sha256d(b[:-4])[:4] == b[-4:]


def b58encode_check(payload: bytes) -> str:
    return b58encode(payload + _sha256d(payload)[:4])


# Monero splits data into 8-byte blocks of 11 characters; a short last block uses fewer.
_BLOCK_CHARS = (0, 2, 3, 5, 6, 7, 9, 10, 11)


def b58decode_monero(s: str) -> bytes | None:
    out = bytearray()
    for i in range(0, len(s), 11):
        chunk = s[i : i + 11]
        if len(chunk) not in _BLOCK_CHARS[1:]:
            return None
        size = _BLOCK_CHARS.index(len(chunk))
        n = 0
        for c in chunk:
            d = _B58_INDEX.get(c)
            if d is None:
                return None
            n = n * 58 + d
        if n.bit_length() > size * 8:
            return None
        out += n.to_bytes(size, "big")
    return bytes(out)


def b58encode_monero(data: bytes) -> str:
    out = []
    for i in range(0, len(data), 8):
        block = data[i : i + 8]
        n = int.from_bytes(block, "big")
        chars = []
        for _ in range(_BLOCK_CHARS[len(block)]):
            n, r = divmod(n, 58)
            chars.append(B58[r])
        out.append("".join(reversed(chars)))
    return "".join(out)


# ── bech32 / bech32m ───────────────────────────────────────────────────────

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_GEN = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
_BECH32M = 0x2BC830A3


def _polymod(values: list[int]) -> int:
    chk = 1
    for v in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            if (top >> i) & 1:
                chk ^= _GEN[i]
    return chk


def segwit_ok(addr: str, hrp: str) -> bool:
    """A lowercase segwit address for ``hrp``: right checksum flavour, witness version and program length."""
    if len(addr) > 90:
        return False
    pos = addr.rfind("1")
    if pos != len(hrp) or addr[:pos] != hrp or len(addr) - pos - 1 < 6:
        return False
    data = [_CHARSET.find(c) for c in addr[pos + 1 :]]
    if any(d < 0 for d in data):
        return False
    chk = _polymod([ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + data)
    version = data[0]
    if version > 16 or (version == 0 and chk != 1) or (version > 0 and chk != _BECH32M):
        return False
    acc = bits = 0
    prog = []
    for v in data[1:-6]:
        acc = (acc << 5) | v
        bits += 5
        while bits >= 8:
            bits -= 8
            prog.append((acc >> bits) & 0xFF)
    if bits >= 5 or (acc << (8 - bits)) & 0xFF:
        return False
    if not 2 <= len(prog) <= 40:
        return False
    return not (version == 0 and len(prog) not in (20, 32))


# ── Keccak-256 (Ethereum/Monero: original padding, not NIST SHA3-256) ──────

_RC = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)
_ROT = (0, 1, 62, 28, 27, 36, 44, 6, 55, 20, 3, 10, 43, 25, 39, 41, 45, 15, 21, 8, 18, 2, 61, 56, 14)
_M64 = (1 << 64) - 1
_RATE = 136


def _rotl(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _M64 if n else x


def _permute(a: list[int]) -> None:
    for rc in _RC:
        c = [a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20] for x in range(5)]
        for x in range(5):
            d = c[(x + 4) % 5] ^ _rotl(c[(x + 1) % 5], 1)
            for y in range(0, 25, 5):
                a[x + y] ^= d
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl(a[x + 5 * y], _ROT[x + 5 * y])
        for x in range(5):
            for y in range(5):
                a[x + 5 * y] = b[x + 5 * y] ^ (~b[(x + 1) % 5 + 5 * y] & b[(x + 2) % 5 + 5 * y] & _M64)
        a[0] ^= rc


def keccak256(data: bytes) -> bytes:
    padded = bytearray(data) + bytearray((len(data) // _RATE + 1) * _RATE - len(data))
    padded[len(data)] ^= 0x01
    padded[-1] ^= 0x80
    state = [0] * 25
    for off in range(0, len(padded), _RATE):
        for i in range(_RATE // 8):
            state[i] ^= int.from_bytes(padded[off + 8 * i : off + 8 * i + 8], "little")
        _permute(state)
    return b"".join(lane.to_bytes(8, "little") for lane in state[:4])
