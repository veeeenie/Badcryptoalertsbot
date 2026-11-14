"""
Event bets (trader, 14 Nov 2026): the trader's own HIP-4 outcome trades on trade.xyz Events, read-only.

Shows open positions (size, entry, value now, P&L), open orders (what has to happen for each to fill)
and settled markets of the last 14 days. Coins: "#<10*outcome+side>" (side 0 = YES, 1 = NO);
balances use "+" with the same number. YES and NO share one book: NO bid = 1 - YES ask.

Writes docs/data/events.json. Run hourly. Places no orders. No wallet address in the output.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from predict import hl, kv, when, title_of  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data", "events.json")
WALLET = os.getenv("FILLS_WALLET", "0xaBA792B28CA16629b3CFF232F30E9dC0868e5EaE")


SHARES_B = 13.087          # Anthropic shares outstanding after the IPO, billions (S-1/A No. 2, before over-allotment)


def resolves(o, f):
    """(settlement time ISO or None, when it settles in words, criteria in words)."""
    name = o.get("name", "")
    if name == "template:binaryPrice" and f.get("time"):
        u = f.get("perp", "?")
        src = f"trade.xyz {u.replace('xyz:', '')} perp" if u.startswith("xyz:") else f"Hyperliquid {u} perp"
        return when(f["time"]).isoformat(), "", f"YES if {src} is above {float(f['threshold']):g} (1-min candle)"
    if name == "template:companyIpoFirstDayMarketCap":
        b = float(f.get("marketCapThresholdB", 0))
        dl = datetime.strptime(f.get("listingDeadline", "")[:8], "%Y%m%d").strftime("%-d %b %Y")
        per = f" ≈ ${b / SHARES_B:,.1f}/share" if f.get("company") == "Anthropic" else ""
        cap = f"${b / 1000:g}T" if b >= 1000 else f"${b:g}B"
        return None, f"Close of first trading day (no IPO by {dl} = NO)", f"YES if day-1 close × shares outstanding ≥ {cap}{per}"
    if name == "template:companyIpoConfirmed":
        return None, "Deadline", "YES if the IPO is confirmed by the deadline"
    return None, "", ""


def main():
    try:
        with open(OUT) as fh:
            prev = json.load(fh)
    except (OSError, ValueError):
        prev = {}
    names = prev.get("names", {})            # outcome -> title, kept so settled markets keep their name
    meta = hl({"type": "outcomeMeta"})
    om = {o["outcome"]: o for o in meta.get("outcomes", [])}
    qd = {}
    for q in meta.get("questions", []):
        for oid in (q.get("namedOutcomes") or []) + ([q["fallbackOutcome"]] if q.get("fallbackOutcome") is not None else []):
            qd[oid] = q.get("description")
    mids = hl({"type": "allMids"})
    xmids = hl({"type": "allMids", "dex": "xyz"})
    orders = [o for o in hl({"type": "frontendOpenOrders", "user": WALLET}) if o["coin"].startswith("#")]
    bals = [b for b in hl({"type": "spotClearinghouseState", "user": WALLET}).get("balances", []) if b["coin"][:1] in "+#" and float(b["total"]) > 0]
    fills = [x for x in hl({"type": "userFillsByTime", "user": WALLET, "startTime": int(time.time() * 1000) - 14 * 86_400_000,
                            "aggregateByTime": True}) or [] if x["coin"].startswith("#")]

    books = {}

    def info(code):
        oid, side = code // 10, code % 10
        o = om.get(oid)
        if o:
            f = kv(o.get("description"))
            names[str(oid)] = title_of(o, f, qd.get(oid))
            exp, when_txt, rule = resolves(o, f)
            under = None
            if o.get("name") == "template:binaryPrice":
                u = f.get("perp", "")
                px = (xmids if u.startswith("xyz:") else mids).get(u)
                under = {"coin": u.replace("xyz:", ""), "price": float(px), "line": float(f["threshold"])} if px else None
        else:
            exp, when_txt, rule, under = None, "settled", "", None
        if oid not in books and o:
            try:
                lv = hl({"type": "l2Book", "coin": f"#{oid * 10}"})["levels"]
                books[oid] = (float(lv[0][0]["px"]) if lv[0] else None, float(lv[1][0]["px"]) if lv[1] else None)
            except Exception:
                books[oid] = (None, None)
        yb, ya = books.get(oid, (None, None))
        bid, ask = (yb, ya) if side == 0 else ((1 - ya) if ya is not None else None, (1 - yb) if yb is not None else None)
        mid = mids.get(f"#{code}")
        rd = lambda x: None if x is None else round(x, 5)
        return {"title": names.get(str(oid), f"Market #{oid}"), "side": "YES" if side == 0 else "NO", "ends": exp,
                "settles": when_txt, "criteria": rule, "underlying": under, "bid": rd(bid), "ask": rd(ask), "mid": float(mid) if mid else None, "live": bool(o)}

    positions = []
    for b in bals:
        code = int(b["coin"][1:]); sz = float(b["total"]); cost = float(b.get("entryNtl") or 0)
        r = info(code)
        val = sz * r["mid"] if r["mid"] is not None else None
        positions.append({**r, "size": sz, "entry": cost / sz if sz else None, "cost": round(cost, 2),
                          "value": round(val, 2) if val is not None else None, "pnl": round(val - cost, 2) if val is not None else None,
                          "if_wins": round(sz - cost, 2)})

    out_orders = []
    for o in orders:
        code = int(o["coin"][1:]); r = info(code)
        sz, px = float(o["sz"]), float(o["limitPx"])
        buy = o["side"] == "B"
        gap = None if r["ask" if buy else "bid"] is None else round((r["ask"] - px) if buy else (px - r["bid"]), 4)
        out_orders.append({**r, "action": "Buy" if buy else "Sell", "size": sz, "price": px, "cost": round(sz * px, 2),
                           "if_wins": round(sz * (1 - px), 2) if buy else None, "gap": gap, "placed": o["timestamp"]})
    out_orders.sort(key=lambda x: (x["title"], x["side"], -x["price"]))

    # settled markets in the last 14 days: cash in (sells + settlement) minus cash out (buys) minus fees
    by = {}
    for x in fills:
        code = int(x["coin"][1:]); oid = code // 10
        d = by.setdefault(oid, {"cash": 0.0, "settled": False, "time": 0, "won": None})
        amt = float(x["sz"]) * float(x["px"])
        d["cash"] += -amt if x["side"] == "B" else amt
        d["cash"] -= float(x.get("fee") or 0)
        d["time"] = max(d["time"], x["time"])
        if x.get("dir") == "Settlement":
            d["settled"] = True
    settled = [{"title": names.get(str(oid), f"market #{oid}"), "pnl": round(d["cash"], 2),
                "time": datetime.fromtimestamp(d["time"] / 1000, timezone.utc).isoformat(timespec="minutes")}
               for oid, d in by.items() if d["settled"] or (oid not in om and not any(int(b["coin"][1:]) // 10 == oid for b in bals))]
    settled.sort(key=lambda s: s["time"], reverse=True)

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "positions": positions,
           "orders": out_orders, "settled": settled, "names": names}
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1)
        fh.write("\n")
    print(f"events: {len(positions)} positions, {len(out_orders)} orders, {len(settled)} settled")


if __name__ == "__main__":
    main()
