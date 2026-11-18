# Levels section (archived 18 Nov 2026)

A Setup Desk section that drew each coin's chart with fixed-range volume profiles (POC/VAH/VAL), daily EMAs,
alert levels and the trader's live Hyperliquid orders. Built when TradingView limited drawings; archived when
TradingView came back. Nothing here runs.

To restore:
1. `git mv archive/levels/levels.py scanner/levels.py` and `git mv archive/levels/levels_list.json docs/data/levels_list.json`
2. Put the four parts of `index-snippets.html` back into `docs/index.html` (each part says where), and add
   `"levels"` back to the `secs` list after `"alerts"`.
3. In `.github/workflows/scan.yml`: add `python scanner/levels.py || echo "::warning::levels failed"` after dexwatch,
   `[ -f docs/data/levels.json ] && git add docs/data/levels.json`, `levels.json` to GEN, and `scanner/levels.py` to the push paths.
