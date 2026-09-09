"""Unit tests for shared helper utilities."""

import time

import pytest

from utils.helpers import format_pnl, format_price, safe_divide, timestamp_ms


def test_timestamp_ms_is_monotonic():
    before = timestamp_ms()
    time.sleep(0.005)
    after = timestamp_ms()
    assert after >= before


@pytest.mark.parametrize(
    ("price", "decimals", "expected"),
    [
        (123.45000000, 8, "123.45"),
        (123.00000000, 8, "123"),
        (0.00012345, 8, "0.00012345"),
        (123.456789, 2, "123.46"),
    ],
)
def test_format_price_trims_trailing_zeros(price, decimals, expected):
    assert format_price(price, decimals) == expected


@pytest.mark.parametrize(
    ("pnl", "expected"),
    [
        (5.5, "+5.50%"),
        (0.0, "+0.00%"),
        (-3.25, "-3.25%"),
    ],
)
def test_format_pnl_includes_explicit_sign(pnl, expected):
    assert format_pnl(pnl) == expected


@pytest.mark.parametrize(
    ("a", "b", "default", "expected"),
    [
        (10.0, 2.0, 0.0, 5.0),
        (10.0, 0.0, 0.0, 0.0),
        (10.0, 0.0, -1.0, -1.0),
    ],
)
def test_safe_divide_handles_zero_divisor(a, b, default, expected):
    assert safe_divide(a, b, default) == expected
