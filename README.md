# Setup Desk

Hourly EMA setup scanner for the top 100 Hyperliquid perps (the coins you trade on trade.xyz).

- **Scanner** (`scanner/scan.py`) runs on GitHub Actions every hour. It pulls daily candles from Hyperliquid's free public API, builds weekly candles, and computes 25/50/100 EMAs on both.
- **Setups are built on weekly levels** (weekly 25/50/100 EMAs and weekly swing highs/lows) and only in the direction the weekly trend allows:
  - **Weekly support long:** price above the weekly 50 EMA; entry at the nearest weekly level below price.
  - **Weekly reclaim long:** last week closed back above the weekly 50 or 100 EMA; entry on the retest.
  - **Weekly rejection short:** price below the weekly 100 EMA with the 25 under the 50; entry at the nearest weekly level above price.
  - The daily chart times the entry: **confirmed** when price tested the level in the last 3 days and closed back on the right side; a daily close well through the level drops the setup.
  - **Tranches:** entries scale into the zone (30% at the near edge, 30% at the middle, 40% at the far edge); exits take half at target 1 and half at target 2. Reward-to-risk is blended across both exits from the average entry; longs need 2.5R, shorts 2R.
  - **Stops sit at structure:** beyond the zone, and beyond any weekly level sitting just past it, so the stop is never parked in front of the next support or supply shelf. Skipped if more than 15% from the average entry.
  - Targets are the next weekly zones (or the all-time high/low). The first entry must be within 10% of price.
  - **Rating:** A = another weekly level plus one more level near the entry, B = one other level, C = none.
- **Telegram:** only the top 8 setups (ranked by rating, confirmation, R and distance) alert, once when price reaches the entry and once when the retest is confirmed, at most 3 alerts per scan. Each alert includes the position size for your dollar risk (RISK_USD, default $2).
- **Dashboard:** `docs/index.html`, served by GitHub Pages and refreshed every hour.
- **News and unlocks:** a daily Claude scheduled task writes `docs/data/intel.json`.

## Settings

Edit the constants at the top of `scanner/scan.py`: `TOP_N`, `MIN_RR`, `MAX_DIST` (how far an entry may be from price), `AT_LEVEL`.

## Notes

- EMAs use Hyperliquid prices, so they can differ slightly from a Binance chart, especially weekly EMAs on coins listed on Hyperliquid later than on Binance.
- GitHub can delay hourly runs by a few minutes at busy times.
- These are filters for charts worth checking, not trade instructions.
