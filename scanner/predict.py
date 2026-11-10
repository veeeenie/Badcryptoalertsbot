"""
Prediction-market scanner (trader, 10 Nov 2026): read-only look at Hyperliquid's HIP-4 outcome markets.

For every live market: best YES bid and ask, spread, size at the best prices, time left.
For price markets ("will BTC / NVDA / GOLD be above X at time T") it adds a model price: the chance
the underlying ends above the threshold, from its current Hyperliquid price, the hours left and its
volatility over the last 7 days of hourly candles (a lognormal, no drift; rough, ignores fat tails,
weekends and the averaging window some markets settle on).

Only markets listed on trade.xyz's Events page alert (venue "txyz" plus Hyperliquid's own recurring daily
markets); other apps' markets (Skew, Fox, Out) are kept in the file but hidden on the site.
Telegram alert when a price market with 1.5 to 72 hours left can be bought at least MARGIN (10
points) below the model price on either side (YES at the ask, or NO at 1 - the YES bid), with at
least $10 available at that price (Hyperliquid's minimum order). Once per market and side.

YES and NO share one order book: buying NO at q is the same as selling YES at 1 - q.
Writes docs/data/predict.json. Run hourly. Places no orders.
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data", "predict.json")
HL = "https://api.hyperliquid.xyz/info"
MARGIN = 0.10            # model vs price, in probability points, for an alert
MIN_USD = 10.0           # Hyperliquid's minimum order
ALERT_HOURS = (1.5, 72)
IST = timezone(timedelta(hours=5, minutes=30))


def hl(body):
    for i in range(3):
        try:
            r = requests.post(HL, json=body, timeout=30)
            if r.status_code == 429:
                time.sleep(2 + 3 * i); continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == 2:
                raise
            time.sleep(2)


def kv(desc):
    out = {}
    for part in (desc or "").split("|"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k] = v
    return out


def when(s):
    """'20261110-2100' -> aware UTC datetime."""
    return datetime.strptime(s, "%Y%m%d-%H%M").replace(tzinfo=timezone.utc)


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def sigma_hourly(coin, now_ms):
    """Std dev of hourly log returns over the last 7 days, or None."""
    try:
        cs = hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h", "startTime": now_ms - 7 * 86_400_000, "endTime": now_ms}})
    except Exception:
        return None
    px = [float(c["c"]) for c in cs or [] if float(c["c"]) > 0]
    if len(px) < 24:
        return None
    r = [math.log(px[i] / px[i - 1]) for i in range(1, len(px))]
    mu = sum(r) / len(r)
    return math.sqrt(sum((x - mu) ** 2 for x in r) / (len(r) - 1)) or None


def fmt(x):
    return f"{x:,.0f}" if x >= 1000 else f"{x:,.2f}" if x >= 10 else f"{x:.4g}"


def title_of(o, f, qdesc):
    name = o.get("name", "")
    if name == "template:binaryPrice":
        u = f.get("perp", "?").replace("xyz:", "")
        return f"{u} above {fmt(float(f['threshold']))}"
    if name == "template:priceTouch":
        u = f.get("perp", "?").replace("xyz:", "")
        th = f.get("threshold")
        return f"{u} touches {fmt(float(th))}" if th else f"{u} price touch"
    if "participantA" in f:
        return f"{f.get('shortNameA') or f['participantA']} beat {f.get('shortNameB') or f.get('participantB', '?')}" + (f" ({f['competition']})" if f.get("competition") else "")
    if "participant" not in f and len(f) > 1:          # other templates: show the description's fields
        return " · ".join(f"{k} {v}" for k, v in f.items())[:90]
    q = kv(qdesc) if qdesc else {}
    p = f.get("participant") or ("Other" if o.get("description") == "other" else o.get("description", "")[:60])
    ctx = q.get("competition") or ""
    if q.get("participantA"):
        ctx = f"{q.get('participantA')} v {q.get('participantB')}" + (f", {ctx}" if ctx else "")
    return f"{p}" + (f" ({ctx})" if ctx else "")


def telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("TELEGRAM not set:\n" + text)
        return True
    try:
        return requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                             json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=20).ok
    except Exception as e:
        print("telegram error", e, file=sys.stderr)
        return False


def main():
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    try:
        with open(OUT) as fh:
            prev = json.load(fh)
    except (OSError, ValueError):
        prev = {}
    meta = hl({"type": "outcomeMeta"})
    mids = hl({"type": "allMids"})
    try:
        mids.update(hl({"type": "allMids", "dex": "xyz"}))
    except Exception:
        pass
    qdesc = {}
    for q in meta.get("questions", []):
        for oid in (q.get("namedOutcomes") or []) + ([q["fallbackOutcome"]] if q.get("fallbackOutcome") is not None else []):
            qdesc[oid] = q.get("description", "")

    sig = {}
    rows = []
    for o in meta.get("outcomes", []):
        oid = o["outcome"]
        yes = f"#{oid * 10}"
        if yes not in mids:
            continue            # settled or not trading
        f = kv(o.get("description"))
        if f.get("class") == "priceBinary":        # Hyperliquid's own recurring daily markets (listed on trade.xyz)
            f = {"perp": f.get("underlying"), "threshold": f.get("targetPrice"), "time": f.get("expiry")}
            o = dict(o, name="template:binaryPrice")
        kind = "price" if o.get("name") == "template:binaryPrice" else "touch" if o.get("name") == "template:priceTouch" else "event"
        exp = None
        for k in ("time", "scheduledStart", "resolutionDeadline"):
            if f.get(k):
                try:
                    exp = when(f[k]); break
                except ValueError:
                    pass
        hours = (exp - now).total_seconds() / 3600 if exp else None
        if hours is not None and hours <= 0 and kind != "event":
            continue
        try:
            book = hl({"type": "l2Book", "coin": yes})["levels"]
        except Exception:
            continue
        time.sleep(0.05)
        bids, asks = book[0], book[1]
        bid = float(bids[0]["px"]) if bids else None
        ask = float(asks[0]["px"]) if asks else None
        bid_usd = sum(float(l["sz"]) * (1 - float(l["px"])) for l in bids if bid - float(l["px"]) <= 0.01) if bids else 0   # buying NO
        ask_usd = sum(float(l["sz"]) * float(l["px"]) for l in asks if float(l["px"]) - ask <= 0.01) if asks else 0
        venue = o.get("venue")
        r = {"id": oid, "kind": kind, "title": title_of(o, f, qdesc.get(oid)), "venue": venue,
             "on_txyz": venue in (None, "txyz"),      # what the trade.xyz Events page shows (its own + Hyperliquid's recurring)
             "expiry": exp.isoformat() if exp else None, "hours_left": round(hours, 2) if hours is not None else None,
             "bid": bid, "ask": ask, "spread": round(ask - bid, 4) if bid is not None and ask is not None else None,
             "bid_usd": round(bid_usd, 2), "ask_usd": round(ask_usd, 2)}
        if kind == "price" and hours:
            u = f.get("perp")
            spot = float(mids[u]) if u in mids else None
            if u not in sig:
                sig[u] = sigma_hourly(u, now_ms)
            s = sig[u]
            th = float(f["threshold"])
            if spot and s:
                fair = phi(math.log(spot / th) / (s * math.sqrt(max(hours, 1 / 60))))
                r.update({"underlying": u.replace("xyz:", ""), "spot": spot, "threshold": th, "sigma_h": round(s, 5), "fair": round(fair, 4),
                          "edge_yes": round(fair - ask, 4) if ask is not None else None,          # buy YES at the ask
                          "edge_no": round(bid - fair, 4) if bid is not None else None})          # buy NO at 1 - bid
        rows.append(r)

    # alerts: price markets with a real edge, enough size, and time to act
    sent = {k: v for k, v in (prev.get("alerted") or {}).items() if now_ms - v < 4 * 86_400_000}
    msgs = []
    for r in rows:
        if r.get("fair") is None or not r["on_txyz"] or not (ALERT_HOURS[0] <= r["hours_left"] <= ALERT_HOURS[1]):
            continue
        for side, edge, usd, px in (("YES", r.get("edge_yes"), r["ask_usd"], r["ask"]), ("NO", r.get("edge_no"), r["bid_usd"], None if r["bid"] is None else 1 - r["bid"])):
            key = f"{r['id']}:{side}"
            if edge is None or edge < MARGIN or usd < MIN_USD or key in sent:
                continue
            model = r["fair"] if side == "YES" else 1 - r["fair"]
            exp = datetime.fromisoformat(r["expiry"]).astimezone(IST).strftime("%d %b %H:%M IST")
            msgs.append(f"🎲 {r['title']} at {exp} (in {r['hours_left']:.1f}h)\n"
                        f"Buy {side} at {px:.2f} vs model {model:.2f} (+{edge * 100:.0f} pts) · ${usd:,.0f} available\n"
                        f"{r['underlying']} now {fmt(r['spot'])} · model uses last 7 days' volatility, rough")
            sent[key] = now_ms
    if msgs and not telegram("Prediction markets: priced off the model\n\n" + "\n\n".join(msgs[:8])):
        sent = prev.get("alerted") or {}

    out = {"generated_at": now.isoformat(timespec="seconds"), "margin": MARGIN, "min_usd": MIN_USD,
           "alert_hours": list(ALERT_HOURS), "markets": rows, "alerted": sent}
    with open(OUT, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"predict: {len(rows)} markets, {sum(1 for r in rows if r.get('fair') is not None)} modelled, {len(msgs)} alerts")


if __name__ == "__main__":
    main()
