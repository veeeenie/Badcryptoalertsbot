"""
EMA setup scanner for Hyperliquid perps.

Every run:
  1. Takes the top N Hyperliquid perps by 24h volume.
  2. Pulls ~2.5 years of daily candles per coin, builds weekly candles (Mon 00:00 UTC, like Binance/TradingView).
  3. Computes 25/50/100 EMAs on daily and weekly closes.
  4. Flags three setups on weekly levels (support long, rejection short, reclaim long), timed with the daily chart.
  5. Writes docs/data/setups.json for the dashboard and sends Telegram alerts for new or triggered setups.

No API keys needed for market data (Hyperliquid public API). Telegram is optional (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

HL_URL = "https://api.hyperliquid.xyz/info"
TOP_N = int(os.getenv("TOP_N", "100"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "docs", "data")
SETUPS_PATH = os.path.join(DATA_DIR, "setups.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")

EMA_LENGTHS = (25, 50, 100)
# only use a weekly/daily EMA once there are at least that many candles (False = draw it anyway, like the chart)
REQUIRE_HISTORY = os.getenv("REQUIRE_HISTORY", "1") == "1"
ALLOW_DAILY_FLIP = True   # trades against the weekly trend when the daily is fully stacked the other way
MIN_RR = 1.5          # rule 8: more than 1:1.5 (blended, from the average entry)
MIN_RR_SHORT = 1.5
MIN_ORDER_USD = 10.0  # rule 4: every tranche order must be at least $10
MAX_DIST = 0.10       # ignore setups whose entry is more than 10% from price
AT_LEVEL = 0.02       # within 2% of entry counts as "at level"
MAX_STOP_PCT = 15.0   # skip setups whose stop is more than 15% from entry (too volatile to size sensibly)
# Under test (17 Oct 2026, backtest variant only; live = 0): also put the stop beyond the high/low of the
# last N weekly candles (current week included) when price has traded through the zone, so the stop
# never sits inside this week's or last week's wick.
RECENT_WEEKS_STOP = int(os.getenv("RECENT_WEEKS_STOP", "0"))
RISK_USD = float(os.getenv("RISK_USD", "2"))  # dollar risk per trade used for the position-size hint
SKIP = {"PURR"}       # spot-like / illiquid names to ignore

session = requests.Session()


# ---------------------------------------------------------------- data

def hl(payload, tries=4):
    for i in range(tries):
        try:
            r = session.post(HL_URL, json=payload, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))
    return None


def universe():
    meta, ctxs = hl({"type": "metaAndAssetCtxs"})
    rows = []
    for asset, ctx in zip(meta["universe"], ctxs):
        name = asset["name"]
        if asset.get("isDelisted") or name in SKIP:
            continue
        try:
            vol = float(ctx.get("dayNtlVlm") or 0)
            mark = float(ctx.get("markPx") or 0)
            prev = float(ctx.get("prevDayPx") or 0)
            funding = float(ctx.get("funding") or 0)
        except (TypeError, ValueError):
            continue
        if mark <= 0:
            continue
        rows.append({
            "coin": name,
            "price": mark,
            "chg24h": (mark / prev - 1) if prev else 0.0,
            "vol24h": vol,
            "funding_8h": funding * 8,  # Hyperliquid funding is hourly
            "max_lev": asset.get("maxLeverage"),
        })
    rows.sort(key=lambda r: r["vol24h"], reverse=True)
    return rows[:TOP_N]


def daily_candles(coin, days=2500):
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    raw = hl({"type": "candleSnapshot",
              "req": {"coin": coin, "interval": "1d", "startTime": start, "endTime": end}}) or []
    out = []
    for c in raw:
        out.append({"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]),
                    "l": float(c["l"]), "c": float(c["c"])})
    out.sort(key=lambda x: x["t"])
    return out


def weekly_from_daily(daily):
    """Aggregate to weeks starting Monday 00:00 UTC. Last week may be partial (live bar)."""
    weeks = []
    cur = None
    for d in daily:
        dt = datetime.fromtimestamp(d["t"] / 1000, tz=timezone.utc)
        wk_start = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        key = int(wk_start.timestamp() * 1000)
        if cur is None or cur["t"] != key:
            if cur:
                weeks.append(cur)
            cur = {"t": key, "o": d["o"], "h": d["h"], "l": d["l"], "c": d["c"]}
        else:
            cur["h"] = max(cur["h"], d["h"])
            cur["l"] = min(cur["l"], d["l"])
            cur["c"] = d["c"]
    if cur:
        weeks.append(cur)
    return weeks


# ---------------------------------------------------------------- indicators

EMA_DROP = 1   # the Krypt 3-EMA indicator drops the first candle ("Drop first N candles" = 1)


def ema(values, n, drop=EMA_DROP):
    """Exactly the TradingView "3-EMA [Krypt]" calculation the trader uses:
    skip the first `drop` candles, start the EMA at the next close, then
    e = k * close + (1 - k) * e with k = 2 / (n + 1). Returns a list aligned with values."""
    out = [None] * len(values)
    if len(values) <= drop:
        return out
    k = 2 / (n + 1)
    e = values[drop]
    out[drop] = e
    for i in range(drop + 1, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def atr(candles, n=14):
    if len(candles) < n + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n  # Wilder
    return a


def last(series, back=0):
    i = len(series) - 1 - back
    return series[i] if i >= 0 else None


def slope(series, bars):
    a, b = last(series), last(series, bars)
    if a is None or b is None or b == 0:
        return None
    return a / b - 1


# ---------------------------------------------------------------- setups

def round_level(x):
    """Round to 3 significant figures for stable setup ids."""
    if x <= 0:
        return x
    from math import floor, log10
    digits = 2 - int(floor(log10(abs(x))))
    return round(x, digits)


STRONG_MOVE_PCT = 25.0     # a 14-day move this big counts as a strong move (rule 6)
STRONG_STRETCH_PCT = 15.0  # ...as does price this far from the daily 25 EMA
TOP_K = int(os.getenv("TOP_K", "5"))   # "Act now" list: the best setups to place limits on / enter (dashboard cards, Telegram)
WATCH_K = 8                             # "Waiting for a daily close" list (compact rows, no alert until confirmed)
# 19 Oct 2026 (trader's decision, backed by the backtest): only rating-A setups are kept, and setups whose
# move already played out (stale) are removed. B/C ratings lost money in the two-year backtest.
KEEP_RATINGS = ("A",)
ACTION_TEXT = {"ENTER": "ENTER", "LIMITS": "PLACE LIMITS", "WAIT": "WAIT FOR CLOSE"}
ALERTS_PAUSED = os.getenv("ALERTS_PAUSED", "0") == "1"   # backtest validated 17 Oct 2026; set ALERTS_PAUSED=1 to pause
MAX_ALERTS = 3                          # most individual alerts per scan; the rest go in one summary line
SCORE_RATING = {"A": 6, "B": 3, "C": 0}
SCORE_STATUS = {"confirmed": 4, "at level": 2, "approaching": 0}
TRANCHE_IN = (0.3, 0.3, 0.4)   # share of the position at each entry, nearest price first
TRANCHE_OUT = (0.5, 0.5)       # share closed at target 1 and target 2


TRANCHE_LAYOUTS = {3: (0.3, 0.3, 0.4), 2: (0.5, 0.5), 1: (1.0,)}


def tranche_prices(zone_prices, n):
    """zone_prices = (near edge, middle, far edge). 3 tranches use all three, 2 use the edges,
    1 uses the middle."""
    near, mid, far = zone_prices
    return {3: [near, mid, far], 2: [near, far], 1: [mid]}[n]


def make_setup(kind, coin, price, zone_prices, stop, t1, t2, reason, level_name, notes=None):
    """Build a tranche setup dict, or return None and append the reason to notes.
    Rule 4: up to 3 tranches, each at least MIN_ORDER_USD; the dollar risk decides how many fit.
    R is measured from the average entry and blends half at T1 and half at T2."""
    tag = "Short" if kind == "short" else "Long"
    sign = -1 if kind == "short" else 1

    def plan(n):
        px = tranche_prices(zone_prices, n)
        w = TRANCHE_LAYOUTS[n]
        e = sum(p * q for p, q in zip(px, w))
        r = sign * (e - stop)
        return px, w, e, r

    px, wts, entry, risk = plan(3)

    def no(msg):
        if notes is not None:
            notes.append(f"{tag} at {level_name} ({fmt(entry)}): {msg}")
        return None
    if t1 is None:
        return no("no weekly target zone beyond the entry")
    if risk <= 0 or sign * (t1 - entry) <= 0:
        return no("no room to a target")
    # how many $10+ tranches fit at this dollar risk
    for n in (3, 2, 1):
        px, wts, entry, risk = plan(n)
        if risk <= 0:
            continue
        size = RISK_USD / (risk / entry)
        if size * min(wts) >= MIN_ORDER_USD:
            break
    else:
        return no(f"at ${RISK_USD:g} risk the position is under the ${MIN_ORDER_USD:g} minimum order")
    if size * min(wts) < MIN_ORDER_USD:
        return no(f"at ${RISK_USD:g} risk the position (${size:.0f}) is under the ${MIN_ORDER_USD:g} minimum order")
    r1 = sign * (t1 - entry) / risk
    r2 = sign * (t2 - entry) / risk if t2 is not None else None
    rr = r1 * TRANCHE_OUT[0] + (r2 if r2 is not None else r1) * TRANCHE_OUT[1]
    dist = (px[0] - price) / price  # first tranche vs price; + means above price
    if abs(dist) > MAX_DIST:
        return no(f"first entry {abs(dist) * 100:.0f}% from price (limit {MAX_DIST * 100:.0f}%)")
    if sign * (price - stop) <= 0:
        return no("price is already past the stop")
    near_edge = zone_prices[0]
    status = "at level" if (price <= near_edge * (1 + AT_LEVEL) if kind == "long" else price >= near_edge * (1 - AT_LEVEL)) else "approaching"
    risk_pct = risk / entry * 100
    if risk_pct > MAX_STOP_PCT:
        return no(f"stop {fmt(stop)} is {risk_pct:.0f}% from the average entry, too wide (limit {MAX_STOP_PCT:.0f}%)")
    need = MIN_RR_SHORT if kind == "short" else MIN_RR
    if rr < need:
        return no(f"targets {fmt(t1)}" + (f" / {fmt(t2)}" if t2 else "") + f" give {rr:.1f}R blended (need {need:g}R)")
    return {
        "id": f"{coin}:{reason['type']}",
        "coin": coin,
        "type": reason["type"],
        "side": kind,
        "status": status,
        "level": level_name,
        "entry": entry,
        "entries": [{"px": p, "pct": int(round(q * 100)), "usd": round(size * q, 2)} for p, q in zip(px, wts)],
        "stop": stop,
        "t1": t1,
        "t2": t2,
        "exits": [{"px": t1, "pct": int(TRANCHE_OUT[0] * 100) if t2 is not None else 100, "r": round(r1, 2)}] +
                 ([{"px": t2, "pct": int(TRANCHE_OUT[1] * 100), "r": round(r2, 2)}] if t2 is not None else []),
        "rr": round(rr, 2),
        "rr2": round(r2, 2) if r2 is not None else None,
        "dist_pct": round(dist * 100, 2),
        "risk_pct": round(risk_pct, 2),
        "size_usd": round(size, 2),
        "why": reason["why"],
    }


def analyse(coin_row, daily):
    coin, price = coin_row["coin"], coin_row["price"]
    if len(daily) < 120:
        return None, []
    # use live mark price as the current bar's close so EMAs match the chart now
    daily = [dict(d) for d in daily]
    daily[-1]["c"] = price
    weekly = weekly_from_daily(daily)

    dc = [d["c"] for d in daily]
    wc = [w["c"] for w in weekly]
    # an EMA is only used once there is at least as much history as its length;
    # a 100-week EMA on 40 weeks of data is meaningless
    hist_ok = lambda vals, n: len(vals) >= n or not REQUIRE_HISTORY
    d = {n: ema(dc, n) if hist_ok(dc, n) else [None] * len(dc) for n in EMA_LENGTHS}
    w = {n: ema(wc, n) if hist_ok(wc, n) else [None] * len(wc) for n in EMA_LENGTHS}
    a = atr(daily) or price * 0.05

    dE = {n: last(d[n]) for n in EMA_LENGTHS}
    wE = {n: last(w[n]) for n in EMA_LENGTHS}
    hi60 = max(x["h"] for x in daily[-60:])
    hi120 = max(x["h"] for x in daily[-120:])
    lo60 = min(x["l"] for x in daily[-60:])
    close_10_ago = dc[-11] if len(dc) > 11 else dc[0]

    info = {
        **coin_row,
        "d_ema": {str(n): dE[n] for n in EMA_LENGTHS},
        "w_ema": {str(n): wE[n] for n in EMA_LENGTHS},
        "atr": a,
        "atr_pct": a / price * 100,
        "stretch_d25": (price / dE[25] - 1) * 100 if dE[25] else None,
        "hi60": hi60,
        "lo60": lo60,
        "weeks_of_data": len(weekly),
        "d_trend": trend_label(dE),
        "w_trend": trend_label(wE),
    }

    notes = []
    missing = [n for n in EMA_LENGTHS if len(wc) < n] if REQUIRE_HISTORY else []
    if missing:
        notes.append(f"Only {len(wc)} weeks of history: no weekly {'/'.join(map(str, missing))} EMA yet" +
                     (" so no setups" if 50 in missing else " so no shorts (they need the weekly 100 EMA)"))

    # ---- weekly levels: the 25/50/100 EMAs plus weekly swing highs and lows (last 2 years),
    #      grouped into zones when they sit within one daily ATR of each other
    closed_w = weekly[:-1]
    raw = [(wE[n], f"weekly {n} EMA") for n in EMA_LENGTHS if wE[n]]
    for v, kind in swing_levels(closed_w[-104:], k=2):
        raw.append((v, f"weekly swing {kind} {fmt(v)}"))
    zones = cluster_levels(raw, a)   # dicts: mid, lo, hi, label, names
    # extremes use weekly closes, not wicks: a one-off wick high is not a realistic target
    ath = max(x["c"] for x in weekly)
    atl = min(x["c"] for x in weekly)

    # weekly trend: which direction the weekly chart allows
    w25_slope = slope(w[25], 4) or 0
    w_long = bool(wE[50]) and price > wE[50] and w25_slope >= -0.01
    w_short = bool(wE[100] and wE[25] and wE[50]) and price < wE[100] and wE[25] < wE[50]
    # rule 1 (as updated): weekly trend first, but a daily that has completely flipped
    # (25/50/100 EMAs fully stacked the other way) also allows trades in the daily's direction
    d_bull = all(dE[n] for n in EMA_LENGTHS) and dE[25] > dE[50] > dE[100]
    d_bear = all(dE[n] for n in EMA_LENGTHS) and dE[25] < dE[50] < dE[100]
    long_ok = w_long or (d_bull and ALLOW_DAILY_FLIP)
    short_ok = w_short or (d_bear and ALLOW_DAILY_FLIP)
    trend_of = {"long": "weekly" if w_long else "daily flip", "short": "weekly" if w_short else "daily flip"}
    if not long_ok:
        notes.append("No longs: weekly trend isn't up and the daily EMAs aren't fully stacked bullish")
    if not short_ok:
        notes.append("No shorts: weekly trend isn't down and the daily EMAs aren't fully stacked bearish")

    # obstacles price must get through before a target: every weekly zone, the daily 25/50/100 EMAs
    # and daily swing highs/lows of the last 6 months. Target 1 is the first one in the way.
    obstacles = [o["mid"] for o in zones] + [v for v in dE.values() if v] + \
                [v for v, _ in swing_levels(daily[-181:-1], k=5)]

    def build(kind, z, typ, why):
        """Plan a tranche trade at weekly zone z. Stop sits beyond the zone and beyond any weekly
        level just past it (supply/demand that would otherwise sit right behind the stop)."""
        lo, hi = min(z["lo"], z["mid"] - 0.25 * a), max(z["hi"], z["mid"] + 0.25 * a)
        if kind == "long":
            entries = (hi, z["mid"], lo)                      # scale in from the top of the zone down
            stop = lo - 0.5 * a
            for o in sorted([o for o in zones if o["hi"] < lo], key=lambda o: -o["hi"]):
                if o["hi"] >= stop - a:                       # another level right under the stop: go below it
                    stop = o["lo"] - 0.5 * a
                else:
                    break
            if RECENT_WEEKS_STOP:
                rl = min(x["l"] for x in weekly[-RECENT_WEEKS_STOP:])
                if rl < lo:
                    stop = min(stop, rl - 0.5 * a)
            obst = sorted([v for v in obstacles if v > hi + 0.5 * a])
            wk = sorted([o["mid"] for o in zones if o["lo"] > hi + 0.5 * a] + ([ath] if ath > hi + a else []))
            t1 = obst[0] if obst else (wk[0] if wk else None)
            t2 = next((v for v in wk if t1 is not None and v > t1 + a), None)
            tgts = [v for v in (t1, t2) if v is not None]
        else:
            entries = (lo, z["mid"], hi)                      # scale in from the bottom of the zone up
            stop = hi + 0.5 * a
            for o in sorted([o for o in zones if o["lo"] > hi], key=lambda o: o["lo"]):
                if o["lo"] <= stop + a:
                    stop = o["hi"] + 0.5 * a
                else:
                    break
            if RECENT_WEEKS_STOP:
                rh = max(x["h"] for x in weekly[-RECENT_WEEKS_STOP:])
                if rh > hi:
                    stop = max(stop, rh + 0.5 * a)
            obst = sorted([v for v in obstacles if v < lo - 0.5 * a], reverse=True)
            wk = sorted([o["mid"] for o in zones if o["hi"] < lo - 0.5 * a] + ([atl] if atl < lo - a else []),
                        reverse=True)
            t1 = obst[0] if obst else (wk[0] if wk else None)
            t2 = next((v for v in wk if t1 is not None and v < t1 - a), None)
            tgts = [v for v in (t1, t2) if v is not None]
        t1 = tgts[0] if tgts else None
        t2 = tgts[1] if len(tgts) > 1 else None
        s_ = make_setup(kind, coin, price, entries, stop, t1, t2, {"type": typ, "why": why}, z["label"], notes)
        if s_:
            s_["zone"] = [lo, hi]
        return s_

    candidates = []

    # ---- A) weekly reclaim long: last completed week closed back above the weekly 50 or 100 EMA
    if long_ok and len(closed_w) >= 2:
        for n in (100, 50):
            cur, prev = w[n][len(closed_w) - 1], w[n][len(closed_w) - 2]
            if cur and prev and closed_w[-1]["c"] > cur and closed_w[-2]["c"] <= prev and price > wE[n] * (1 - AT_LEVEL):
                z = next((o for o in zones if f"weekly {n} EMA" in o["names"]), None)
                if z:
                    candidates.append(build("long", z, "reclaim_long",
                        f"Last week closed back above the weekly {n} EMA. Buy the retest in tranches; "
                        f"wrong on a weekly close back below the zone."))
                break

    # ---- B) weekly support long: weekly uptrend, buy a pullback into the nearest weekly zone below
    #         (if the nearest zone doesn't work, try the next one down)
    if long_ok and not any(candidates):
        below = sorted([o for o in zones if o["mid"] < price * (1 + AT_LEVEL)], key=lambda o: -o["mid"])
        if not below:
            notes.append("Long: no weekly level below price")
        for z in below[:2]:
            s_ = build("long", z, "support_long",
                       f"Weekly uptrend (price above the weekly 50 EMA). Buy a pullback into the {z['label']} "
                       f"in tranches; wrong on a weekly close below the zone.")
            if s_:
                candidates.append(s_)
                break

    # ---- C) weekly rejection short: weekly downtrend, short a rally into the nearest weekly zone above
    if short_ok:
        above = sorted([o for o in zones if o["mid"] > price * (1 - AT_LEVEL)], key=lambda o: o["mid"])
        if not above:
            notes.append("Short: no weekly level above price")
        tried = 0
        for z in above:
            ema_members = [m for m in z["names"] if "EMA" in m]
            if ema_members and len(ema_members) == len(z["names"]) and \
                    all((slope(w[int(m.split()[1])], 4) or 0) > 0.01 for m in ema_members):
                notes.append(f"Short at {z['label']}: the weekly EMA there is rising, not fading it")
                continue
            s_ = build("short", z, "rejection_short",
                       f"Weekly downtrend (below the weekly 100 EMA, 25 under 50). Short a rally into the {z['label']} "
                       f"in tranches; wrong on a weekly close above the zone.")
            tried += 1
            if s_:
                candidates.append(s_)
                break
            if tried >= 2:
                break

    setups = [x for x in candidates if x]
    for x in setups:
        x["trend"] = trend_of[x["side"]]
        if x["trend"] == "daily flip":
            x["why"] = (f"Against the weekly trend: the daily 25/50/100 EMAs are fully stacked "
                        f"{'bullish' if x['side'] == 'long' else 'bearish'}. " + x["why"].split(". ", 1)[-1])
        # rule 2 (as updated): exit the runner on the weekly
        x["runner"] = {"ema": "weekly 25 EMA", "value": wE[25]}

    # ---- daily timing, judged on closed daily candles (today's candle is still forming):
    #      confirmed = the zone was traded into in the last 3 closed days AND the last closed day
    #      closed back out of the zone in the trade's direction, as a candle in that direction
    kept = []
    closed_d = daily[-4:-1]
    last_d = daily[-2]
    for x in setups:
        lo, hi = x["zone"]
        if x["side"] == "long":
            if last_d["c"] < lo - 0.5 * a or price < lo - 0.5 * a:
                notes.append(f"Long at {x['level']}: price broke below the zone, retest failed")
                continue
            if any(b["l"] <= hi for b in closed_d) and last_d["c"] > hi and last_d["c"] > last_d["o"] and price > hi:
                x["status"] = "confirmed"
                x["why"] += " Daily chart: traded into the zone and the last daily candle closed green, back above it."
        else:
            if last_d["c"] > hi + 0.5 * a or price > hi + 0.5 * a:
                notes.append(f"Short at {x['level']}: price broke above the zone, no rejection")
                continue
            if any(b["h"] >= lo for b in closed_d) and last_d["c"] < lo and last_d["c"] < last_d["o"] and price < lo:
                x["status"] = "confirmed"
                x["why"] += " Daily chart: traded into the zone and the last daily candle closed red, back below it."
        kept.append(x)
    setups = kept

    # ---- stale confirmation: the rejection already ran to T1 (or price has left the zone by more than
    #      one daily ATR). The resting limits are still the plan, but it now needs a fresh retest, so it is
    #      shown as approaching rather than confirmed (no change to the rules, only the label and ranking).
    for x in setups:
        if x["status"] != "confirmed":
            continue
        lo, hi = x["zone"]
        recent = daily[-6:]
        if x["side"] == "short":
            ran = min(b["l"] for b in recent) <= x["t1"]
            away = price < lo - a
        else:
            ran = max(b["h"] for b in recent) >= x["t1"]
            away = price > hi + a
        if ran or away:
            x["status"] = "approaching"
            x["stale"] = True
            x["why"] = x["why"].replace(" Daily chart: traded into the zone and the last daily candle closed red, back below it.", "") \
                               .replace(" Daily chart: traded into the zone and the last daily candle closed green, back above it.", "")
            x["why"] += (" The last rejection already ran to T1" if ran else " Price has already moved away from the zone") + \
                        ": don't chase it; the resting limits only fill on a fresh retest of the zone."

    # ---- price already beyond T1 (the zone is still ahead, but the move looks "done" from here);
    #      flagged only, tested in the backtest before it is used to remove anything
    for x in setups:
        x["past_t1"] = bool(x.get("t1")) and (price < x["t1"] if x["side"] == "short" else price > x["t1"])

    # ---- entry rule: price already in the zone -> enter only after a confirmed daily rejection
    for x in setups:
        if x["status"] == "at level":
            x["needs_confirmation"] = True
            x["why"] += " Price is already in the zone: enter only after a confirmed daily rejection."

    # ---- rule 5: against a strong move, only take the trade after a confirmed daily rejection
    chg14 = (price / dc[-15] - 1) * 100 if len(dc) > 15 else 0
    stretch = info["stretch_d25"] or 0
    info["chg14"] = round(chg14, 1)
    for x in setups:
        strong_up = chg14 >= STRONG_MOVE_PCT or stretch >= STRONG_STRETCH_PCT
        strong_down = chg14 <= -STRONG_MOVE_PCT or stretch <= -STRONG_STRETCH_PCT
        if (x["side"] == "short" and strong_up) or (x["side"] == "long" and strong_down):
            x["needs_confirmation"] = True
            x["why"] += (f" Strong move ({chg14:+.0f}% in 14 days, {stretch:+.0f}% from the daily 25 EMA)" +
                         (": the confirmation above is what rule 5 requires." if x["status"] == "confirmed"
                          else ": wait for a confirmed daily rejection before entering."))

    # ---- confluence: other weekly levels in the same zone, plus daily EMAs within the zone (+/- 1 ATR)
    by_label = {z["label"]: z for z in zones}
    for x in setups:
        z = by_label.get(x["level"])
        near = list(z["names"][1:]) if z else []
        for n in EMA_LENGTHS:
            if dE[n] and x["zone"][0] - a <= dE[n] <= x["zone"][1] + a:
                near.append(f"daily {n} EMA")
        x["confluence"] = near
        weekly_hits = sum(1 for nm in near if nm.startswith("weekly"))
        x["rating"] = "A" if weekly_hits >= 1 and len(near) >= 2 else ("B" if near else "C")

    # ---- extra confluence (measured, not yet used for the rating): fib retracements of the last major
    #      weekly swing (the move into the current 52-week high or low) and of the all-time low-to-high
    #      move, areas of interest (levels with 3+ weekly swing touches), and monthly 25/50/100 EMAs
    if setups:
        extra_levels = []
        wk = weekly[-52:]
        hi_i = max(range(len(wk)), key=lambda i: wk[i]["h"]); lo_i = min(range(len(wk)), key=lambda i: wk[i]["l"])
        for v, r in fib_levels(wk[hi_i]["h"], wk[lo_i]["l"], hi_i > lo_i):
            extra_levels.append((v, f"fib {r} (last swing)", "fib_swing"))
        hi_i = max(range(len(weekly)), key=lambda i: weekly[i]["h"]); lo_i = min(range(len(weekly)), key=lambda i: weekly[i]["l"])
        for v, r in fib_levels(weekly[hi_i]["h"], weekly[lo_i]["l"], hi_i > lo_i):
            extra_levels.append((v, f"fib {r} (ATL–ATH)", "fib_macro"))
        for v, n in areas_of_interest(closed_w, 0.6 * a):
            extra_levels.append((v, f"area of interest {fmt(v)} ({n} touches)", "aoi"))
        mc = [m["c"] for m in monthly_from_daily(daily)]
        for n in EMA_LENGTHS:
            if len(mc) >= n:
                extra_levels.append((ema(mc, n)[-1], f"monthly {n} EMA", "monthly"))
        for x in setups:
            lo, hi = x["zone"]
            hit = [(name, kind) for v, name, kind in extra_levels if v and lo - 0.5 * a <= v <= hi + 0.5 * a]
            x["extra"] = [name for name, _ in hit]
            x["extra_kinds"] = sorted({kind for _, kind in hit})
            # A+ = rating A plus an area of interest (a level with 3+ weekly touches) at the zone.
            # Backtest 19 Oct 2026: +0.194R a trade vs +0.109R without; fibs and monthly EMAs showed no edge.
            x["aoi"] = [name for name, kind in hit if kind == "aoi"]
            x["grade"] = "A+" if x["aoi"] and x["rating"] == "A" else x["rating"]

    info["notes"] = notes[:6]
    return info, setups


def cluster_levels(raw, width):
    """Group levels closer than `width` into zones.
    Returns dicts: mid (mean), lo, hi, label (leads with the strongest member, an EMA if any), names."""
    raw = sorted([r for r in raw if r[0]], key=lambda r: r[0])
    groups, cur = [], []
    for v, name in raw:
        # start a new zone when the gap is wider than one ATR, or the zone would span more than 1.5 ATR
        if cur and (v - cur[-1][0] > width or v - cur[0][0] > 1.5 * width):
            groups.append(cur); cur = []
        cur.append((v, name))
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        vals = [m[0] for m in g]
        g.sort(key=lambda m: (0 if "EMA" in m[1] else 1, -int(m[1].split()[1]) if "EMA" in m[1] else 0))
        names = [m[1] for m in g]
        label = names[0] if len(names) == 1 else f"{names[0]} zone ({len(names)} levels)"
        out.append({"mid": sum(vals) / len(vals), "lo": min(vals), "hi": max(vals), "label": label, "names": names})
    return out


def swing_levels(candles, k=5):
    """Daily swing highs/lows (a bar higher/lower than the k bars either side), newest first."""
    out = []
    for i in range(len(candles) - k - 1, k - 1, -1):
        h = candles[i]["h"]; l = candles[i]["l"]
        win = candles[i - k:i + k + 1]
        if h == max(c["h"] for c in win):
            out.append((h, "high"))
        if l == min(c["l"] for c in win):
            out.append((l, "low"))
    return out


FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)


def fib_levels(hi, lo, up):
    """Retracement levels of a move. up=True: the move went up into `hi` (levels sit below hi)."""
    rng = hi - lo
    return [((hi - r * rng) if up else (lo + r * rng), r) for r in FIB_RATIOS]


def monthly_from_daily(daily):
    """Calendar-month candles (UTC). Last month may be partial."""
    out, cur, key = [], None, None
    for d in daily:
        dt = datetime.fromtimestamp(d["t"] / 1000, tz=timezone.utc)
        k = (dt.year, dt.month)
        if k != key:
            if cur:
                out.append(cur)
            key, cur = k, {"t": d["t"], "o": d["o"], "h": d["h"], "l": d["l"], "c": d["c"]}
        else:
            cur["h"] = max(cur["h"], d["h"]); cur["l"] = min(cur["l"], d["l"]); cur["c"] = d["c"]
    if cur:
        out.append(cur)
    return out


def areas_of_interest(weekly_closed, width, min_touches=3):
    """Horizontal levels price reacted to several times: weekly swing highs and lows (all history)
    grouped within `width`; a group with at least `min_touches` pivots is an area of interest."""
    piv = sorted(v for v, _ in swing_levels(weekly_closed, k=2))
    groups, cur = [], []
    for v in piv:
        if cur and v - cur[0] > width:
            groups.append(cur); cur = []
        cur.append(v)
    if cur:
        groups.append(cur)
    return [(sum(g) / len(g), len(g)) for g in groups if len(g) >= min_touches]


def trend_label(E):
    a, b, c = E[25], E[50], E[100]
    if not (a and b and c):
        return "n/a"
    if a > b > c:
        return "up"
    if a < b < c:
        return "down"
    return "mixed"


# ---------------------------------------------------------------- telegram

def fmt(x):
    if x is None:
        return "-"
    if x >= 1000:
        return f"{x:,.0f}"
    if x >= 1:
        return f"{x:.3f}"
    if x >= 0.01:
        return f"{x:.4f}"
    return f"{x:.6f}"


LABEL = {"support_long": "Weekly support LONG", "rejection_short": "Weekly rejection SHORT",
         "reclaim_long": "Weekly reclaim LONG"}


def telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=20)
        if not r.ok:
            print("telegram error", r.status_code, r.text[:200], file=sys.stderr)
        return r.ok
    except requests.RequestException as e:
        print("telegram error", e, file=sys.stderr)
        return False


def alert_text(s, price):
    side = "🔴" if s["side"] == "short" else "🟢"
    act = ACTION_TEXT[s["action"]]
    head = {"ENTER": "daily close confirmed the rejection: place the orders now",
            "LIMITS": "price is heading to the zone: place the limits, stop and both take-profits now"}[s["action"]]
    ins = " / ".join(f"{fmt(e['px'])}" for e in s["entries"])
    t2 = f" · T2 {fmt(s['t2'])}" if s.get("t2") else ""
    lines = [
        f"{side} <b>{s['coin']} {s['side'].upper()}</b> · <b>{act}</b>",
        head,
        f"Zone {fmt(s['zone'][0])}–{fmt(s['zone'][1])} (price {fmt(price)})",
        f"Limits {ins} · Stop {fmt(s['stop'])}",
        f"T1 {fmt(s['t1'])} (half, then stop to breakeven){t2} · {s['rr']}R",
        f"${RISK_USD:g} risk → ${s['size_usd']:,.0f} position",
    ]
    if s.get("trend") == "daily flip":
        lines.append("Against the weekly trend (daily flip)")
    if s.get("unlock"):
        u = s["unlock"]
        lines.append(f"⚠️ Unlock {u['date']}: {u.get('pct', '?')}% of supply")
    return "\n".join(lines)


# ---------------------------------------------------------------- unlocks

INTEL_PATH = os.path.join(DATA_DIR, "intel.json")
UNLOCK_DAYS = 14
UNLOCK_MIN_PCT = 1.0


def annotate_unlocks(setups):
    """Mark setups with a large unlock (>= 1% of supply) in the next 14 days, from the daily intel file."""
    try:
        with open(INTEL_PATH) as f:
            intel = json.load(f).get("coins", {})
    except (OSError, ValueError):
        return
    today = datetime.now(timezone.utc).date()
    for s in setups:
        u = (intel.get(s["coin"]) or {}).get("unlock") or {}
        try:
            when = datetime.strptime(str(u.get("date_iso") or u.get("date", ""))[:10], "%Y-%m-%d").date()
            pct = float(str(u.get("pct_supply", "")).rstrip("%").strip())
        except ValueError:
            continue
        if 0 <= (when - today).days <= UNLOCK_DAYS and pct >= UNLOCK_MIN_PCT:
            s["unlock"] = {"date": when.isoformat(), "pct": pct}


# ---------------------------------------------------------------- attention

def coingecko_trending():
    """Symbols of the coins most searched on CoinGecko in the last 24h (free, no key)."""
    try:
        r = requests.get("https://api.coingecko.com/api/v3/search/trending", timeout=20)
        r.raise_for_status()
        return {c["item"]["symbol"].upper() for c in r.json().get("coins", [])}
    except Exception as e:
        print("trending error", e, file=sys.stderr)
        return set()


# ---------------------------------------------------------------- main

def main():
    started = time.time()
    coins = universe()
    print(f"universe: {len(coins)} coins")
    all_info, all_setups, errors = [], [], []
    for row in coins:
        try:
            daily = daily_candles(row["coin"])
            info, setups = analyse(row, daily)
            if info:
                all_info.append(info)
            all_setups.extend(setups)
        except Exception as e:  # keep scanning other coins
            errors.append(f"{row['coin']}: {e}")
        time.sleep(0.35)  # stay well inside Hyperliquid's rate limit

    dropped = {"rating": sum(1 for s in all_setups if s.get("rating") not in KEEP_RATINGS),
               "stale": sum(1 for s in all_setups if s.get("rating") in KEEP_RATINGS and s.get("stale"))}
    all_setups = [s for s in all_setups if s.get("rating") in KEEP_RATINGS and not s.get("stale")]
    order = {"confirmed": 0, "at level": 1, "approaching": 2}
    all_setups.sort(key=lambda s: (s.get("rating", "C"), order.get(s["status"], 2), -s["rr"]))
    # one setup per coin: keep the best (at level first, then highest R)
    best = {}
    for s in all_setups:
        best.setdefault(s["coin"], s)
    all_setups = [s for s in all_setups if best[s["coin"]] is s]
    annotate_unlocks(all_setups)
    trending = coingecko_trending()
    for s in all_setups:
        if s["coin"].lstrip("k").upper() in trending:
            s["trending"] = True
    for c in all_info:
        c["trending"] = c["coin"].lstrip("k").upper() in trending

    # ---- rank: only the best TOP_K setups are "top" (shown first, and the only ones that alert)
    for s in all_setups:
        s["score"] = round(SCORE_RATING.get(s.get("rating"), 0) + SCORE_STATUS.get(s["status"], 0)
                           + (2 if s.get("trend") == "weekly" else 0)
                           + (3 if s.get("grade") == "A+" else 0)
                           + min(s["rr"], 6) * 0.5 - abs(s["dist_pct"]) * 0.1
                           - (2 if s.get("needs_confirmation") and s["status"] != "confirmed" else 0)
                           - (2 if s.get("unlock") and s["side"] == "long" else 0), 2)
    all_setups.sort(key=lambda s: -s["score"])
    # ---- one plain action per setup:
    #      ENTER  = the daily close confirmed the rejection: place the orders now
    #      WAIT   = price is in the zone or it's against a strong move: nothing until a daily close confirms
    #      LIMITS = price is heading to the zone: place the resting limits, stop and take-profits now
    for s in all_setups:
        if s["status"] == "confirmed":
            s["action"] = "ENTER"
        elif s.get("needs_confirmation"):
            s["action"] = "WAIT"
        else:
            s["action"] = "LIMITS"
    act = [s for s in all_setups if s["action"] in ("ENTER", "LIMITS")]
    act.sort(key=lambda s: (0 if s["action"] == "ENTER" else 1, -s["score"]))
    wait = [s for s in all_setups if s["action"] == "WAIT"]
    for s in all_setups:
        s["top"] = False
        s["watch"] = False
    for s in act[:TOP_K]:
        s["top"] = True
    for s in wait[:WATCH_K]:
        s["watch"] = True

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = {
        "generated_at": now,
        "source": "Hyperliquid perps, daily candles; weekly built Mon 00:00 UTC",
        "risk_usd": RISK_USD,
        "alerts_paused": ALERTS_PAUSED,
        "universe_size": len(all_info),
        "setups": all_setups,
        "dropped": dropped,
        "keep_ratings": list(KEEP_RATINGS),
        "coins": sorted(all_info, key=lambda r: -r["vol24h"]),
        "errors": errors[:20],
        "runtime_s": round(time.time() - started, 1),
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SETUPS_PATH, "w") as f:
        json.dump(out, f, separators=(",", ":"))

    # ---- alerts: only when a setup reaches its entry, at most once a day per setup
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    seen = state.get("seen", {})
    alerted = state.get("alerted", {})
    first_run = not seen and not alerted
    price_of = {c["coin"]: c["price"] for c in all_info}
    msgs = []
    new_seen = {}
    now_ts = time.time()
    live_ids = {x["id"] for x in all_setups}
    # forget alerts only once a setup has left the scan, so each setup alerts at most once per stage
    alerted = {k: v for k, v in alerted.items() if k.rsplit(":", 1)[0] in live_ids or now_ts - v < 86400}
    v3 = state.get("version") == 3
    due = []
    for s in all_setups:
        new_seen[s["id"]] = s["action"]
        # alert when a setup joins the Act-now list (place limits) and when a rejection is confirmed (enter);
        # never for "wait for close" — that one pings only once the daily close confirms it
        if s["action"] == "WAIT" or not s.get("top"):
            continue
        key = s["id"] + ":" + s["action"]
        if key in alerted:
            continue
        alerted[key] = now_ts
        if first_run or not v3:
            continue                          # first run of this version: mark as sent, stay quiet
        due.append(s)
    due.sort(key=lambda s: 0 if s["action"] == "ENTER" else 1)
    for s in due[:MAX_ALERTS]:
        msgs.append(alert_text(s, price_of[s["coin"]]))
    if len(due) > MAX_ALERTS:
        msgs.append(f"+{len(due) - MAX_ALERTS} more: " +
                    ", ".join(f"{x['coin']} ({ACTION_TEXT[x['action']]})" for x in due[MAX_ALERTS:]) +
                    ". Details on the dashboard.")
    if (first_run or not v3) and all_setups:
        acts = [x for x in all_setups if x.get("top")]
        waits = [x for x in all_setups if x.get("watch")]
        msgs.append("<b>Setup Desk updated: A-rated setups only, played-out moves removed.</b>\n\n"
                    "<b>Act now</b>\n" + ("\n".join(f"• {x['coin']} {x['side'].upper()} · {ACTION_TEXT[x['action']]} · "
                                                      f"zone {fmt(x['zone'][0])}–{fmt(x['zone'][1])} · {x['rr']}R" for x in acts) or "none") +
                    "\n\n<b>Waiting for a daily close</b>\n" +
                    (", ".join(f"{x['coin']} {x['side']}" for x in waits) or "none") +
                    "\n\nYou'll get a ping when a new setup joins Act now, and when a wait turns into ENTER.")
    if ALERTS_PAUSED:
        print(f"alerts paused: {len(msgs)} message(s) not sent")
    else:
        for m in msgs:
            telegram(m)
            time.sleep(1)
    with open(STATE_PATH, "w") as f:
        json.dump({"version": 3, "seen": new_seen, "alerted": alerted, "updated": now}, f)

    print(f"setups: {len(all_setups)}, alerts: {len(msgs)}, errors: {len(errors)}, "
          f"time: {out['runtime_s']}s")
    if errors:
        print("\n".join(errors[:20]), file=sys.stderr)
    if not all_info:
        sys.exit("No coins analysed — check the Hyperliquid API response.")


if __name__ == "__main__":
    main()
