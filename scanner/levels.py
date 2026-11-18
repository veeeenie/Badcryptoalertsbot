"""
Levels (trader, 18 Nov 2026): the trader's chart levels, stored on the site instead of TradingView drawings.

The list lives in docs/data/levels_list.json:
  {"coins": [{"coin": "VVV",                          Hyperliquid name (trade.xyz stocks: "xyz:GOLD")
              "profiles": [{"label": "May–Aug range", "from": "2026-05-12", "to": "2026-09-07"},
                           {"label": "Since ATH", "from": "2026-09-21"}],          no "to" = up to now
              "lines": [{"price": 22.6, "label": "Short entry", "kind": "entry"}]}]}   kind: entry|stop|target|level

For each coin this writes to docs/data/levels.json:
  - a fixed-range volume profile per entry in "profiles": POC, VAH, VAL (70% value area) and a 40-row histogram,
    built from Hyperliquid candles (1-hour candles when the range starts within ~200 days, else 4-hour), with each
    candle's volume spread evenly across its high-low range;
  - daily EMAs 25/50/100/200 and the weekly EMA 25/50, now and as series for the chart;
  - the last 180 daily candles for the chart.
Run hourly. Reads public market data only.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, "docs", "data", "levels_list.json")
OUT = os.path.join(ROOT, "docs", "data", "levels.json")
API = "https://api.hyperliquid.xyz/info"
DAY = 86_400_000
WINDOW = 180          # daily candles shown on the chart


def hl(body):
    for i in range(3):
        try:
            r = requests.post(API, json=body, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == 2:
                raise
            time.sleep(2)


def candles(coin, interval, start, end):
    """All candles from start to end (ms), paging past the API's 5000-per-call limit."""
    step = {"1h": 3_600_000, "4h": 14_400_000, "1d": DAY}[interval]
    out, s = {}, start
    while s < end:
        e = min(end, s + 4900 * step)
        for c in hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": s, "endTime": e}}) or []:
            out[c["t"]] = c
        s = e + 1
    return [out[k] for k in sorted(out)]


def ema_series(xs, n):
    if len(xs) < n:
        return [None] * len(xs)
    k, e = 2 / (n + 1), sum(xs[:n]) / n
    out = [None] * (n - 1) + [e]
    for x in xs[n:]:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def profile(cs, rows=40):
    """POC / VAH / VAL (70%) and a histogram from candles, volume spread evenly over each candle's range."""
    lo = min(float(c["l"]) for c in cs)
    hi = max(float(c["h"]) for c in cs)
    fine = 400
    step = (hi - lo) / fine or 1e-9
    vol = [0.0] * fine
    for c in cs:
        l, h = float(c["l"]), float(c["h"])
        v = float(c["v"]) * (l + h) / 2
        a, b = int((l - lo) / step), min(fine - 1, int((h - lo) / step))
        for i in range(a, b + 1):
            vol[i] += v / (b - a + 1)
    tot = sum(vol)
    poc = max(range(fine), key=vol.__getitem__)
    i, j, acc = poc, poc, vol[poc]
    while acc < 0.7 * tot and (i > 0 or j < fine - 1):
        up = sum(vol[j + 1:j + 3]) if j < fine - 1 else -1
        dn = sum(vol[max(0, i - 2):i]) if i > 0 else -1
        if up >= dn:
            acc += up; j = min(fine - 1, j + 2)
        else:
            acc += dn; i = max(0, i - 2)
    per = fine // rows
    hist = [sum(vol[r * per:(r + 1) * per]) for r in range(rows)]
    mx = max(hist) or 1
    return {"poc": lo + (poc + 0.5) * step, "vah": lo + (j + 1) * step, "val": lo + i * step,
            "lo": lo, "hi": hi, "hist": [round(x / mx, 3) for x in hist]}


def sig(x):
    return None if x is None else float(f"{x:.5g}")


def main():
    try:
        with open(LIST) as f:
            coins = json.load(f).get("coins", [])
    except (OSError, ValueError):
        coins = []
    now = int(time.time() * 1000)
    out = []
    for cfg in coins:
        coin = cfg["coin"]
        try:
            daily = candles(coin, "1d", now - 900 * DAY, now)
            closes = [float(c["c"]) for c in daily]
            e = {n: ema_series(closes, n) for n in (25, 50, 100, 200)}
            # weekly closes (weeks start Monday 00:00 UTC)
            wk = {}
            for c in daily:
                d = datetime.fromtimestamp(c["t"] / 1000, timezone.utc)
                wk[(d - timedelta(days=d.weekday())).date()] = float(c["c"])
            wcl = [wk[k] for k in sorted(wk)]
            w25, w50 = ema_series(wcl, 25)[-1], ema_series(wcl, 50)[-1]
            price = float(hl({"type": "allMids", "dex": "xyz"} if coin.startswith("xyz:") else {"type": "allMids"})[coin])

            profs = []
            for p in cfg.get("profiles", []):
                t0 = int(datetime.fromisoformat(p["from"]).replace(tzinfo=timezone.utc).timestamp() * 1000)
                t1 = int(datetime.fromisoformat(p["to"]).replace(tzinfo=timezone.utc).timestamp() * 1000) + DAY if p.get("to") else now
                iv = "1h" if now - t0 < 200 * DAY else "4h"
                cs = [c for c in candles(coin, iv, t0, t1) if t0 <= c["t"] < t1]
                if not cs:
                    continue
                pr = profile(cs)
                profs.append({"label": p["label"], "from": p["from"], "to": p.get("to"), "interval": iv,
                              **{k: sig(v) for k, v in pr.items() if k != "hist"}, "hist": pr["hist"]})

            win = daily[-WINDOW:]
            n0 = len(daily) - len(win)
            out.append({
                "coin": coin, "name": cfg.get("name") or coin.replace("xyz:", ""), "price": sig(price),
                "ema": {"d25": sig(e[25][-1]), "d50": sig(e[50][-1]), "d100": sig(e[100][-1]), "d200": sig(e[200][-1]),
                        "w25": sig(w25), "w50": sig(w50)},
                "series": {"t": [c["t"] for c in win],
                           "ohlc": [[sig(float(c["o"])), sig(float(c["h"])), sig(float(c["l"])), sig(float(c["c"]))] for c in win],
                           **{f"d{n}": [sig(x) for x in e[n][n0:]] for n in (25, 50, 200)}},
                "profiles": profs, "lines": cfg.get("lines", []), "note": cfg.get("note", ""),
            })
        except Exception as ex:
            print(f"levels: {coin} failed: {ex}", file=sys.stderr)
    with open(OUT, "w") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "coins": out}, f, separators=(",", ":"))
        f.write("\n")
    print(f"levels: {len(out)} coins")


if __name__ == "__main__":
    main()
