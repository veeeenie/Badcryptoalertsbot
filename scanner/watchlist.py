"""
Weekly watchlist (rebuild step 1, 23 Oct 2026).

For the top Hyperliquid perps by volume:
  1. Weekly trend. A coin is in a downtrend only when the weekly EMAs AND the weekly structure agree:
       EMAs:      25 < 50 < 100 on closed weekly candles, and price below the 50
                  younger coins (trader, 23 Oct 2026): 50-99 weeks -> 25 < 50 and price below the 50;
                  25-49 weeks -> price below the 25 and the 25 falling over 4 weeks; under 25 weeks -> skipped
       structure: the last two weekly swing highs (highest week of a 9-week window) are lower highs, the last two swing lows are lower lows,
                  and price has not gone back above the last swing high.
     An uptrend is the mirror. Anything else is "no trend" and is left off the list.
  2. Weekly supply and demand zones: the base before a strong weekly move.
       strong move: 1-3 decisive weekly candles (body at least half the range) in one direction that together
                    move at least half the weekly ATR(14) (weekly crypto ATR is large: long wicks)
       base:        the 1-3 small-bodied candles (body at most half the range) right before it,
                    or the single candle before it when there are none
       demand (move up):   top = highest body of the base, bottom = lowest low of the base and the move candle
       supply (move down): bottom = lowest body of the base, top = highest high of the base and the move candle
     A zone is dropped once a weekly candle closes through its far side. Each later visit into the zone counts
     as a test; zones tested more than once are dropped.
  3. Trade plan for each trending coin: the nearest zone in the trend's direction (supply above price for a
     downtrend, demand below for an uptrend), at most one weekly ATR tall and within 2 weekly ATRs of price,
     that passes the gate.
       entries:     three tranches at the zone's near edge (or today's price, if price is already inside), middle and far edge, sized 20 : 30 : 50 so the
                    biggest order sits at the best price, next to the stop
       stop:        a tenth of a weekly ATR beyond the zone's far edge
       take-profit: two equal exits at the near edge of the next two opposite zones or the last weekly swing,
                    each at least 1R from the average entry (one exit if only one qualifies)
       gate:        reward-to-risk at least 1.4 : 1 with all orders filled (from the average entry)
                    and with only the first order filled; trader, 23 Oct 2026

Writes docs/data/watchlist.json. No alerts yet.
"""
import json
import os
import time
from datetime import datetime, timezone

from scan import DATA_DIR, universe, daily_candles, weekly_from_daily, ema

OUT_PATH = os.path.join(DATA_DIR, "watchlist.json")
EMA_LENGTHS = (25, 50, 100)
SWING_K = 4            # a weekly swing high/low is the extreme of a 9-week window (4 weeks each side); trader's choice, 23 Oct 2026
ATR_N = 14
MOVE_ATR = 0.5         # strong move: 1-3 decisive candles in one direction that together move >= half a weekly ATR
MOVE_MAX = 3
DECISIVE = 0.5         # decisive candle: body >= 50% of its range
BASE_BODY_RANGE = 0.5  # base candle: body <= 50% of its range
BASE_MAX = 3
MAX_TESTS = 1          # zones visited more than once are dropped
MAX_ZONE_ATR = 1.0     # zones taller than one weekly ATR are not traded (stop too far); trader, 23 Oct 2026
MAX_DIST_ATR = 2.0     # zones whose near edge is more than 2 weekly ATRs from price are not shown; trader, 23 Oct 2026
MIN_RR = 1.4           # the gate: reward-to-risk of the whole plan from the average entry; trader, 23 Oct 2026
ENTRY_SPLIT = (0.3, 0.3, 0.4)         # size grows toward the best price, flat enough that every order reaches $10 with ~$33 of position; trader, 24 Oct 2026
EXIT_SPLIT = (0.5, 0.5)                # two equal exits: next opposite zone, then the one after (or the last swing)
RISK_USD = 3.0         # dollars lost if every order fills and the stop is hit; trader, 23 Oct 2026
MIN_ORDER_USD = 10.0   # Hyperliquid's smallest order value
LIQ_BUFFER = 2.0       # liquidation at least 2x the stop distance away when suggesting leverage
MAX_STOP_GAP_ATR = 0.5 # skip a trade whose stop sits more than half a weekly ATR beyond the last entry; trader, 24 Oct 2026
STOP_BUFFER_ATR = 0.25 # stop sits a quarter of a weekly ATR beyond the rejection cluster (or the zone); trader, 23 Oct 2026
REJECT_LOOK = 13       # weeks searched for the start of the current counter-move
CLUSTER_WEEKS = 52     # weekly highs/lows of the past year count towards a rejection cluster
CLUSTER_ATR = 0.2      # highs within a fifth of a weekly ATR of each other form a cluster
CLUSTER_MIN = 3        # at least three rejections


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


def swings(c, k=None):
    k = k or SWING_K
    highs, lows = [], []
    for i in range(k, len(c) - k):
        h, l = c[i]["h"], c[i]["l"]
        if h > max(x["h"] for x in c[i - k:i]) and h >= max(x["h"] for x in c[i + 1:i + k + 1]):
            highs.append((i, h))
        if l < min(x["l"] for x in c[i - k:i]) and l <= min(x["l"] for x in c[i + 1:i + k + 1]):
            lows.append((i, l))
    return highs, lows


def weekly_trend(c, price, k=None):
    """Returns ("up"|"down"|None, details)."""
    closes = [x["c"] for x in c]
    if len(closes) < 25:             # an EMA is only used once there are at least that many weekly candles
        return None, {"why": "under 25 weeks of history"}
    E = {n: ema(closes, n)[-1] for n in EMA_LENGTHS if len(closes) >= n}
    highs, lows = swings(c, k)
    if len(highs) < 2 or len(lows) < 2:
        return None, {"why": "not enough weekly swings"}
    (ih1, h1), (ih2, h2) = highs[-2], highs[-1]
    (il1, l1), (il2, l2) = lows[-2], lows[-1]
    d = {"ema25": E.get(25), "ema50": E.get(50), "ema100": E.get(100), "last_swing_high": h2, "last_swing_low": l2,
         "swing_highs": [h1, h2], "swing_lows": [l1, l2]}
    if 100 in E:                     # 100+ weeks: 25/50/100 stacked, price on the trend side of the 50
        d["ema_basis"] = "25/50/100"
        ema_down = E[25] < E[50] < E[100] and price < E[50]
        ema_up = E[25] > E[50] > E[100] and price > E[50]
    elif 50 in E:                    # 50-99 weeks: 25/50 stacked, price on the trend side of the 50
        d["ema_basis"] = "25/50"
        ema_down = E[25] < E[50] and price < E[50]
        ema_up = E[25] > E[50] and price > E[50]
    else:                            # 25-49 weeks: price on the trend side of the 25, and the 25 sloping that way (4 weeks)
        d["ema_basis"] = "25"
        e25 = ema(closes, 25)
        ema_down = price < E[25] and e25[-1] < e25[-5]
        ema_up = price > E[25] and e25[-1] > e25[-5]
    broke_high = price > h2 or any(x["c"] > h2 for x in c[ih2 + 1:])
    broke_low = price < l2 or any(x["c"] < l2 for x in c[il2 + 1:])
    struct_down = h2 < h1 and l2 < l1 and not broke_high
    struct_up = h2 > h1 and l2 > l1 and not broke_low
    if ema_down and struct_down:
        return "down", d
    if ema_up and struct_up:
        return "up", d
    d["ema"] = "down" if ema_down else "up" if ema_up else "mixed"
    d["structure"] = ("down" if struct_down else "up" if struct_up else
                      ("LH+LL but price back above last swing high" if h2 < h1 and l2 < l1 else
                       "HH+HL but price back below last swing low" if h2 > h1 and l2 > l1 else
                       "lower high, higher low" if h2 < h1 else "higher high, lower low"))
    d["why"] = f"EMAs {d['ema']}, structure {d['structure']}"
    return None, d


def decisive(x):
    r = x["h"] - x["l"]
    return r > 0 and abs(x["c"] - x["o"]) >= DECISIVE * r


def zones(c):
    """Supply/demand zones from closed weekly candles that are still standing."""
    A = atr_series(c)
    found = []
    i = 1
    while i < len(c):
        a = A[i - 1]
        if a is None or not decisive(c[i]):
            i += 1
            continue
        up = c[i]["c"] > c[i]["o"]
        j = i          # the move: 1-3 decisive candles in the same direction
        while j + 1 < len(c) and j - i + 1 < MOVE_MAX and decisive(c[j + 1]) and (c[j + 1]["c"] > c[j + 1]["o"]) == up:
            j += 1
        move = abs(c[j]["c"] - c[i]["o"])
        if move < MOVE_ATR * a:
            i += 1
            continue
        base = []
        k = i - 1
        while k >= 0 and len(base) < BASE_MAX:
            b = c[k]
            br = b["h"] - b["l"]
            if br > 0 and abs(b["c"] - b["o"]) <= BASE_BODY_RANGE * br:
                base.insert(0, b); k -= 1
            else:
                break
        if not base:
            base = [c[i - 1]]
        leg = c[i:j + 1]
        if up:
            top = max(max(b["o"], b["c"]) for b in base)
            bottom = min(x["l"] for x in base + leg[:1])
        else:
            bottom = min(min(b["o"], b["c"]) for b in base)
            top = max(x["h"] for x in base + leg[:1])
        if top > bottom:
            found.append({"kind": "demand" if up else "supply", "top": top, "bottom": bottom,
                          "base_start": base[0]["t"], "formed": c[j]["t"], "i": j})
        i = j + 1
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


def rejection_level(closed, short, A):
    """The level where most of the rejections cluster; the stop goes slightly beyond it (trader, 23 Oct 2026).
    For a short: find the peak of the current bounce (highest weekly high since the lowest low of the last
    REJECT_LOOK weeks), then the highest price at or below that peak where at least CLUSTER_MIN weekly highs
    of the past CLUSTER_WEEKS weeks sit within CLUSTER_ATR weekly ATRs of each other. A lone wick above the
    cluster is ignored. No cluster -> the peak itself. A long is the mirror, using weekly lows."""
    rec = closed[-REJECT_LOOK:]
    year = closed[-CLUSTER_WEEKS:]
    tol = CLUSTER_ATR * A
    if short:
        i = min(range(len(rec)), key=lambda k: rec[k]["l"])
        peak = max(x["h"] for x in rec[i:])
        highs = [x["h"] for x in year if x["h"] <= peak]
        for L in sorted(set(highs), reverse=True):
            if sum(1 for h in highs if L - tol <= h <= L) >= CLUSTER_MIN:
                return L
        return peak
    i = max(range(len(rec)), key=lambda k: rec[k]["h"])
    trough = min(x["l"] for x in rec[i:])
    lows = [x["l"] for x in year if x["l"] >= trough]
    for L in sorted(set(lows)):
        if sum(1 for l in lows if L <= l <= L + tol) >= CLUSTER_MIN:
            return L
    return trough


def merge_small(entries, stop):
    """Keeps all three 20 : 30 : 50 orders (trader, 23 Oct 2026: no merging; the risk rises instead, see sizing).
    Returns (entries, weights, average entry)."""
    ws = list(ENTRY_SPLIT)
    avg = sum(e * w for e, w in zip(entries, ws))
    return list(entries), ws, avg


def plan(closed, price, trend, d):
    """The nearest zone in the trend's direction that gives a trade passing the gate.
    Returns (plan, None) or (None, reason)."""
    A = atr_series(closed)[-1]
    zs = zones(closed)
    short = trend == "down"
    kind, opp = ("supply", "demand") if short else ("demand", "supply")
    level = rejection_level(closed, short, A)

    def stop_of(z):   # where this zone's plan puts the stop
        anchor = max(z["top"], level) if short else min(z["bottom"], level)
        return anchor + STOP_BUFFER_ATR * A if short else anchor - STOP_BUFFER_ATR * A
    # a zone stays in play until price passes its plan's stop (trader, 24 Oct 2026): price sitting inside the zone,
    # or between the zone and the stop, still shows the plan, with the passed orders filling at today's price
    side_ok = [z for z in zs if z["kind"] == kind and (stop_of(z) > price if short else stop_of(z) < price)]
    if not side_ok:
        return None, f"no {kind} zone"
    short_enough = [z for z in side_ok if z["top"] - z["bottom"] <= MAX_ZONE_ATR * A]
    if not short_enough:
        return None, "only zones taller than a weekly ATR"
    # price must be within 2 weekly ATRs of the zone's near edge
    cands = [z for z in short_enough
             if ((z["bottom"] - price) if short else (price - z["top"])) <= MAX_DIST_ATR * A]
    if not cands:
        return None, "nearest zone more than 2 weekly ATRs away"
    cands.sort(key=lambda z: z["bottom"] if short else -z["top"])
    best = None
    for z in cands:
        near, far = (z["bottom"], z["top"]) if short else (z["top"], z["bottom"])
        # entries stay inside the zone, where price is likely to reach them; the stop sits a quarter of a weekly
        # ATR beyond the rejection cluster (or the zone, if that's further), so a sweep of the level doesn't take it
        anchor = max(far, level) if short else min(far, level)
        # price already inside the zone: the ladder starts at today's price and runs to the far edge (no stacking)
        start = min(far, max(near, price)) if short else max(far, min(near, price))
        levels = (start, (start + far) / 2, far)
        # levels price has already passed would fill straight away, at today's price
        entries = [max(x, price) if short else min(x, price) for x in levels]
        stop = anchor + STOP_BUFFER_ATR * A if short else anchor - STOP_BUFFER_ATR * A
        entries, weights, avg = merge_small(entries, stop)
        if abs(stop - entries[-1]) > MAX_STOP_GAP_ATR * A:   # thin zone far below the real rejection: not the supply
            best = best or (None, "stop more than half a weekly ATR beyond the zone")
            continue
        risk = abs(stop - avg)
        if short:
            raw = sorted((o["top"] for o in zs if o["kind"] == opp and o["top"] < near), reverse=True)
            swing = d["last_swing_low"] if d["last_swing_low"] < near else None
        else:
            raw = sorted(o["bottom"] for o in zs if o["kind"] == opp and o["bottom"] > near)
            swing = d["last_swing_high"] if d["last_swing_high"] > near else None
        if swing is not None:
            raw = sorted(raw + [swing], reverse=short)
        tz = []   # each take-profit at least 1R from the average entry; skip one within a quarter ATR of the last
        for t in raw:
            if (avg - t if short else t - avg) < risk:
                continue
            if tz and abs(t - tz[-1]) < 0.25 * A:
                continue
            tz.append(t)
        if not tz:
            best = best or (None, "no zone or swing at least 1R away to take profit at")
            continue
        tp1 = tz[0]
        tp2 = tz[1] if len(tz) > 1 else None
        tps = [tp1, tp2] if tp2 is not None else [tp1]
        split = EXIT_SPLIT if tp2 is not None else (1.0,)
        exit_ = sum(t * w for t, w in zip(tps, split))
        rr = (avg - exit_) / risk if short else (exit_ - avg) / risk
        e1 = entries[0]   # if price only reaches the first (smallest) order
        rr_first = (e1 - exit_) / abs(stop - e1) if short else (exit_ - e1) / abs(stop - e1)
        if rr < MIN_RR or rr_first < MIN_RR:   # the gate holds with all orders filled AND with only the first
            if best is None or best[0] is None and "gives" not in best[1]:
                best = (None, f"best zone {z['bottom']:.4g}-{z['top']:.4g} gives {min(rr, rr_first):.1f}:1")
            continue
        first = entries[0]   # the first order left after merging
        dist = max(0.0, (first / price - 1) if short else (1 - first / price))
        dist_best = max(0.0, (entries[-1] / price - 1) if short else (1 - entries[-1] / price))   # to the biggest order
        return {
            "side": "short" if short else "long", "zone_low": z["bottom"], "zone_high": z["top"],
            "zone_atr": round((z["top"] - z["bottom"]) / A, 2), "tests": z["tests"], "rejection_level": level if anchor == level else None,   # only when it sets the stop
            "base_from": iso(z["base_start"]), "formed": iso(z["formed"]),
            "entries": entries, "entry_split": [round(w * 100) for w in weights], "avg_entry": avg, "stop": stop, "tps": tps, "rr": round(rr, 2), "rr_first_only": round(rr_first, 2),
            "risk_pct": round(risk / avg * 100, 1), "past_zone": (price > z["top"]) if short else (price < z["bottom"]), "distance_pct": round(dist * 100, 1), "distance_best_pct": round(dist_best * 100, 1), "at_zone": dist == 0.0,
        }, None
    return best if best else (None, "no trade")


def sizing(p, max_lev):
    """Order sizes so the whole plan loses RISK_USD if every order fills and the stop is hit, plus a leverage
    suggestion: the highest whole leverage that keeps (isolated) liquidation at least LIQ_BUFFER x the stop
    distance away, measured from the first entry (the one furthest from the stop)."""
    weights = [x / 100 for x in p["entry_split"]]
    qty = RISK_USD / abs(p["avg_entry"] - p["stop"])
    # if $3 can't fund every order at Hyperliquid's $10 minimum, the risk rises just enough that it can
    qty = max([qty] + [MIN_ORDER_USD * 1.01 / (w * e) for e, w in zip(p["entries"], weights)])
    risk = qty * abs(p["avg_entry"] - p["stop"])
    orders = []
    for e, w in zip(p["entries"], weights):
        orders.append({"price": e, "share": round(w * 100), "size": qty * w, "usd": round(qty * w * e, 2),
                       "risk_usd": round(qty * w * abs(e - p["stop"]), 2)})
    notional = sum(o["usd"] for o in orders)
    stop_dist = abs(p["stop"] - p["entries"][0]) / p["entries"][0]
    max_lev = int(max_lev or 10)
    mmr = 1 / (2 * max_lev)                      # Hyperliquid maintenance margin: half the initial margin at max leverage
    lev = max(1, min(max_lev, int(1 / (LIQ_BUFFER * stop_dist + mmr))))
    return {"risk_usd": round(risk, 2), "risk_raised": risk > RISK_USD + 0.005, "size_total": qty, "notional_usd": round(notional, 2), "orders": orders,
            "leverage": lev, "max_leverage": max_lev, "margin_usd": round(notional / lev, 2),
            "risk_first_only_usd": orders[0]["risk_usd"],
            "small_orders": [o["share"] for o in orders if o["usd"] < MIN_ORDER_USD]}


def main():
    now_ms = int(time.time() * 1000)
    rows, notrade, skipped = [], [], {"young": [], "no_trend": 0}
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
                skipped.setdefault("detail", []).append({"coin": coin, "ema": d.get("ema"), "structure": d.get("structure")})
            continue
        p, why = plan(closed, price, trend, d)
        if p is None:
            notrade.append({"coin": coin, "trend": trend, "ema_basis": d["ema_basis"], "why": why})
            print(f"{coin:8} {trend:4} no trade: {why}")
            continue
        p.update({"coin": coin, "price": price, "trend": trend, "ema_basis": d["ema_basis"], "weeks": len(closed)})
        p["sizing"] = sizing(p, r.get("max_lev"))
        rows.append(p)
        print(f"{coin:8} {p['side']:5} entries {[round(e, 6) for e in p['entries']]} stop {p['stop']:.6g} "
              f"tps {[round(t, 6) for t in p['tps']]} {p['rr']}:1 dist {p['distance_pct']}%")
    rows.sort(key=lambda x: x["distance_pct"])
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scanned": len(coins),
        "gate_rr": MIN_RR, "risk_usd": RISK_USD,
        "counts": {"trades": len(rows), "trend_no_trade": len(notrade), "no_trend": skipped["no_trend"],
                   "too_young": len(skipped["young"])},
        "rows": rows,
        "trend_no_trade": notrade,
        "no_trend_detail": skipped.get("detail", []),
        "too_young": skipped["young"],
    }
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out["counts"]))


if __name__ == "__main__":
    main()
