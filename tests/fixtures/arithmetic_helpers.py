"""Small integer helpers used as a review-gate fixture."""


def add(left: int, right: int) -> int:
    """Return the sum of two integers."""
    return left + right


def is_even(value: int) -> bool:
    """Return whether an integer is even."""
    return value % 2 == 0


def is_positive(value: int) -> bool:
    """Return whether an integer is positive."""
    return value > 0


def is_negative(value: int) -> bool:
    """Return whether an integer is negative."""
    return value < 0
