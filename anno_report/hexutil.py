"""Decode the hex leaves FileDBReader leaves undecoded (little-endian)."""
from __future__ import annotations

import struct


def is_hex(s: str | None) -> bool:
    return bool(s) and len(s) % 2 == 0 and all(c in "0123456789ABCDEFabcdef" for c in s)


def h2i(s: str | None, signed: bool = True, default: int | None = None) -> int | None:
    """Hex string -> int. Decimal strings pass through (the rule file already decoded them)."""
    if s is None or s == "":
        return default
    s = s.strip()
    if s.lstrip("-").isdigit():
        # Ambiguous for all-digit hex blobs ("1000" is 16 as u16 hex). Use this only for fields the
        # rule file already decoded to decimal; use h2i_hex / hexf for raw fields.
        return int(s)
    if is_hex(s):
        return int.from_bytes(bytes.fromhex(s), "little", signed=signed)
    return default


def h2i_hex(s: str | None, signed: bool = True, default: int | None = None) -> int | None:
    """Hex string -> int, always treating the text as little-endian hex (e.g. Owner/id '1800' -> 24)."""
    if not s:
        return default
    s = s.strip()
    if not is_hex(s):
        return int(s) if s.lstrip("-").isdigit() else default
    return int.from_bytes(bytes.fromhex(s), "little", signed=signed)


def h2f(s: str | None, default: float | None = None) -> float | None:
    if s is None or s == "":
        return default
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        pass
    if is_hex(s) and len(s) == 8:
        return struct.unpack("<f", bytes.fromhex(s))[0]
    return default


def hexf(s: str | None, default: float | None = None) -> float | None:
    """Raw little-endian float32 hex -> float. Never reads the text as decimal ('00008040' is 4.0, not 8040)."""
    if not s:
        return default
    s = s.strip()
    if is_hex(s) and len(s) == 8:
        return struct.unpack("<f", bytes.fromhex(s))[0]
    return default


def pairs_i32(s: str | None) -> list[tuple[int, int]]:
    """'StrgLrg' blobs: a struct-of-arrays map. All u32 keys come first, then all i32 values.

    With one entry this looks like a (guid, amount) pair; with two entries it is
    (guid1, guid2, amount1, amount2). Observed 2026-10-31: 61690F00 AC000000 5A450000 F6FFFFFF
    = keys [1010017 (Coins), 172], values [17754, -10].
    """
    if not s or not is_hex(s):
        return []
    b = bytes.fromhex(s)
    n = len(b) // 8
    if n == 0:
        return []
    keys = struct.unpack_from(f"<{n}I", b, 0)
    vals = struct.unpack_from(f"<{n}i", b, 4 * n)
    return list(zip(keys, vals))


def utf16(s: str | None) -> str:
    if not s or not is_hex(s):
        return s or ""
    return bytes.fromhex(s).decode("utf-16-le", errors="replace").rstrip("\x00")
