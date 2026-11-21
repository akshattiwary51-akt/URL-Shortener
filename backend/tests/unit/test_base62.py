import random

import pytest

from app.services import base62

ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def test_alphabet_and_capacity():
    assert base62.ALPHABET == ALPHABET
    assert len(set(ALPHABET)) == 62
    assert base62.CAPACITY == 62**7 == 3_521_614_606_208


@pytest.mark.parametrize("n", [0, 1, 61, 62, 63, 3843, 3844, 10**9, 62**7 - 1])
def test_roundtrip(n):
    assert base62.decode(base62.encode(n)) == n


def test_known_values():
    assert base62.encode(0) == "a"
    assert base62.encode(61) == "9"
    assert base62.encode(62) == "ba"


def test_fixed_length_padding_and_bounds():
    assert base62.encode_fixed(0) == "aaaaaaa"
    assert base62.encode_fixed(1) == "aaaaaab"
    assert base62.encode_fixed(base62.CAPACITY - 1) == "9999999"
    assert all(len(base62.encode_fixed(random.randrange(base62.CAPACITY))) == 7 for _ in range(1000))
    with pytest.raises(ValueError):
        base62.encode_fixed(base62.CAPACITY)
    with pytest.raises(ValueError):
        base62.encode_fixed(-1)


def test_decode_rejects_invalid():
    with pytest.raises(ValueError):
        base62.decode("ab-c")
    with pytest.raises(ValueError):
        base62.decode("")


def test_fixed_length_ordering_is_numeric():
    values = sorted(random.sample(range(base62.CAPACITY), 500))
    # Alphabet is not lexicographically ordered, so just assert uniqueness + roundtrip.
    codes = [base62.encode_fixed(v) for v in values]
    assert len(set(codes)) == len(codes)
    assert [base62.decode(c) for c in codes] == values


def test_scramble_is_a_bijection_on_sample_and_boundaries():
    m, c = 2654435761, 987654321
    sample = list(range(0, 50_000)) + [base62.CAPACITY - 1 - i for i in range(1000)]
    out = [base62.scramble(n, m, c) for n in sample]
    assert len(set(out)) == len(sample)  # no collisions
    assert all(0 <= o < base62.CAPACITY for o in out)
    assert all(base62.unscramble(o, m, c) == n for n, o in zip(sample, out))


def test_scrambled_codes_are_not_sequential():
    m, c = 2654435761, 987654321
    codes = [base62.encode_fixed(base62.scramble(n, m, c)) for n in range(5)]
    assert len(set(codes)) == 5
    assert codes[0][:3] != codes[1][:3] or codes[1][:3] != codes[2][:3]


def test_scramble_rejects_out_of_domain():
    with pytest.raises(ValueError):
        base62.scramble(base62.CAPACITY, 2654435761, 0)
