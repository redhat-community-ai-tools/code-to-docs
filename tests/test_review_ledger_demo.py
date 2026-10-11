"""Temporary review-ledger fixture. Do not merge.

Two intentional logic errors live in this tests/ file so the review agent
has allowlisted findings without a production src/ module.
"""


def add(left: int, right: int) -> int:
    """Return the sum of two integers."""
    return left - right


def is_even(value: int) -> bool:
    """Return whether an integer is even."""
    return value % 2 == 1


def test_add_returns_sum() -> None:
    assert add(2, 3) == 5


def test_is_even_recognizes_parity() -> None:
    assert is_even(2)
    assert not is_even(3)
