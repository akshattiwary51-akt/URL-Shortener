"""Base62 codec and the bijective scrambler used for generated short codes."""

from __future__ import annotations

ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
BASE = len(ALPHABET)  # 62
CODE_LENGTH = 7
CAPACITY = BASE**CODE_LENGTH  # 3_521_614_606_208 (~3.52 trillion)

_INDEX = {ch: i for i, ch in enumerate(ALPHABET)}


def encode(n: int) -> str:
    """Variable-length Base62 (``0`` encodes to ``"a"``)."""
    if n < 0:
        raise ValueError("cannot encode a negative number")
    if n == 0:
        return ALPHABET[0]
    out: list[str] = []
    while n:
        n, rem = divmod(n, BASE)
        out.append(ALPHABET[rem])
    return "".join(reversed(out))


def encode_fixed(n: int, length: int = CODE_LENGTH) -> str:
    """Fixed-length, left-padded with the zero digit. Raises if ``n`` does not fit."""
    if n < 0 or n >= BASE**length:
        raise ValueError(f"{n} is outside the {length}-character Base62 range")
    return encode(n).rjust(length, ALPHABET[0])


def decode(code: str) -> int:
    if not code:
        raise ValueError("empty code")
    n = 0
    for ch in code:
        try:
            n = n * BASE + _INDEX[ch]
        except KeyError as exc:
            raise ValueError(f"invalid Base62 character {ch!r}") from exc
    return n


def scramble(n: int, multiplier: int, offset: int, modulus: int = CAPACITY) -> int:
    """Bijection on ``[0, modulus)``: ``(n * multiplier + offset) mod modulus``.

    It is a permutation iff ``gcd(multiplier, modulus) == 1`` (guaranteed by
    ``Settings`` because ``modulus`` is a power of 62). Distinct inputs therefore
    always produce distinct outputs, so scrambling cannot create collisions.
    """
    if not 0 <= n < modulus:
        raise ValueError("value outside permutation domain")
    return (n * multiplier + offset) % modulus


def unscramble(m: int, multiplier: int, offset: int, modulus: int = CAPACITY) -> int:
    return ((m - offset) * pow(multiplier, -1, modulus)) % modulus
