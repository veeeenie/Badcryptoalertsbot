"""
Chart screener (trader, 5 Nov 2026): every active Hyperliquid perp, sorted into bullish, bearish and mixed
on the daily, weekly and monthly charts, with plain facts for and against, nearby levels, and market cap.
The trader picks the trade; the dashboard checks entries, stops and targets.

Trend on each timeframe uses the trader's EMAs (25 / 50 / 100, on closed candles):
  bullish  = close above the 25 EMA and the 25 above the 50 (and the 50 above the 100 when there are 100 candles)
  bearish  = the mirror
  mixed    = anything else; "n/a" when there are fewer than 25 candles (young coins on the monthly)
The daily 200 EMA is a filter (trader, 5 Nov 2026): Bullish also needs price above it, Bearish below it
(coins with under 200 daily candles skip this check).
Lists: Bullish = weekly bullish and monthly bullish or n/a; Bearish = the mirror. Within a list, coins
bullish on all three come first ("all three"), then those whose daily is pulling back ("daily pullback").

Writes docs/data/screener.json. Run hourly by the scan workflow.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan import hl, daily_candles, weekly_from_daily, DATA_DIR  # noqa: E402
from risk import risk_usd, RISK_PCT  # noqa: E402

OUT = os.path.join(DATA_DIR, "screener.json")
FUND_N = 15      # coins per side that get fundamentals researched (trader, 5 Nov 2026)


def ema(xs, n):
    if len(xs) < n:
        return None
    a = 2 / (n + 1); e = xs[0]
    for x in xs[1:]:
        e = a * x + (1 - a) * e
    return e


def monthly(daily):
    out, cur = [], None
    for c in daily:
        dt = datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc)
        key = dt.year * 12 + dt.month
        if cur is None or cur["k"] != key:
            cur = {"k": key, "t": c["t"], "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"]}
            out.append(cur)
        else:
            cur["h"] = max(cur["h"], c["h"]); cur["l"] = min(cur["l"], c["l"]); cur["c"] = c["c"]
    return out


def trend(closed):
    """('bull'|'bear'|'mixed'|'n/a', {ema25, ema50, ema100})"""
    cs = [x["c"] for x in closed]
    e25, e50, e100 = ema(cs, 25), ema(cs, 50), ema(cs, 100)
    info = {"ema25": e25, "ema50": e50, "ema100": e100}
    if e25 is None:
        return "n/a", info
    p = cs[-1]
    if e50 is None:
        # young: price vs the 25 and the 25's slope over 4 candles
        prev = ema(cs[:-4], 25) if len(cs) >= 29 else None
        if prev is None:
            return "n/a", info
        return ("bull" if p > e25 and e25 > prev else "bear" if p < e25 and e25 < prev else "mixed"), info
    up = p > e25 > e50 and (e100 is None or e50 > e100)
    dn = p < e25 < e50 and (e100 is None or e50 < e100)
    return ("bull" if up else "bear" if dn else "mixed"), info


def atr(bars, n=14):
    tr = [max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"])) for a, b in zip(bars[:-1], bars[1:])][-n:]
    return sum(tr) / len(tr) if tr else None


def swing_levels(weeks, price, A, k=2):
    """Weekly swing highs/lows (k weeks each side) plus the all-time high/low, merged within a quarter ATR.
    Returns (supports below price, resistances above), nearest first, each {price, touches}."""
    pts = []
    for i in range(k, len(weeks) - k):
        w = weeks[i - k:i + k + 1]
        if weeks[i]["h"] >= max(x["h"] for x in w):
            pts.append(weeks[i]["h"])
        if weeks[i]["l"] <= min(x["l"] for x in w):
            pts.append(weeks[i]["l"])
    if weeks:
        pts += [max(x["h"] for x in weeks), min(x["l"] for x in weeks)]
    pts.sort()
    tol = 0.25 * A if A else 0
    groups = []
    for p in pts:
        if groups and p - groups[-1][-1] <= tol:
            groups[-1].append(p)
        else:
            groups.append([p])
    lv = [{"price": sum(g) / len(g), "touches": len(g)} for g in groups]
    sup = sorted([x for x in lv if x["price"] < price], key=lambda x: -x["price"])[:4]
    res = sorted([x for x in lv if x["price"] > price], key=lambda x: x["price"])[:4]
    return sup, res


def coingecko():
    """{SYMBOL: (market cap, rank, name)} for the top 500 by market cap; {} if unreachable."""
    out = {}
    for page in (1, 2):
        try:
            r = __import__("requests").get("https://api.coingecko.com/api/v3/coins/markets",
                                           params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": page},
                                           timeout=30, headers={"accept": "application/json"})
            r.raise_for_status()
            for c in r.json():
                s = c["symbol"].upper()
                if s not in out and c.get("market_cap"):
                    out[s] = (c["market_cap"], c.get("market_cap_rank"), c.get("name"))
        except Exception as e:
            print("coingecko failed", e, file=sys.stderr)
        time.sleep(2)
    return out


def pct(a, b):
    return (a / b - 1) * 100


def facts(coin, price, tf, w, A, sup, res, fund_apr, chg13, e200=None):
    """Plain facts, as two lists: points a long would like, points a short would like."""
    bull, bear = [], []
    if e200:
        d = pct(price, e200)
        (bull if d > 0 else bear).append(f"Price {'above' if d > 0 else 'below'} the daily 200 EMA ({fmt(e200)}, {d:+.1f}%)")
    names = {"d": "Daily", "w": "Weekly", "m": "Monthly"}
    for k in ("m", "w", "d"):
        t = tf[k]["trend"]
        if t == "bull":
            bull.append(f"{names[k]} trend up: price above the 25 EMA, EMAs stacked up")
        elif t == "bear":
            bear.append(f"{names[k]} trend down: price below the 25 EMA, EMAs stacked down")
        elif t == "mixed":
            e25 = tf[k]["ema25"]
            if e25:
                (bull if price > e25 else bear).append(f"{names[k]} mixed; price {'above' if price > e25 else 'below'} its 25 EMA ({fmt(e25)})")
    if sup:
        s = sup[0]; d = pct(price, s["price"])
        (bull if d < 8 else bear).append(f"Nearest weekly support {fmt(s['price'])}, {d:.1f}% below" + (f" ({s['touches']} touches)" if s["touches"] > 1 else "")
                                         + (": close by" if d < 8 else ": far, room to fall"))
    else:
        bear.append("No weekly support below: at or near the all-time low")
    if res:
        r = res[0]; d = pct(r["price"], price)
        (bear if d < 8 else bull).append(f"Nearest weekly resistance {fmt(r['price'])}, {d:.1f}% above" + (f" ({r['touches']} touches)" if r["touches"] > 1 else "")
                                         + (": close by" if d < 8 else ": room to rise"))
    else:
        bull.append("No weekly resistance above: at the all-time high")
    last5 = w[-5:]
    ups = sum(1 for x in last5 if x["c"] > x["o"])
    if ups >= 4:
        bull.append(f"{ups} of the last 5 weeks closed green")
    elif ups <= 1:
        bear.append(f"{5 - ups} of the last 5 weeks closed red")
    if chg13 is not None:
        (bull if chg13 > 0 else bear).append(f"{chg13:+.0f}% over 13 weeks")
    if fund_apr is not None:
        if fund_apr > 30:
            bear.append(f"Funding {fund_apr:+.0f}%/yr: longs crowded and paying")
        elif fund_apr < -10:
            bull.append(f"Funding {fund_apr:+.0f}%/yr: shorts crowded and paying")
    return bull, bear


def fmt(x):
    return f"{x:,.0f}" if x >= 1000 else f"{x:.4g}"


def main():
    meta, ctxs = hl({"type": "metaAndAssetCtxs"})
    cg = coingecko()
    old = {}
    try:
        with open(OUT) as f:
            old = {c["coin"]: c for c in json.load(f).get("coins", [])}
    except (OSError, ValueError):
        pass
    now = int(time.time() * 1000)
    rows = []
    for a, c in zip(meta["universe"], ctxs):
        if a.get("isDelisted"):
            continue
        coin = a["name"]
        try:
            price = float(c.get("markPx") or 0)
            d = daily_candles(coin)
        except Exception as e:
            print(coin, "candles failed", e, file=sys.stderr); continue
        if price <= 0 or len(d) < 30:
            continue
        dc = d[:-1]                                        # closed days
        wk = [x for x in weekly_from_daily(d) if x["t"] + 7 * 86_400_000 <= now]
        mo = monthly(d)[:-1]                               # closed months
        tf = {}
        for k, bars in (("d", dc), ("w", wk), ("m", mo)):
            t, info = trend(bars) if bars else ("n/a", {})
            tf[k] = {"trend": t, **{kk: (round(v, 8) if v else None) for kk, v in info.items()}}
        A = atr(wk) if len(wk) > 2 else None
        sup, res = swing_levels(wk, price, A)
        fund_apr = float(c.get("funding") or 0) * 24 * 365 * 100
        chg13 = pct(price, wk[-13]["c"]) if len(wk) >= 13 else None
        e200 = ema([x["c"] for x in dc], 200)
        tf["d"]["ema200"] = round(e200, 8) if e200 else None
        bull, bear = facts(coin, price, tf, wk, A, sup, res, fund_apr, chg13, e200)
        wt, mt, dt = tf["w"]["trend"], tf["m"]["trend"], tf["d"]["trend"]
        above200 = None if e200 is None else price > e200
        if wt == "bull" and mt in ("bull", "n/a") and above200 is not False:
            label, group = "bullish", ("all three" if dt == "bull" else "daily pullback")
        elif wt == "bear" and mt in ("bear", "n/a") and above200 is not True:
            label, group = "bearish", ("all three" if dt == "bear" else "daily bounce")
        else:
            label = "mixed"
            group = ("weekly up, below the daily 200" if wt == "bull" and mt in ("bull", "n/a") else
                     "weekly down, above the daily 200" if wt == "bear" and mt in ("bear", "n/a") else "")
        score = sum({"bull": 1, "bear": -1}.get(tf[k]["trend"], 0) * wgt for k, wgt in (("d", 1), ("w", 2), ("m", 2)))
        sym = coin[1:] if coin.startswith("k") and coin[1:].isupper() else coin
        mc = cg.get(sym)
        prev = old.get(coin, {})
        rows.append({
            "coin": coin, "price": price, "chg24h": round(pct(price, float(c.get("prevDayPx") or price)), 2),
            "vol24h": round(float(c.get("dayNtlVlm") or 0)), "oi_usd": round(float(c.get("openInterest") or 0) * price),
            "funding_apr": round(fund_apr, 1), "max_lev": a.get("maxLeverage"),
            "mcap": mc[0] if mc else prev.get("mcap"),
            "mcap_rank": mc[1] if mc else prev.get("mcap_rank"), "name": mc[2] if mc else prev.get("name"),
            "label": label, "group": group, "score": score, "tf": tf,
            "atr_w": A, "chg13w": round(chg13, 1) if chg13 is not None else None,
            "weeks": len(wk), "support": sup, "resistance": res, "for_long": bull, "for_short": bear,
        })
        time.sleep(0.2)

    def strength(r):   # within a list: all three first, then by 13-week move in the list's direction
        s = r["chg13w"] or 0
        return (r["group"] != "all three", -s if r["label"] == "bullish" else s)
    bulls = sorted([r for r in rows if r["label"] == "bullish"], key=strength)
    bears = sorted([r for r in rows if r["label"] == "bearish"], key=strength)
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "risk_usd": risk_usd(), "risk_pct": RISK_PCT, "count": len(rows),
           "fund_targets": {"bullish": [r["coin"] for r in bulls[:FUND_N]], "bearish": [r["coin"] for r in bears[:FUND_N]]},
           "coins": bulls + bears + sorted([r for r in rows if r["label"] == "mixed"], key=lambda r: -abs(r["score"]))}
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"screener: {len(rows)} coins, {len(bulls)} bullish, {len(bears)} bearish")


if __name__ == "__main__":
    main()
