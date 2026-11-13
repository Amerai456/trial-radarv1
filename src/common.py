"""Small shared definitions used by both the pipeline and the dashboard.

This file deliberately imports nothing from the pipeline (no yfinance,
requests, rapidfuzz...), so the dashboard can use it without loading the
whole data pipeline. That keeps the deployed app small and fast to start.
"""

import math

import pandas as pd

# Trial statuses that count as "open" (the trial hasn't finished yet).
OPEN_STATUSES = ["RECRUITING", "ACTIVE_NOT_RECRUITING", "NOT_YET_RECRUITING",
                 "ENROLLING_BY_INVITATION"]

# Company size groups used in the event study and the dashboard.
SIZE_GROUPS = [(2e9, "small (<$2B)"), (20e9, "mid ($2-20B)"), (math.inf, "large (>$20B)")]


def size_group(market_cap):
    """Label a market cap as small / mid / large. None if unknown."""
    if market_cap is None or pd.isna(market_cap):
        return None
    return next(label for limit, label in SIZE_GROUPS if market_cap < limit)
