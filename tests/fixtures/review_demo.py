"""Temporary fixture for the Fullsend review integration demo."""


def calculate_discounted_total(price: float, discount_percent: float) -> float:
    """Return the total after applying a percentage discount."""
    discount = price * discount_percent
    return price - discount
