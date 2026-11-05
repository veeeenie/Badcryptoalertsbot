"""
Download the test bench's data: daily candles (open, high, low, close, base volume) for every
Hyperliquid main-dex perp, delisted ones included (so the bench isn't limited to today's survivors).
Writes bench/data/candles_1d.json.gz: {"generated_at": ..., "coins": {coin: {"delisted": bool,
"max_lev": n, "candles": [[t_ms, o, h, l, c, v], ...]}}}. Run by .github/workflows/bench-data.yml.
"""
import gzip
import json
import os
import time
from datetime import datetime, timezone

import requests

HL = "https://api.hyperliquid.xyz/info"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "candles_1d.json.gz")
S = requests.Session()


def post(body, tries=5):
    for i in range(tries):
        try:
            r = S.post(HL, json=body, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (i + 1)); continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def main():
    meta, _ = post({"type": "metaAndAssetCtxs"})
    end = int(time.time() * 1000)
    start = end - 4000 * 86_400_000
    coins = {}
    for a in meta["universe"]:
        name = a["name"]
        try:
            raw = post({"type": "candleSnapshot", "req": {"coin": name, "interval": "1d", "startTime": start, "endTime": end}}) or []
        except Exception as e:
            print(name, "failed", e); continue
        rows = sorted([[int(c["t"]), float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"]), float(c["v"])] for c in raw])
        if rows:
            coins[name] = {"delisted": bool(a.get("isDelisted")), "max_lev": a.get("maxLeverage"), "candles": rows}
        time.sleep(0.25)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with gzip.open(OUT, "wt") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "coins": coins}, f, separators=(",", ":"))
    n = sum(len(v["candles"]) for v in coins.values())
    print(f"{len(coins)} coins ({sum(v['delisted'] for v in coins.values())} delisted), {n} daily candles")


if __name__ == "__main__":
    main()
