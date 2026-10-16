"""
EMA setup scanner for Hyperliquid perps.

Every run:
  1. Takes the top N Hyperliquid perps by 24h volume.
  2. Pulls ~2.5 years of daily candles per coin, builds weekly candles (Mon 00:00 UTC, like Binance/TradingView).
  3. Computes 25/50/100 EMAs on daily and weekly closes.
  4. Flags three setups: pullback long, rejection short, reclaim/breakout long.
  5. Writes docs/data/setups.json for the dashboard and sends Telegram alerts for new or triggered setups.

No API keys needed for market data. Telegram is optional (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID).
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
MIN_RR = 2.5          # minimum reward:risk to target 1 for longs
MIN_RR_SHORT = 2.0    # shorts target a nearer level, slightly lower bar
MAX_DIST = 0.18       # ignore setups whose entry is more than 18% from price
AT_LEVEL = 0.02       # within 2% of entry counts as "at level"
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

def ema(values, n):
    """TradingView-style EMA (ta.ema): seeded with the first close, so it has a value from the
    first bar, like the chart. Returns a list aligned with values."""
    out = [None] * len(values)
    if not values:
        return out
    k = 2 / (n + 1)
    e = values[0]
    out[0] = e
    for i in range(1, len(values)):
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


def make_setup(kind, coin, price, entry, stop, t1, t2, reason, level_name):
    if kind == "short":
        risk = stop - entry
        reward = entry - t1
    else:
        risk = entry - stop
        reward = t1 - entry
    if risk <= 0 or reward <= 0:
        return None
    rr = reward / risk
    dist = (entry - price) / price  # + means entry is above price
    if abs(dist) > MAX_DIST:
        return None
    if kind == "short":
        status = "at level" if price >= entry * (1 - AT_LEVEL) and price < stop else "approaching"
        if price >= stop:
            return None
    else:
        status = "at level" if price <= entry * (1 + AT_LEVEL) and price > stop else "approaching"
        if price <= stop:
            return None
    rr2 = None
    if t2 is not None:
        rr2 = ((entry - t2) if kind == "short" else (t2 - entry)) / risk
    return {
        "id": f"{coin}:{reason['type']}:{level_name}",
        "coin": coin,
        "type": reason["type"],
        "side": kind,
        "status": status,
        "level": level_name,
        "entry": entry,
        "stop": stop,
        "t1": t1,
        "t2": t2,
        "rr": round(rr, 2),
        "rr2": round(rr2, 2) if rr2 is not None else None,
        "dist_pct": round(dist * 100, 2),
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
    d = {n: ema(dc, n) for n in EMA_LENGTHS}
    w = {n: ema(wc, n) for n in EMA_LENGTHS}
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

    setups = []

    # ---- A) pullback long: daily uptrend, entry at the nearest rising EMA below price
    up = all(dE[n] for n in EMA_LENGTHS) and dE[25] > dE[50] > dE[100]
    rising = (slope(d[50], 5) or 0) > 0 and (slope(d[100], 5) or 0) > 0
    if up and rising and price > dE[100]:
        supports = sorted(
            [(dE[25], "daily 25 EMA"), (dE[50], "daily 50 EMA"), (dE[100], "daily 100 EMA")] +
            [(wE[n], f"weekly {n} EMA") for n in EMA_LENGTHS if wE[n] and wE[n] < price],
            key=lambda x: -x[0])
        for lvl, name in supports:
            if lvl >= price * (1 + AT_LEVEL):
                continue
            entry = lvl
            stop = entry - 1.5 * a
            t1 = hi60 if hi60 > entry * 1.02 else hi120
            overhead = [wE[n] for n in EMA_LENGTHS if wE[n] and wE[n] > t1 * 1.02]
            t2 = min(overhead) if overhead else (hi120 if hi120 > t1 * 1.02 else None)
            s = make_setup("long", coin, price, entry, stop, t1, t2,
                           {"type": "pullback_long",
                            "why": f"Daily uptrend (25 > 50 > 100 EMA, 50 and 100 rising). Buy a pullback into the {name}."},
                           name)
            if s and s["rr"] >= MIN_RR:
                setups.append(s)
                break

    # ---- B) rejection short: price rallying into a flat/falling higher-timeframe EMA overhead
    rallied = price > close_10_ago * 1.10 or (info["stretch_d25"] or 0) > 15
    for n in (50, 100):
        L = wE[n]
        if not L or L <= price * (1 - MAX_DIST):
            continue
        sl4 = slope(w[n], 4)
        if sl4 is None or sl4 > 0.01:      # skip if the weekly EMA is clearly rising
            continue
        if price > L * 1.04:                # already well above it: not a rejection setup
            continue
        if not rallied:
            continue
        entry = max(L, price) if price > L else L
        stop = entry + 1.5 * a
        t1 = dE[25] if dE[25] and dE[25] < entry * 0.97 else lo60
        t2 = dE[50] if dE[50] and dE[50] < t1 * 0.98 else None
        s = make_setup("short", coin, price, entry, stop, t1, t2,
                       {"type": "rejection_short",
                        "why": f"Rally into a {'falling' if sl4 < -0.003 else 'flat'} weekly {n} EMA. "
                               f"Fade the first test; wrong on a weekly close above it."},
                       f"weekly {n} EMA")
        if s and s["rr"] >= MIN_RR_SHORT:
            setups.append(s)
            break

    # ---- C) reclaim / breakout: a daily close crossed above a key EMA in the last 5 days
    levels = [(d[100], f"daily 100 EMA", True)] + [(w[n], f"weekly {n} EMA", False) for n in (50, 100)]
    for series, name, is_daily in levels:
        lvl_now = last(series)
        if not lvl_now:
            continue
        crossed = False
        for back in range(0, 5):
            i = len(dc) - 1 - back
            if i < 1:
                break
            if is_daily:
                cur_l, prev_l = series[i], series[i - 1]
            else:
                cur_l = prev_l = lvl_now  # weekly EMA moves slowly; compare daily closes to today's value
            if cur_l is None or prev_l is None:
                continue
            if dc[i] > cur_l and dc[i - 1] <= prev_l:
                crossed = True
                break
        if not crossed or price < lvl_now:
            continue
        entry = lvl_now
        stop = entry - 1.2 * a
        hi90 = max(x["h"] for x in daily[-90:])
        overhead = sorted([v for v in [wE[25], wE[50], wE[100], hi90] if v and v > entry * 1.03])
        if not overhead:
            continue
        t1 = overhead[0]
        t2 = overhead[1] if len(overhead) > 1 else None
        s = make_setup("long", coin, price, entry, stop, t1, t2,
                       {"type": "reclaim_long",
                        "why": f"Daily close back above the {name}. Buy the retest; wrong on a close back below."},
                       name)
        if s and s["rr"] >= MIN_RR:
            setups.append(s)
            break

    # ---- weekly agreement: only trade in the direction the weekly chart allows
    w25_slope = slope(w[25], 4) or 0
    long_ok = bool(wE[50]) and price > wE[50] and w25_slope >= -0.01
    short_ok = bool(wE[100]) and price < wE[100] and bool(wE[25] and wE[50]) and wE[25] < wE[50]
    setups = [x for x in setups if (long_ok if x["side"] == "long" else short_ok)]

    # ---- confluence: other levels within 1 ATR of the entry
    pivots = swing_levels(daily[-365:])
    for x in setups:
        near = []
        for n in EMA_LENGTHS:
            for v, name in ((dE[n], f"daily {n} EMA"), (wE[n], f"weekly {n} EMA")):
                if v and name != x["level"] and abs(v - x["entry"]) <= a:
                    near.append(name)
        for v, kind in pivots:
            if abs(v - x["entry"]) <= a:
                near.append(f"swing {kind} {fmt(v)}")
                break
        x["confluence"] = near
        x["rating"] = "A" if len(near) >= 2 else ("B" if near else "C")

    return info, setups


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


LABEL = {"pullback_long": "Pullback LONG", "rejection_short": "Rejection SHORT", "reclaim_long": "Reclaim LONG"}


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


def alert_text(s, price, reason_head):
    side = "🔴" if s["side"] == "short" else "🟢"
    lines = [
        f"{side} <b>{s['coin']}</b> · {LABEL[s['type']]} · {reason_head}",
        f"Price {fmt(price)} · entry {fmt(s['entry'])} ({s['dist_pct']:+.1f}%)",
        f"Stop {fmt(s['stop'])} · T1 {fmt(s['t1'])} ({s['rr']}R)" +
        (f" · T2 {fmt(s['t2'])} ({s['rr2']}R)" if s.get("t2") else ""),
        s["why"],
    ]
    if s.get("confluence"):
        lines.append(f"Rating {s['rating']}: lines up with " + ", ".join(s["confluence"]))
    if s.get("unlock"):
        u = s["unlock"]
        lines.append(f"⚠️ Unlock {u['date']}: {u.get('pct', '?')}% of supply "
                     f"({'risk for this long' if s['side'] == 'long' else 'supports the short'})")
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

    order = {"at level": 0, "approaching": 1}
    all_setups.sort(key=lambda s: (s.get("rating", "C"), order.get(s["status"], 2), -s["rr"]))
    # one setup per coin: keep the best (at level first, then highest R)
    best = {}
    for s in all_setups:
        best.setdefault(s["coin"], s)
    all_setups = [s for s in all_setups if best[s["coin"]] is s]
    annotate_unlocks(all_setups)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = {
        "generated_at": now,
        "source": "Hyperliquid perps, daily candles; weekly built Mon 00:00 UTC",
        "universe_size": len(all_info),
        "setups": all_setups,
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
    alerted = {k: v for k, v in alerted.items() if now_ts - v < 86400}
    legacy = bool(state) and "alerted" not in state  # older state file: mark current setups as already sent
    for s in all_setups:
        new_seen[s["id"]] = s["status"]
        if legacy and s["status"] == "at level":
            alerted[s["id"]] = now_ts
            continue
        if first_run or s["status"] != "at level" or s["id"] in alerted or s.get("rating") == "C":
            continue
        msgs.append(alert_text(s, price_of[s["coin"]], "price at entry"))
        alerted[s["id"]] = now_ts
    if first_run and all_setups:
        top = all_setups[:10]
        msgs.append("<b>Setup scanner is live.</b> Current setups:\n\n" +
                    "\n".join(f"• {s['coin']} {LABEL[s['type']]} entry {fmt(s['entry'])} "
                              f"({s['dist_pct']:+.1f}%), {s['rr']}R, {s['status']}" for s in top))
    for m in msgs[:15]:
        telegram(m)
        time.sleep(1)
    with open(STATE_PATH, "w") as f:
        json.dump({"seen": new_seen, "alerted": alerted, "updated": now}, f)

    print(f"setups: {len(all_setups)}, alerts: {len(msgs)}, errors: {len(errors)}, "
          f"time: {out['runtime_s']}s")
    if errors:
        print("\n".join(errors[:20]), file=sys.stderr)
    if not all_info:
        sys.exit("No coins analysed — check the Hyperliquid API response.")


if __name__ == "__main__":
    main()
