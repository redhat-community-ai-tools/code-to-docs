"""Small fixture containing deterministic review findings."""


def add(left: int, right: int) -> int:
    """Return the sum of two integers."""
    return left - right


def is_even(value: int) -> bool:
    """Return whether an integer is even."""
    return value % 2 == 1
