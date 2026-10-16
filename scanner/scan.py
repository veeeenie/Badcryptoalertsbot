"""
EMA setup scanner for Hyperliquid perps.

Every run:
  1. Takes the top N Hyperliquid perps by 24h volume.
  2. Pulls ~2.5 years of daily candles per coin, builds weekly candles (Mon 00:00 UTC, like Binance/TradingView).
  3. Computes 25/50/100 EMAs on daily and weekly closes.
  4. Flags three setups on weekly levels (support long, rejection short, reclaim long), timed with the daily chart.
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
MAX_DIST = 0.10       # ignore setups whose entry is more than 10% from price
AT_LEVEL = 0.02       # within 2% of entry counts as "at level"
MAX_STOP_PCT = 15.0   # skip setups whose stop is more than 15% from entry (too volatile to size sensibly)
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


def make_setup(kind, coin, price, entry, stop, t1, t2, reason, level_name, notes=None):
    """Build a setup dict, or return None and append the reason to notes."""
    tag = "Short" if kind == "short" else "Long"
    def no(msg):
        if notes is not None:
            notes.append(f"{tag} at {level_name} ({fmt(entry)}): {msg}")
        return None
    if t1 is None:
        return no("no weekly target zone beyond the entry")
    risk = (stop - entry) if kind == "short" else (entry - stop)
    reward = (entry - t1) if kind == "short" else (t1 - entry)
    if risk <= 0 or reward <= 0:
        return no("no room to a target")
    rr = reward / risk
    dist = (entry - price) / price  # + means entry is above price
    if abs(dist) > MAX_DIST:
        return no(f"entry {abs(dist) * 100:.0f}% from price (limit {MAX_DIST * 100:.0f}%)")
    if (kind == "short" and price >= stop) or (kind == "long" and price <= stop):
        return no("price is already past the stop")
    if kind == "short":
        status = "at level" if price >= entry * (1 - AT_LEVEL) else "approaching"
    else:
        status = "at level" if price <= entry * (1 + AT_LEVEL) else "approaching"
    risk_pct = risk / entry * 100
    if risk_pct > MAX_STOP_PCT:
        return no(f"stop {risk_pct:.0f}% away is too wide (limit {MAX_STOP_PCT:.0f}%)")
    need = MIN_RR_SHORT if kind == "short" else MIN_RR
    if rr < need:
        return no(f"first target {fmt(t1)} gives only {rr:.1f}R (need {need:g}R)")
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
        "risk_pct": round(risk_pct, 2),
        "size_usd": round(RISK_USD / (risk_pct / 100), 2),
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

    notes = []

    # ---- weekly levels: the 25/50/100 EMAs plus weekly swing highs and lows (last 2 years),
    #      grouped into zones when they sit within one daily ATR of each other
    closed_w = weekly[:-1]
    raw = [(wE[n], f"weekly {n} EMA") for n in EMA_LENGTHS if wE[n]]
    for v, kind in swing_levels(closed_w[-104:], k=2):
        raw.append((v, f"weekly swing {kind} {fmt(v)}"))
    zones = cluster_levels(raw, a)   # [(value, label, [member names])]
    supports = sorted([z for z in zones if z[0] < price * (1 + AT_LEVEL)], key=lambda z: -z[0])
    resists = sorted([z for z in zones if z[0] > price * (1 - AT_LEVEL)], key=lambda z: z[0])
    hi26 = max(x["h"] for x in weekly[-26:])
    lo26 = min(x["l"] for x in weekly[-26:])

    # weekly trend: which direction the weekly chart allows
    w25_slope = slope(w[25], 4) or 0
    long_ok = bool(wE[50]) and price > wE[50] and w25_slope >= -0.01
    short_ok = bool(wE[100] and wE[25] and wE[50]) and price < wE[100] and wE[25] < wE[50]
    if not long_ok:
        notes.append("No longs: price is below the weekly 50 EMA or the weekly 25 EMA is falling")
    if not short_ok:
        notes.append("No shorts: weekly trend is not down (needs price below the weekly 100 EMA and 25 under 50)")

    def targets_above(entry):
        ups = [z[0] for z in zones if z[0] > entry + a]
        if hi26 > entry + a and all(abs(hi26 - u) > a for u in ups):
            ups.append(hi26)
        ups.sort()
        return (ups[0] if ups else None), (ups[1] if len(ups) > 1 else None)

    def targets_below(entry):
        downs = [z[0] for z in zones if z[0] < entry - a]
        if lo26 < entry - a and all(abs(lo26 - u) > a for u in downs):
            downs.append(lo26)
        downs.sort(reverse=True)
        return (downs[0] if downs else None), (downs[1] if len(downs) > 1 else None)

    candidates = []

    # ---- A) weekly reclaim long: last completed week closed back above the weekly 50 or 100 EMA
    if long_ok and len(closed_w) >= 2:
        for n in (100, 50):
            cur, prev = w[n][len(closed_w) - 1], w[n][len(closed_w) - 2]
            if cur and prev and closed_w[-1]["c"] > cur and closed_w[-2]["c"] <= prev and price > wE[n] * (1 - AT_LEVEL):
                entry = wE[n]
                t1, t2 = targets_above(entry)
                candidates.append(make_setup("long", coin, price, entry, entry - 1.5 * a, t1, t2,
                    {"type": "reclaim_long",
                     "why": f"Last week closed back above the weekly {n} EMA. Buy the retest; "
                            f"wrong on a weekly close back below it."}, f"weekly {n} EMA", notes))
                break

    # ---- B) weekly support long: weekly uptrend, buy a pullback into the nearest weekly zone below
    if long_ok:
        if not supports:
            notes.append("Long: no weekly level below price")
        for v, label, _ in supports[:1]:
            t1, t2 = targets_above(v)
            candidates.append(make_setup("long", coin, price, v, v - 1.5 * a, t1, t2,
                {"type": "support_long",
                 "why": f"Weekly uptrend (price above the weekly 50 EMA). Buy a pullback into the {label}; "
                        f"wrong on a weekly close below it."}, label, notes))

    # ---- C) weekly rejection short: weekly downtrend, short a rally into the nearest weekly zone above
    if short_ok:
        picked = None
        for z in resists:
            rising = [m for m in z[2] if "EMA" in m and (slope(w[int(m.split()[1])], 4) or 0) > 0.01]
            if rising and len(rising) == len(z[2]):
                notes.append(f"Short at {z[1]}: the weekly EMA there is rising, not fading it")
                continue
            picked = z
            break
        if picked is None and not resists:
            notes.append("Short: no weekly level above price")
        if picked:
            v, label, _ = picked
            t1, t2 = targets_below(v)
            candidates.append(make_setup("short", coin, price, v, v + 1.5 * a, t1, t2,
                {"type": "rejection_short",
                 "why": f"Weekly downtrend (below the weekly 100 EMA, 25 under 50). Short a rally into the {label}; "
                        f"wrong on a weekly close above it."}, label, notes))

    setups = [x for x in candidates if x]

    # ---- daily timing: did price already test the level and hold (confirmed) or break it (failed)?
    kept = []
    for x in setups:
        L, last3, close = x["entry"], daily[-3:], daily[-1]["c"]
        if x["side"] == "long":
            touched = any(b["l"] <= L * 1.01 for b in last3)
            if close < L - 0.5 * a:
                notes.append(f"Long at {x['level']}: daily close broke below the level, retest failed")
                continue
            if touched and close > L:
                x["status"] = "confirmed"
                x["why"] += " Daily chart: tested the level and closed back above it."
        else:
            touched = any(b["h"] >= L * 0.99 for b in last3)
            if close > L + 0.5 * a:
                notes.append(f"Short at {x['level']}: daily close broke above the level, no rejection")
                continue
            if touched and close < L:
                x["status"] = "confirmed"
                x["why"] += " Daily chart: tagged the level and closed back below it."
        kept.append(x)
    setups = kept

    # ---- confluence: other weekly levels in the same zone, plus daily EMAs within 1 daily ATR
    zone_members = {z[1]: z[2] for z in zones}
    for x in setups:
        members = zone_members.get(x["level"], [x["level"]])
        near = [m for m in members[1:]] if len(members) > 1 else []
        for n in EMA_LENGTHS:
            if dE[n] and abs(dE[n] - x["entry"]) <= a:
                near.append(f"daily {n} EMA")
        x["confluence"] = near
        weekly_hits = sum(1 for nm in near if nm.startswith("weekly"))
        x["rating"] = "A" if weekly_hits >= 1 and len(near) >= 2 else ("B" if near else "C")

    info["notes"] = notes[:6]
    return info, setups


def cluster_levels(raw, width):
    """Group levels closer than `width` into zones. Returns [(zone value, label, member names)].
    The zone value is the mean of its members; the label leads with the strongest member (an EMA if any)."""
    raw = sorted([r for r in raw if r[0]], key=lambda r: r[0])
    zones, cur = [], []
    for v, name in raw:
        if cur and v - cur[-1][0] > width:
            zones.append(cur); cur = []
        cur.append((v, name))
    if cur:
        zones.append(cur)
    out = []
    for z in zones:
        z.sort(key=lambda m: (0 if "EMA" in m[1] else 1, -int(m[1].split()[1]) if "EMA" in m[1] else 0))
        val = sum(m[0] for m in z) / len(z)
        names = [m[1] for m in z]
        label = names[0] if len(names) == 1 else f"{names[0]} zone ({len(names)} levels)"
        out.append((val, label, names))
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


def alert_text(s, price, reason_head):
    side = "🔴" if s["side"] == "short" else "🟢"
    lines = [
        f"{side} <b>{s['coin']}</b> · {LABEL[s['type']]} · {reason_head}",
        f"Price {fmt(price)} · entry {fmt(s['entry'])} ({s['dist_pct']:+.1f}%)",
        f"Stop {fmt(s['stop'])} · T1 {fmt(s['t1'])} ({s['rr']}R)" +
        (f" · T2 {fmt(s['t2'])} ({s['rr2']}R)" if s.get("t2") else ""),
        s["why"],
    ]
    lines.append(f"Stop is {s['risk_pct']:.1f}% away. Size for ${RISK_USD:g} risk: ${s['size_usd']:,.0f} position "
                 f"(any leverage; leverage only changes the margin you put up)")
    if s.get("trending"):
        lines.append("🔥 Trending on CoinGecko searches right now")
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

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = {
        "generated_at": now,
        "source": "Hyperliquid perps, daily candles; weekly built Mon 00:00 UTC",
        "risk_usd": RISK_USD,
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
        if legacy and s["status"] != "approaching":
            alerted[s["id"] + ":" + s["status"]] = now_ts
            continue
        if first_run or s["status"] == "approaching" or s.get("rating") == "C":
            continue
        key = s["id"] + ":" + s["status"]
        if key in alerted:
            continue
        head = "retest confirmed on the daily" if s["status"] == "confirmed" else "price at entry"
        msgs.append(alert_text(s, price_of[s["coin"]], head))
        alerted[key] = now_ts
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
