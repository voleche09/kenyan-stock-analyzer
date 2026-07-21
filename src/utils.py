"""
Shared utilities: retry decorator, helpers.
"""

import time
import functools
import logging
from typing import Tuple, List

logger = logging.getLogger(__name__)


def retry(max_attempts=3, backoff=2, exceptions=(Exception,)):
    """
    Decorator that retries a function on failure with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts (including the first).
        backoff: Multiplier for sleep between retries (seconds).
        exceptions: Tuple of exception types to catch.

    Usage:
        @retry(max_attempts=3, backoff=2)
        def flaky_network_call():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        wait = backoff ** (attempt - 1)
                        logger.warning(
                            f"{func.__name__} attempt {attempt}/{max_attempts} "
                            f"failed: {e}. Retrying in {wait}s..."
                        )
                        time.sleep(wait)
                    else:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} "
                            f"attempts: {e}"
                        )
            raise last_exc
        return wrapper
    return decorator


def safe_float(value, default=0.0):
    """Convert a value to float, returning default on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def detect_support_resistance(
    prices, window=20, threshold=0.02
) -> Tuple[List[float], List[float]]:
    """
    Detect support and resistance levels from a price series.

    Args:
        prices: Array-like of closing prices.
        window: Rolling window size for finding local extrema.
        threshold: Percentage threshold for clustering nearby levels.

    Returns:
        Tuple of (support_levels, resistance_levels).
    """
    import numpy as np
    prices = np.asarray(prices)

    supports = []
    resistances = []

    for i in range(window, len(prices) - window):
        # Local minimum (support)
        if all(prices[i] <= prices[i - window:i]) and \
           all(prices[i] <= prices[i + 1:i + window + 1]):
            supports.append(float(prices[i]))

        # Local maximum (resistance)
        if all(prices[i] >= prices[i - window:i]) and \
           all(prices[i] >= prices[i + 1:i + window + 1]):
            resistances.append(float(prices[i]))

    # Cluster nearby levels
    supports = _cluster_levels(supports, threshold)
    resistances = _cluster_levels(resistances, threshold)

    return supports, resistances


def _cluster_levels(levels, threshold):
    """Cluster nearby price levels together."""
    if not levels:
        return []
    levels = sorted(set(levels))
    clusters = []
    current_cluster = [levels[0]]
    for level in levels[1:]:
        if abs(level - current_cluster[-1]) / current_cluster[-1] < threshold:
            current_cluster.append(level)
        else:
            clusters.append(sum(current_cluster) / len(current_cluster))
            current_cluster = [level]
    clusters.append(sum(current_cluster) / len(current_cluster))
    return [round(c, 2) for c in clusters]