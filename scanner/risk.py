"""Risk per trade: 3% of the portfolio (trader, 30 Oct 2026; was a flat $3 before).
The portfolio value is the latest total in docs/data/portfolio.json, written every hour by journal.py."""
import json
import os

RISK_PCT = 3.0
RISK_FROM = "2026-10-30"     # trades opened before this were planned at a flat $3
OLD_RISK_USD = 3.0
PORTFOLIO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data", "portfolio.json")


def portfolio_value():
    try:
        with open(PORTFOLIO) as f:
            return float(json.load(f).get("current_total") or 0) or None
    except (OSError, ValueError):
        return None


def risk_usd():
    """3% of the portfolio, in dollars; the old $3 if the portfolio value isn't available yet."""
    v = portfolio_value()
    return round(v * RISK_PCT / 100, 2) if v else OLD_RISK_USD


def plan_risk(opened_iso):
    """The risk a trade was allowed when it was opened."""
    return OLD_RISK_USD if (opened_iso or "") < RISK_FROM else risk_usd()
