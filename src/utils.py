"""Shared utilities for code-to-docs modules."""

import time
from functools import wraps


def retry_with_backoff(
    max_retries=3,
    delay_multiplier=3,
    on_retry=None,
    default=None,
    reraise=False,
):
    """
    Decorator that retries a function on exception with linear backoff.

    Args:
        max_retries: Maximum number of attempts (default: 3)
        delay_multiplier: Multiplied by (attempt+1) for wait time in seconds
        on_retry: Optional callback(attempt, max_retries, exception, wait_time) for logging
        default: Value to return if all retries are exhausted (ignored if reraise=True)
        reraise: If True, re-raise the last exception instead of returning default

    Returns:
        Decorated function with retry logic
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    wait_time = calc_backoff_delay(attempt, delay_multiplier)
                    if on_retry:
                        on_retry(attempt, max_retries, e, wait_time)
                    if attempt < max_retries - 1:
                        time.sleep(wait_time)
            if reraise and last_exception:
                raise last_exception
            return default
        return wrapper
    return decorator


def calc_backoff_delay(attempt, multiplier=3):
    """Calculate linear backoff delay: (attempt + 1) * multiplier seconds."""
    return (attempt + 1) * multiplier
