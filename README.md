# Setup Desk

Hourly EMA setup scanner for the top 100 Hyperliquid perps (the coins you trade on trade.xyz).

- **Scanner** (`scanner/scan.py`) runs on GitHub Actions every hour. It pulls daily candles from Hyperliquid's free public API, builds weekly candles, and computes 25/50/100 EMAs on both.
- **Setups flagged:**
  - **Pullback long:** daily 25 > 50 > 100 EMA with the 50 and 100 rising; entry at the nearest EMA below price.
  - **Rejection short:** price has rallied into a flat or falling weekly 50/100 EMA overhead; entry at that EMA.
  - **Reclaim long:** a daily close back above the daily 100 or weekly 50/100 EMA in the last 5 days; entry on the retest.
  - Each setup gets an entry, a stop (1.2–1.5x daily ATR beyond the level), targets and reward-to-risk. Longs need at least 2R, shorts 1.8R.
- **Telegram:** you get a message when a new setup appears or price reaches an entry.
- **Dashboard:** `docs/index.html`, served by GitHub Pages and refreshed every hour.
- **News and unlocks:** a daily Claude scheduled task writes `docs/data/intel.json`.

## Settings

Edit the constants at the top of `scanner/scan.py`: `TOP_N`, `MIN_RR`, `MAX_DIST` (how far an entry may be from price), `AT_LEVEL`.

## Notes

- EMAs use Hyperliquid prices, so they can differ slightly from a Binance chart, especially weekly EMAs on coins listed on Hyperliquid later than on Binance.
- GitHub can delay hourly runs by a few minutes at busy times.
- These are filters for charts worth checking, not trade instructions.
