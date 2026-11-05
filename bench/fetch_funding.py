"""
Download Hyperliquid funding history for the bench (shard i of n), averaged per UTC day.
Writes bench/data/funding_<i>.json: {coin: [[day_ms, mean hourly rate], ...]}.
"""
import json
import os
import sys
import time

import requests

HL = "https://api.hyperliquid.xyz/info"
DAY = 86_400_000
START = 1704067200000  # 2024-01-01
S = requests.Session()


def post(body, tries=6):
    for i in range(tries):
        try:
            r = S.post(HL, json=body, timeout=30)
            if r.status_code == 429:
                time.sleep(10 * (i + 1)); continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def main(i, n):
    meta, _ = post({"type": "metaAndAssetCtxs"})
    names = sorted(a["name"] for a in meta["universe"])[i::n]
    out = {}
    now = int(time.time() * 1000)
    for name in names:
        t, rows = START, []
        while t < now:
            try:
                r = post({"type": "fundingHistory", "coin": name, "startTime": t, "endTime": now}) or []
            except Exception as e:
                print(name, "failed", e); break
            if not r:
                break
            rows += [(int(x["time"]), float(x["fundingRate"])) for x in r]
            last = int(r[-1]["time"])
            if last <= t or len(r) < 2:
                break
            t = last + 1
            time.sleep(1.2)
        days = {}
        for ts, f in rows:
            d = ts - ts % DAY
            s, c = days.get(d, (0.0, 0))
            days[d] = (s + f, c + 1)
        if days:
            out[name] = [[d, s / c] for d, (s, c) in sorted(days.items())]
        print(name, len(rows), "hours", flush=True)
    os.makedirs("bench/data", exist_ok=True)
    with open(f"bench/data/funding_{i}.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]))
