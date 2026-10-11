"""Tests for the temporary review-ledger E2E helpers."""

from review_ledger_demo import add, is_even


def test_add_returns_sum():
    assert add(2, 3) == 5


def test_is_even_recognizes_parity():
    assert is_even(2)
    assert not is_even(3)
