# Test bench

Replays a strategy day by day over Hyperliquid's daily candle history and reports results in R.
The rules of the bench (point-in-time top-100 universe, no look-ahead, fills on touch, stop-first on
ambiguous days, fees and long funding, 2026 held out) are at the top of `bench.py`.

    # data: the "Bench data" workflow writes bench/data/candles_1d.json.gz to the bench-data branch
    git fetch origin bench-data && mkdir -p bench/data && \
      git show origin/bench-data:bench/data/candles_1d.json.gz > bench/data/candles_1d.json.gz
    python bench/run.py live_main live_testing

`strategies.py` holds the strategies. The `live_*` baselines call `scanner/watchlist.py` unchanged,
so they test exactly what the dashboard shows. A change to the live bot should go live only if it
beats the current version on the 2026 (out-of-sample) trades, across many coins.
