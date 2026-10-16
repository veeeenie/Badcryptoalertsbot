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


STRONG_MOVE_PCT = 25.0     # a 14-day move this big counts as a strong move (rule 6)
STRONG_STRETCH_PCT = 15.0  # ...as does price this far from the daily 25 EMA
TOP_K = int(os.getenv("TOP_K", "8"))   # how many setups count as "top" (dashboard default, Telegram)
MAX_ALERTS = 3                          # most individual alerts per scan; the rest go in one summary line
SCORE_RATING = {"A": 6, "B": 3, "C": 0}
SCORE_STATUS = {"confirmed": 4, "at level": 2, "approaching": 0}
TRANCHE_IN = (0.3, 0.3, 0.4)   # share of the position at each entry, nearest price first
TRANCHE_OUT = (0.5, 0.5)       # share closed at target 1 and target 2


def make_setup(kind, coin, price, entries, stop, t1, t2, reason, level_name, notes=None):
    """Build a tranche setup dict, or return None and append the reason to notes.
    entries: three prices, nearest to current price first. R is measured from the average entry
    and blends the two targets (half off at each)."""
    tag = "Short" if kind == "short" else "Long"
    entry = sum(p * w for p, w in zip(entries, TRANCHE_IN))
    def no(msg):
        if notes is not None:
            notes.append(f"{tag} at {level_name} ({fmt(entry)}): {msg}")
        return None
    if t1 is None:
        return no("no weekly target zone beyond the entry")
    sign = -1 if kind == "short" else 1
    risk = sign * (entry - stop)
    if risk <= 0 or sign * (t1 - entry) <= 0:
        return no("no room to a target")
    r1 = sign * (t1 - entry) / risk
    r2 = sign * (t2 - entry) / risk if t2 is not None else None
    rr = r1 * TRANCHE_OUT[0] + (r2 if r2 is not None else r1) * TRANCHE_OUT[1]
    dist = (entries[0] - price) / price  # first tranche vs price; + means above price
    if abs(dist) > MAX_DIST:
        return no(f"first entry {abs(dist) * 100:.0f}% from price (limit {MAX_DIST * 100:.0f}%)")
    if sign * (price - stop) <= 0:
        return no("price is already past the stop")
    near_edge = entries[0]
    status = "at level" if (price <= near_edge * (1 + AT_LEVEL) if kind == "long" else price >= near_edge * (1 - AT_LEVEL)) else "approaching"
    risk_pct = risk / entry * 100
    if risk_pct > MAX_STOP_PCT:
        return no(f"stop {fmt(stop)} is {risk_pct:.0f}% from the average entry, too wide (limit {MAX_STOP_PCT:.0f}%)")
    need = MIN_RR_SHORT if kind == "short" else MIN_RR
    if rr < need:
        return no(f"targets {fmt(t1)}" + (f" / {fmt(t2)}" if t2 else "") + f" give {rr:.1f}R blended (need {need:g}R)")
    size = RISK_USD / (risk_pct / 100)
    return {
        "id": f"{coin}:{reason['type']}",
        "coin": coin,
        "type": reason["type"],
        "side": kind,
        "status": status,
        "level": level_name,
        "entry": entry,
        "entries": [{"px": p, "pct": int(w * 100), "usd": round(size * w, 2)} for p, w in zip(entries, TRANCHE_IN)],
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
    zones = cluster_levels(raw, a)   # dicts: mid, lo, hi, label, names
    ath = max(x["h"] for x in weekly)
    atl = min(x["l"] for x in weekly)

    # weekly trend: which direction the weekly chart allows
    w25_slope = slope(w[25], 4) or 0
    long_ok = bool(wE[50]) and price > wE[50] and w25_slope >= -0.01
    short_ok = bool(wE[100] and wE[25] and wE[50]) and price < wE[100] and wE[25] < wE[50]
    if not long_ok:
        notes.append("No longs: price is below the weekly 50 EMA or the weekly 25 EMA is falling")
    if not short_ok:
        notes.append("No shorts: weekly trend is not down (needs price below the weekly 100 EMA and 25 under 50)")

    def build(kind, z, typ, why):
        """Plan a tranche trade at weekly zone z. Stop sits beyond the zone and beyond any weekly
        level just past it (supply/demand that would otherwise sit right behind the stop)."""
        lo, hi = min(z["lo"], z["mid"] - 0.25 * a), max(z["hi"], z["mid"] + 0.25 * a)
        if kind == "long":
            entries = [hi, z["mid"], lo]                      # scale in from the top of the zone down
            stop = lo - 0.5 * a
            for o in sorted([o for o in zones if o["hi"] < lo], key=lambda o: -o["hi"]):
                if o["hi"] >= stop - a:                       # another level right under the stop: go below it
                    stop = o["lo"] - 0.5 * a
                else:
                    break
            tgts = [o["mid"] for o in sorted(zones, key=lambda o: o["mid"]) if o["lo"] > hi + a]
            if not tgts or tgts[-1] < ath * 0.98:
                tgts.append(ath)
        else:
            entries = [lo, z["mid"], hi]                      # scale in from the bottom of the zone up
            stop = hi + 0.5 * a
            for o in sorted([o for o in zones if o["lo"] > hi], key=lambda o: o["lo"]):
                if o["lo"] <= stop + a:
                    stop = o["hi"] + 0.5 * a
                else:
                    break
            tgts = [o["mid"] for o in sorted(zones, key=lambda o: -o["mid"]) if o["hi"] < lo - a]
            if not tgts:
                tgts.append(atl)
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

    # ---- daily timing: did price trade into the zone and hold (confirmed) or break through it (failed)?
    kept = []
    for x in setups:
        lo, hi = x["zone"]
        last3, close = daily[-3:], daily[-1]["c"]
        if x["side"] == "long":
            if close < lo - 0.5 * a:
                notes.append(f"Long at {x['level']}: daily close broke below the zone, retest failed")
                continue
            if any(b["l"] <= hi for b in last3) and close > hi:
                x["status"] = "confirmed"
                x["why"] += " Daily chart: traded into the zone and closed back above it."
        else:
            if close > hi + 0.5 * a:
                notes.append(f"Short at {x['level']}: daily close broke above the zone, no rejection")
                continue
            if any(b["h"] >= lo for b in last3) and close < lo:
                x["status"] = "confirmed"
                x["why"] += " Daily chart: traded into the zone and closed back below it."
        kept.append(x)
    setups = kept

    # ---- rule 6: against a strong move, only take the trade after a confirmed daily rejection
    chg14 = (price / dc[-15] - 1) * 100 if len(dc) > 15 else 0
    stretch = info["stretch_d25"] or 0
    info["chg14"] = round(chg14, 1)
    for x in setups:
        strong_up = chg14 >= STRONG_MOVE_PCT or stretch >= STRONG_STRETCH_PCT
        strong_down = chg14 <= -STRONG_MOVE_PCT or stretch <= -STRONG_STRETCH_PCT
        if (x["side"] == "short" and strong_up) or (x["side"] == "long" and strong_down):
            x["needs_confirmation"] = True
            x["why"] += (f" Strong move ({chg14:+.0f}% in 14 days, {stretch:+.0f}% from the daily 25 EMA): "
                         f"wait for a confirmed daily rejection before entering.")

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
    ins = " / ".join(f"{fmt(e['px'])} ({e['pct']}%)" for e in s["entries"])
    outs = " / ".join(f"{fmt(x['px'])} ({x['pct']}%, {x['r']}R)" for x in s["exits"])
    lines = [
        f"{side} <b>{s['coin']}</b> · {LABEL[s['type']]} · {reason_head}",
        f"Price {fmt(price)} · zone {fmt(s['zone'][0])}–{fmt(s['zone'][1])}",
        f"Entries: {ins}",
        f"Stop {fmt(s['stop'])} ({s['risk_pct']:.1f}% from avg entry {fmt(s['entry'])})",
        f"Exits: {outs} · blended {s['rr']}R",
        f"Size for ${RISK_USD:g} risk: ${s['size_usd']:,.0f} total position",
        s["why"],
        "Rules: resting limits at the zone only, never chase. Place the stop and both take-profits with the entry.",
    ]
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

    # ---- rank: only the best TOP_K setups are "top" (shown first, and the only ones that alert)
    for s in all_setups:
        s["score"] = round(SCORE_RATING.get(s.get("rating"), 0) + SCORE_STATUS.get(s["status"], 0)
                           + min(s["rr"], 6) * 0.5 - abs(s["dist_pct"]) * 0.1
                           - (2 if s.get("needs_confirmation") and s["status"] != "confirmed" else 0)
                           - (2 if s.get("unlock") and s["side"] == "long" else 0), 2)
    all_setups.sort(key=lambda s: -s["score"])
    for i, s in enumerate(all_setups):
        s["top"] = i < TOP_K and s.get("rating") in ("A", "B")

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
    live_ids = {x["id"] for x in all_setups}
    # forget alerts only once a setup has left the scan, so each setup alerts at most once per stage
    alerted = {k: v for k, v in alerted.items() if k.rsplit(":", 1)[0] in live_ids or now_ts - v < 86400}
    v2 = state.get("version") == 2
    due = []
    for s in all_setups:
        new_seen[s["id"]] = s["status"]
        if s["status"] == "approaching" or not s.get("top"):
            continue
        if s.get("needs_confirmation") and s["status"] != "confirmed":
            continue                          # rule 5: no alert until the rejection is confirmed
        key = s["id"] + ":" + s["status"]
        if key in alerted:
            continue
        alerted[key] = now_ts
        if first_run or not v2:
            continue                          # first run of this version: mark as sent, stay quiet
        due.append(s)
    for s in due[:MAX_ALERTS]:
        head = "retest confirmed on the daily" if s["status"] == "confirmed" else "price at entry"
        msgs.append(alert_text(s, price_of[s["coin"]], head))
    if len(due) > MAX_ALERTS:
        msgs.append(f"+{len(due) - MAX_ALERTS} more top setups reached entry: " +
                    ", ".join(x["coin"] for x in due[MAX_ALERTS:]) + ". Details on the dashboard.")
    if first_run and all_setups:
        top = [x for x in all_setups if x.get("top")]
        msgs.append("<b>Setup scanner is live.</b> Top setups:\n\n" +
                    "\n".join(f"• {x['coin']} {LABEL[x['type']]} entry {fmt(x['entry'])} "
                               f"({x['dist_pct']:+.1f}%), {x['rr']}R, {x['status']}" for x in top))
    for m in msgs:
        telegram(m)
        time.sleep(1)
    with open(STATE_PATH, "w") as f:
        json.dump({"version": 2, "seen": new_seen, "alerted": alerted, "updated": now}, f)

    print(f"setups: {len(all_setups)}, alerts: {len(msgs)}, errors: {len(errors)}, "
          f"time: {out['runtime_s']}s")
    if errors:
        print("\n".join(errors[:20]), file=sys.stderr)
    if not all_info:
        sys.exit("No coins analysed — check the Hyperliquid API response.")


if __name__ == "__main__":
    main()
