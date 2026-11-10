"""
Market strip (trader, 10 Nov 2026): BTC, TOTAL (all crypto) and ALTS (all crypto minus BTC minus
stablecoins), each labelled bullish / bearish / mixed with the same rules as the coin screener
(25 / 50 / 100 EMAs on daily, weekly and monthly closes; the daily 200 EMA as a filter).

Data:
  BTC   - Hyperliquid daily candles.
  TOTAL - CoinMarketCap's chart data (the feed its website uses; not an official API): daily total
          market cap, alt market cap (total minus BTC) and BTC dominance.
  ALTS  - CoinMarketCap alt market cap minus total stablecoin supply from DefiLlama.
TOTAL and ALTS have one value a day, so their "candles" are daily closes (levels come from closes).
If a feed fails, its row is marked unavailable instead of showing stale numbers.

Sends a Telegram message when a row's label changes. Writes docs/data/market.json; run hourly.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan import daily_candles, weekly_from_daily, DATA_DIR  # noqa: E402
from screener import trend, monthly, atr, swing_levels, ema, facts, pct, fmt  # noqa: E402

OUT = os.path.join(DATA_DIR, "market.json")
DAY = 86_400_000
UA = {"User-Agent": "Mozilla/5.0 (Setup Desk dashboard)", "accept": "application/json"}


def cmc_history(start_year=2018):
    """{day_ms: (total, altcoin = total - BTC, btc_dominance)} from CoinMarketCap's chart data."""
    out = {}
    t = int(datetime(start_year, 1, 1, tzinfo=timezone.utc).timestamp())
    end = int(time.time())
    while t < end:
        t2 = min(end, t + 700 * 86400)
        r = requests.get("https://api.coinmarketcap.com/data-api/v3/global-metrics/quotes/historical",
                         params={"format": "chart", "interval": "1d", "timeStart": t, "timeEnd": t2}, headers=UA, timeout=40)
        r.raise_for_status()
        for q in r.json()["data"]["quotes"]:
            ts = int(datetime.fromisoformat(q["timestamp"].replace("Z", "+00:00")).timestamp() * 1000)
            u = q["quote"][0]
            out[ts - ts % DAY] = (float(u["totalMarketCap"]), float(u["altcoinMarketCap"]), float(q["btcDominance"]))
        t = t2
        time.sleep(1.5)
    return out


def stables_history():
    """{day_ms: total stablecoin supply in USD} from DefiLlama."""
    r = requests.get("https://stablecoins.llama.fi/stablecoincharts/all", headers=UA, timeout=40)
    r.raise_for_status()
    out = {}
    for x in r.json():
        ts = int(x["date"]) * 1000
        v = (x.get("totalCirculatingUSD") or {}).get("peggedUSD")
        if v:
            out[ts - ts % DAY] = float(v)
    return out


def as_candles(series):
    """[(day_ms, value)] -> candle dicts with o = h = l = c = value."""
    return [{"t": t, "o": v, "h": v, "l": v, "c": v} for t, v in series]


def row(rid, name, daily, now_value, unit):
    """One market row from closed daily candles plus the live value."""
    now = int(time.time() * 1000)
    dc = [x for x in daily if x["t"] + DAY <= now] or daily
    wk = [x for x in weekly_from_daily(dc) if x["t"] + 7 * DAY <= now]
    mo = monthly(dc)[:-1]
    tf = {}
    for k, bars in (("d", dc), ("w", wk), ("m", mo)):
        t, info = trend(bars) if bars else ("n/a", {})
        tf[k] = {"trend": t, **{kk: (round(v, 8) if v else None) for kk, v in info.items()}}
    e200 = ema([x["c"] for x in dc], 200)
    tf["d"]["ema200"] = e200
    price = now_value
    A = atr(wk) if len(wk) > 2 else None
    sup, res = swing_levels(wk, price, A)
    chg7 = pct(price, dc[-8]["c"]) if len(dc) > 8 else None
    chg13 = pct(price, wk[-13]["c"]) if len(wk) >= 13 else None
    bull, bear = facts(rid, price, tf, wk, A, sup, res, None, chg13, e200)
    wt, mt = tf["w"]["trend"], tf["m"]["trend"]
    above = None if e200 is None else price > e200
    if wt == "bull" and mt in ("bull", "n/a") and above is not False:
        label = "bullish"
    elif wt == "bear" and mt in ("bear", "n/a") and above is not True:
        label = "bearish"
    else:
        label = "mixed"
    # lean: the weekly trend and the daily 200 agree even if the monthly hasn't turned
    lean = "bearish" if wt == "bear" and above is False else "bullish" if wt == "bull" and above is True else None
    # unit: "price" (USD) or "mcap" ($ billions)
    return {"id": rid, "name": name, "unit": unit, "value": price, "label": label, "lean": lean if label == "mixed" else label, "tf": tf,
            "chg7d": round(chg7, 1) if chg7 is not None else None, "chg13w": round(chg13, 1) if chg13 is not None else None,
            "support": sup, "resistance": res, "for_long": bull, "for_short": bear, "atr_w": A}


def verdict(rows):
    lab = {r["id"]: r["label"] for r in rows if r.get("label")}
    lean = {r["id"]: r.get("lean") for r in rows if r.get("label")}
    if all(v == "mixed" for v in lab.values()) and lean.get("BTC") == lean.get("ALTS") == "bearish":
        return "Leaning risk-off: BTC and alts are down on the weekly and below their daily 200; the monthly hasn't turned yet. Shorts favoured."
    if all(v == "mixed" for v in lab.values()) and lean.get("BTC") == lean.get("ALTS") == "bullish":
        return "Leaning risk-on: BTC and alts are up on the weekly and above their daily 200; the monthly hasn't turned yet. Longs favoured."
    b, a = lab.get("BTC"), lab.get("ALTS")
    if b == "bearish" and a == "bearish":
        return "Risk-off: shorts favoured, longs need a strong reason."
    if b == "bullish" and a == "bullish":
        return "Risk-on: longs favoured, shorts need a strong reason."
    if b == "bullish" and a == "bearish":
        return "BTC strong, alts weak: money is staying in BTC. Alt longs are against the alt market."
    if b == "bearish" and a == "bullish":
        return "Alts holding while BTC is weak: unusual, watch BTC."
    if a == "bearish":
        return "Mixed: BTC undecided, alts weak. Alt longs are against the alt market."
    if a == "bullish":
        return "Mixed: BTC undecided, alts strong."
    return "Mixed: no clear direction. Smaller size, wait for the market to pick a side."


def telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("telegram not set:", text); return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=20)
    except Exception as e:
        print("telegram error", e, file=sys.stderr)


def main():
    try:
        with open(OUT) as f:
            prev = {r["id"]: r for r in json.load(f).get("rows", []) if r.get("label")}
    except (OSError, ValueError):
        prev = {}
    rows = []
    # BTC from Hyperliquid
    try:
        d = daily_candles("BTC")
        rows.append(row("BTC", "Bitcoin", d, d[-1]["c"], "price"))
    except Exception as e:
        print("BTC failed", e, file=sys.stderr); rows.append({"id": "BTC", "name": "Bitcoin", "error": str(e)})
    # TOTAL and ALTS from CoinMarketCap (+ DefiLlama stablecoins)
    dom = None
    try:
        h = cmc_history()
        days = sorted(h)
        total = [(t, h[t][0] / 1e9) for t in days]            # in $ billions
        rows.append(row("TOTAL", "All crypto", as_candles(total), total[-1][1], "mcap"))
        st = stables_history()
        alts, last_s = [], None
        for t in days:
            last_s = st.get(t, last_s)
            if last_s:
                alts.append((t, (h[t][1] - last_s) / 1e9))
        rows.append(row("ALTS", "Alts (minus BTC and stablecoins)", as_candles(alts), alts[-1][1], "mcap"))
        d_now, d_30 = h[days[-1]][2], h[days[-31]][2] if len(days) > 31 else None
        dom = {"now": round(d_now, 2), "d30": round(d_30, 2) if d_30 else None,
               "stables_usd": last_s, "as_of": datetime.fromtimestamp(days[-1] / 1000, tz=timezone.utc).date().isoformat()}
    except Exception as e:
        print("market caps failed", e, file=sys.stderr)
        for rid, name in (("TOTAL", "All crypto"), ("ALTS", "Alts (minus BTC and stablecoins)")):
            if not any(r["id"] == rid for r in rows):
                rows.append({"id": rid, "name": name, "error": "data unavailable"})
    # label changes -> Telegram
    changes = []
    for r in rows:
        p = prev.get(r["id"])
        if r.get("label") and p and (p.get("label"), p.get("lean")) != (r["label"], r.get("lean")):
            was = p["label"] + (f" (leaning {p['lean']})" if p.get("lean") and p["label"] == "mixed" else "")
            now = r["label"].upper() + (f" (leaning {r['lean']})" if r.get("lean") and r["label"] == "mixed" else "")
            changes.append(f"{r['id']}: {was} → {now}")
    v = verdict(rows)
    if changes:
        telegram("📊 Market change\n" + "\n".join(changes) + f"\n\n{v}")
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": rows, "verdict": v, "btc_dominance": dom}
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("market:", [(r["id"], r.get("label") or r.get("error")) for r in rows], "|", v)


if __name__ == "__main__":
    main()
