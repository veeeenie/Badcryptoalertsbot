"""
Setup Desk engine v2 (in testing on the bench): one trend score and one level map, used for every
decision, instead of separate rules for trend, zones, rejection clusters and swings.

1. Trend score, -1..+1, from closed weekly candles:
     momentum: log return over 4, 13, 26 and 52 weeks, each divided by the weekly volatility scaled
               to that horizon (sigma * sqrt(h)), clipped to +-2 and halved, then averaged;
     EMA stack: the average of three signs: price vs EMA25, EMA25 vs EMA50, EMA50 vs EMA100
               (the EMAs a young coin has);
     score = average of the two.
2. Level map: every swing high and low on the weekly (2 weeks each side) and daily (3 days each side)
   charts over the last two years, plus the all-time high and low, clustered when within a quarter of
   a weekly ATR of each other. Each cluster is a band (lowest to highest member) with a strength:
   the sum over its touches of a timeframe weight (weekly 2, daily 1) times a recency decay
   (half-life one year). Highs and lows both count: an old ceiling is a later floor.
3. Plan (long; short is the mirror): with score >= SCORE_MIN, the nearest support band below price
   with strength >= STRENGTH_MIN within 2 weekly ATRs is the entry zone (orders at its top, middle,
   bottom, sized like the live bot); the stop sits a quarter of a weekly ATR under the band; the
   targets are the next two resistance bands above (strength >= STRENGTH_MIN), just in front of them;
   with no resistance above, 3R. The 1.4 : 1 gate applies with all orders filled and with only the first.
"""
import math

WEEK = 7 * 86_400_000
H_MOM = (4, 13, 26, 52)
SCORE_MIN = 0.3
STRENGTH_MIN = 2.0
CLUSTER_ATR = 0.25
STOP_ATR = 0.25
REACH_ATR = 2.0
HALF_LIFE_W = 52.0
LOOKBACK_W = 104
MIN_RR = 1.4
TP_FRONT = 0.005


def ema_last(xs, n):
    if len(xs) < n:
        return None
    a = 2 / (n + 1); e = xs[0]
    for x in xs[1:]:
        e = a * x + (1 - a) * e
    return e


def atr(weeks, n=14):
    trs = []
    for i in range(1, len(weeks)):
        h, l, pc = weeks[i]["h"], weeks[i]["l"], weeks[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    trs = trs[-n:]
    return sum(trs) / len(trs) if trs else None


def trend_score(weeks, price):
    """(score, details) or (None, why)."""
    closes = [w["c"] for w in weeks]
    if len(closes) < 27:
        return None, "under 27 weeks"
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(max(1, len(closes) - 52), len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
    if len(rets) < 26:
        return None, "short history"
    mu = sum(rets) / len(rets)
    sig = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) or 1e-9
    parts = []
    for h in H_MOM:
        if len(closes) > h:
            z = math.log(price / closes[-1 - h]) / (sig * math.sqrt(h))
            parts.append(max(-2.0, min(2.0, z)) / 2)
    mom = sum(parts) / len(parts)
    e = {n: ema_last(closes, n) for n in (25, 50, 100)}
    sg = lambda a, b: 1 if a > b else -1
    stack = [sg(price, e[25])]
    if e[50] is not None:
        stack.append(sg(e[25], e[50]))
    if e[100] is not None:
        stack.append(sg(e[50], e[100]))
    st = sum(stack) / len(stack)
    score = (mom + st) / 2
    return score, {"mom": round(mom, 3), "stack": round(st, 3), "sigma": sig, "ema": e}


def pivots(bars, k):
    out = []
    for i in range(k, len(bars) - k):
        h, l = bars[i]["h"], bars[i]["l"]
        if h >= max(b["h"] for b in bars[i - k:i + k + 1]):
            out.append((bars[i]["t"], h))
        if l <= min(b["l"] for b in bars[i - k:i + k + 1]):
            out.append((bars[i]["t"], l))
    return out


def level_map(weeks, daily, now_ms, A):
    """Bands [{lo, hi, mid, strength, touches}] sorted by price."""
    w = weeks[-LOOKBACK_W:]
    d = [x for x in daily if x["t"] >= now_ms - LOOKBACK_W * WEEK]
    pts = [(p, t, 2.0) for t, p in pivots(w, 2)] + [(p, t, 1.0) for t, p in pivots(d, 3)]
    if weeks:
        hi = max(weeks, key=lambda x: x["h"]); lo = min(weeks, key=lambda x: x["l"])
        pts += [(hi["h"], hi["t"], 2.0), (lo["l"], lo["t"], 2.0)]
    pts.sort()
    tol = CLUSTER_ATR * A
    bands = []
    for p, t, wt in pts:
        s = wt * 0.5 ** (max(0, now_ms - t) / WEEK / HALF_LIFE_W)
        if bands and p - bands[-1]["hi"] <= tol and p - bands[-1]["lo"] <= 2 * tol:   # no chaining into wide bands
            b = bands[-1]
            b["hi"] = p; b["strength"] += s; b["touches"] += 1; b["sum"] += p * s
        else:
            bands.append({"lo": p, "hi": p, "strength": s, "touches": 1, "sum": p * s})
    for b in bands:
        b["mid"] = b["sum"] / b["strength"]
    return bands


def plan(weeks, daily, price, now_ms, ladder):
    """A plan dict for the bench (side, entries, weights, stop, tps, details) or None."""
    if len(weeks) < 27:
        return None
    score, info = trend_score(weeks, price)
    if score is None or abs(score) < SCORE_MIN:
        return None
    long = score > 0
    A = atr(weeks)
    if not A:
        return None
    bands = level_map(weeks, daily, now_ms, A)
    strong = [b for b in bands if b["strength"] >= STRENGTH_MIN]
    if long:
        sup = [b for b in strong if b["hi"] <= price and price - b["hi"] <= REACH_ATR * A]
        if not sup:
            return None
        z = max(sup, key=lambda b: b["hi"])                 # nearest strong support below
        near, far = z["hi"], z["lo"]
        stop = far - STOP_ATR * A
        levels = [near, (near + far) / 2, far] if near - far > 0.1 * A else [near]
        res = sorted([b["lo"] for b in strong if b["lo"] > near + 0.1 * A])
        tgt = [r * (1 - TP_FRONT) for r in res]
    else:
        res_ = [b for b in strong if b["lo"] >= price and b["lo"] - price <= REACH_ATR * A]
        if not res_:
            return None
        z = min(res_, key=lambda b: b["lo"])
        near, far = z["lo"], z["hi"]
        stop = far + STOP_ATR * A
        levels = [near, (near + far) / 2, far] if far - near > 0.1 * A else [near]
        sup = sorted([b["hi"] for b in strong if b["hi"] < near - 0.1 * A], reverse=True)
        tgt = [s * (1 + TP_FRONT) for s in sup]
    lad = ladder(levels, stop)
    if lad is None:
        return None
    entries, weights, avg = lad
    risk = abs(avg - stop)
    sgn = 1 if long else -1
    tps = []
    for t in tgt:
        if sgn * (t - avg) < risk:          # each target at least 1R
            continue
        if tps and abs(t - tps[-1]) < 0.25 * A:
            continue
        tps.append(t)
        if len(tps) == 2:
            break
    if not tps:
        tps = [avg + sgn * 3 * risk]          # nothing in the way: 3R
    split = [0.5, 0.5] if len(tps) == 2 else [1.0]
    ex = sum(t * s for t, s in zip(tps, split))
    rr = sgn * (ex - avg) / risk
    rr1 = sgn * (ex - entries[0]) / abs(entries[0] - stop)
    if rr < MIN_RR or rr1 < MIN_RR:
        return None
    s = sum(weights)
    return {"side": "long" if long else "short", "entries": list(entries), "weights": [w / s for w in weights], "stop": stop,
            "tps": tps, "rr": round(rr, 2), "score": round(score, 3), "zone": (round(z["lo"], 6), round(z["hi"], 6)),
            "zone_strength": round(z["strength"], 2)}
