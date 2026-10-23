"""
Weekly watchlist (rebuild step 1, 23 Oct 2026).

For the top Hyperliquid perps by volume:
  1. Weekly trend. A coin is in a downtrend only when the weekly EMAs AND the weekly structure agree:
       EMAs:      25 < 50 < 100 on closed weekly candles, and price below the 50
       structure: the last two weekly swing highs are lower highs, the last two swing lows are lower lows,
                  and price has not gone back above the last swing high.
     An uptrend is the mirror. Anything else is "no trend" and is left off the list.
  2. Weekly supply and demand zones: the base before a strong weekly move.
       strong move: a weekly candle whose body is at least 1.2x the weekly ATR(14) and at least 60% of its range
       base:        the 1-3 small-bodied candles (body at most half the range) right before it,
                    or the single candle before it when there are none
       demand (move up):   top = highest body of the base, bottom = lowest low of the base and the move candle
       supply (move down): bottom = lowest body of the base, top = highest high of the base and the move candle
     A zone is dropped once a weekly candle closes through its far side. Each later visit into the zone counts
     as a test; zones tested more than once are dropped.
  3. For each trending coin, the nearest zone in the trend's direction: supply above price for a downtrend,
     demand below price for an uptrend, and how far price is from it.

Writes docs/data/watchlist.json. No trade plans and no alerts yet.
"""
import json
import os
import time
from datetime import datetime, timezone

from scan import DATA_DIR, universe, daily_candles, weekly_from_daily, ema

OUT_PATH = os.path.join(DATA_DIR, "watchlist.json")
EMA_LENGTHS = (25, 50, 100)
SWING_K = 2            # a weekly swing high/low beats the 2 weeks on each side
ATR_N = 14
MOVE_BODY_ATR = 1.2    # strong move: body >= 1.2 x weekly ATR ...
MOVE_BODY_RANGE = 0.6  # ... and body >= 60% of the candle's range
BASE_BODY_RANGE = 0.5  # base candle: body <= 50% of its range
BASE_MAX = 3
MAX_TESTS = 1          # zones visited more than once are dropped


def atr_series(c, n=ATR_N):
    """Wilder ATR aligned with candles; None until there are n true ranges."""
    out = [None] * len(c)
    trs = [None]
    for i in range(1, len(c)):
        h, l, pc = c[i]["h"], c[i]["l"], c[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = None
    for i in range(1, len(c)):
        if i == n:
            a = sum(trs[1:n + 1]) / n
        elif i > n:
            a = (a * (n - 1) + trs[i]) / n
        out[i] = a
    return out


def swings(c, k=SWING_K):
    highs, lows = [], []
    for i in range(k, len(c) - k):
        h, l = c[i]["h"], c[i]["l"]
        if h > max(x["h"] for x in c[i - k:i]) and h >= max(x["h"] for x in c[i + 1:i + k + 1]):
            highs.append((i, h))
        if l < min(x["l"] for x in c[i - k:i]) and l <= min(x["l"] for x in c[i + 1:i + k + 1]):
            lows.append((i, l))
    return highs, lows


def weekly_trend(c, price):
    """Returns ("up"|"down"|None, details)."""
    closes = [x["c"] for x in c]
    E = {}
    for n in EMA_LENGTHS:
        if len(closes) < n:          # only use an EMA once there are at least n weekly candles
            return None, {"why": f"under {n} weeks of history"}
        E[n] = ema(closes, n)[-1]
    highs, lows = swings(c)
    if len(highs) < 2 or len(lows) < 2:
        return None, {"why": "not enough weekly swings"}
    (ih1, h1), (ih2, h2) = highs[-2], highs[-1]
    (il1, l1), (il2, l2) = lows[-2], lows[-1]
    d = {"ema25": E[25], "ema50": E[50], "ema100": E[100], "last_swing_high": h2, "last_swing_low": l2}
    ema_down = E[25] < E[50] < E[100] and price < E[50]
    ema_up = E[25] > E[50] > E[100] and price > E[50]
    broke_high = price > h2 or any(x["c"] > h2 for x in c[ih2 + 1:])
    broke_low = price < l2 or any(x["c"] < l2 for x in c[il2 + 1:])
    struct_down = h2 < h1 and l2 < l1 and not broke_high
    struct_up = h2 > h1 and l2 > l1 and not broke_low
    if ema_down and struct_down:
        return "down", d
    if ema_up and struct_up:
        return "up", d
    d["why"] = ("EMAs " + ("down" if ema_down else "up" if ema_up else "mixed")
                + ", structure " + ("down" if struct_down else "up" if struct_up else "mixed"))
    return None, d


def zones(c):
    """Supply/demand zones from closed weekly candles that are still standing."""
    A = atr_series(c)
    found = []
    for i in range(1, len(c)):
        a = A[i - 1]
        m = c[i]
        body, rng = abs(m["c"] - m["o"]), m["h"] - m["l"]
        if a is None or rng <= 0 or body < MOVE_BODY_ATR * a or body < MOVE_BODY_RANGE * rng:
            continue
        up = m["c"] > m["o"]
        base = []
        j = i - 1
        while j >= 0 and len(base) < BASE_MAX:
            b = c[j]
            br = b["h"] - b["l"]
            if br > 0 and abs(b["c"] - b["o"]) <= BASE_BODY_RANGE * br:
                base.insert(0, b); j -= 1
            else:
                break
        if not base:
            base = [c[i - 1]]
        if up:
            top = max(max(b["o"], b["c"]) for b in base)
            bottom = min([b["l"] for b in base] + [m["l"]])
        else:
            bottom = min(min(b["o"], b["c"]) for b in base)
            top = max([b["h"] for b in base] + [m["h"]])
        if top <= bottom:
            continue
        found.append({"kind": "demand" if up else "supply", "top": top, "bottom": bottom,
                      "base_start": base[0]["t"], "formed": m["t"], "i": i})
    standing = []
    for z in found:
        tests, broken, away = 0, False, False
        for x in c[z["i"] + 1:]:
            if z["kind"] == "demand":
                if x["c"] < z["bottom"]:
                    broken = True; break
                inside = x["l"] <= z["top"]
            else:
                if x["c"] > z["top"]:
                    broken = True; break
                inside = x["h"] >= z["bottom"]
            if inside and away:
                tests += 1
            away = not inside
        if not broken and tests <= MAX_TESTS:
            z["tests"] = tests
            standing.append(z)
    return standing


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main():
    now_ms = int(time.time() * 1000)
    rows, skipped = [], {"young": [], "no_trend": 0, "no_zone": []}
    coins = universe()
    for r in coins:
        coin, price = r["coin"], r["price"]
        try:
            weekly = weekly_from_daily(daily_candles(coin))
        except Exception as e:
            print(f"{coin}: candles failed ({e})")
            continue
        closed = [w for w in weekly if w["t"] + 7 * 86_400_000 <= now_ms]
        trend, d = weekly_trend(closed, price)
        if trend is None:
            if "history" in d.get("why", ""):
                skipped["young"].append(coin)
            else:
                skipped["no_trend"] += 1
            continue
        cands = []
        for z in zones(closed):
            if trend == "down" and z["kind"] == "supply" and z["top"] > price:
                cands.append(z)
            if trend == "up" and z["kind"] == "demand" and z["bottom"] < price:
                cands.append(z)
        if not cands:
            skipped["no_zone"].append(coin)
            continue
        if trend == "down":
            z = min(cands, key=lambda z: z["bottom"])
            dist = max(0.0, z["bottom"] / price - 1)
        else:
            z = max(cands, key=lambda z: z["top"])
            dist = max(0.0, 1 - z["top"] / price)
        rows.append({
            "coin": coin, "price": price, "trend": trend,
            "zone_kind": z["kind"], "zone_near": z["bottom"] if trend == "down" else z["top"],
            "zone_far": z["top"] if trend == "down" else z["bottom"],
            "zone_width_pct": round((z["top"] / z["bottom"] - 1) * 100, 1),
            "distance_pct": round(dist * 100, 1), "at_zone": dist == 0.0,
            "tests": z["tests"], "base_from": iso(z["base_start"]), "formed": iso(z["formed"]),
            "ema50": d["ema50"], "last_swing_high": d["last_swing_high"], "last_swing_low": d["last_swing_low"],
        })
        print(f"{coin:8} {trend:4} {z['kind']} {z['bottom']:.6g}-{z['top']:.6g} dist {dist*100:.1f}% tests {z['tests']}")
    rows.sort(key=lambda x: x["distance_pct"])
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scanned": len(coins),
        "counts": {"listed": len(rows), "down": sum(x["trend"] == "down" for x in rows),
                   "up": sum(x["trend"] == "up" for x in rows), "no_trend": skipped["no_trend"],
                   "too_young": len(skipped["young"]), "trend_but_no_zone": len(skipped["no_zone"])},
        "trend_but_no_zone": skipped["no_zone"],
        "rows": rows,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out["counts"]))


if __name__ == "__main__":
    main()
