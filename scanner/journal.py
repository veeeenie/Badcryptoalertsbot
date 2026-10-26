"""
Trade journal for the trader's trade.xyz / Hyperliquid wallets (JOURNAL_WALLETS).

Reads public data only (no keys): fills (userFillsByTime) and order history (historicalOrders).
Groups fills into trades (a position going from flat to flat on one coin), then for each trade works out:
  - entries (distinct entry orders), average entry, exits, net P&L after fees
  - planned stop: the stop attached to the entry orders (same timestamp), else the first stop placed
  - planned risk = |average entry - stop| x position size, and realised R = net P&L / planned risk
  - whether a take-profit was placed while the trade was open (rule 6: stop with the entry, take-profit once in)
  - whether the stop was ever widened during the trade (rule 7)
  - rule checks: stop and TP placed with entry (6), at most 3 entries and each order >= $10 (4),
    planned reward:risk above 1.5 (8), dollar risk vs the $2 plan (9)
Writes docs/data/journal.json for the dashboard.
"""
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import requests

HL_URL = "https://api.hyperliquid.xyz/info"
WALLET = os.getenv("JOURNAL_WALLET", "")
START = os.getenv("JOURNAL_START", "2026-10-09")        # journal starts the week trading got serious
# several wallets: "address:start-date,address:start-date"
WALLETS = [tuple(x.split(":")) for x in os.getenv("JOURNAL_WALLETS", "").split(",") if ":" in x] or \
          ([(WALLET, START)] if WALLET else [])
PLAN_RISK_USD = float(os.getenv("JOURNAL_RISK_USD", "3"))   # trader's rule, 23 Oct 2026
MIN_ORDER_USD = 10.0
MAX_ENTRIES = 3
MIN_RR = 1.4                                                # trader's gate, 23 Oct 2026
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data", "journal.json")


def hl(payload):
    for i in range(4):
        try:
            r = requests.post(HL_URL, json=payload, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"Hyperliquid API failed for {payload.get('type')}")


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="minutes")


def is_stop(o):
    return o["order"].get("isTrigger") and "Stop" in o["order"].get("orderType", "") and o["order"].get("reduceOnly")


def is_tp(o):
    od = o["order"]
    if "Take Profit" in od.get("orderType", ""):
        return True
    return od.get("reduceOnly") and not od.get("isTrigger") and od.get("orderType") == "Limit"


def build_trades(fills, orders):
    # one row per order id (historicalOrders repeats an order for each status change; keep the first placement)
    by_oid = {}
    for o in sorted(orders, key=lambda o: o["statusTimestamp"]):
        oid = o["order"]["oid"]
        if oid not in by_oid:
            by_oid[oid] = o
    orders_by_coin = defaultdict(list)
    for o in orders:
        orders_by_coin[o["order"]["coin"]].append(o)

    trades = []
    open_trade = {}
    for f in sorted(fills, key=lambda f: f["time"]):
        coin = f["coin"]
        if coin.startswith("@"):            # spot, not perps
            continue
        sz = float(f["sz"])
        start = float(f["startPosition"])
        signed = sz if f["side"] == "B" else -sz
        after = start + signed
        t = open_trade.get(coin)
        if t is None:
            t = {"coin": coin, "side": "long" if signed > 0 else "short", "fills": [],
                 "opened_before_journal": abs(start) > 1e-12}
            open_trade[coin] = t
        t["fills"].append(f)
        if abs(after) < 1e-9:
            trades.append(t)
            del open_trade[coin]
    out = [summarise_trade(t, orders_by_coin[t["coin"]], by_oid, closed=True) for t in trades]
    out += [summarise_trade(t, orders_by_coin[t["coin"]], by_oid, closed=False) for t in open_trade.values()]
    return sorted(out, key=lambda x: x["opened"])


def summarise_trade(t, coin_orders, by_oid, closed):
    side = t["side"]
    sign = 1 if side == "long" else -1
    ent = [f for f in t["fills"] if f["dir"].startswith("Open")]
    ext = [f for f in t["fills"] if f["dir"].startswith("Close")]
    qty_in = sum(float(f["sz"]) for f in ent)
    avg_in = sum(float(f["px"]) * float(f["sz"]) for f in ent) / qty_in if qty_in else None
    qty_out = sum(float(f["sz"]) for f in ext)
    avg_out = sum(float(f["px"]) * float(f["sz"]) for f in ext) / qty_out if qty_out else None
    pnl = sum(float(f["closedPnl"]) for f in t["fills"])
    fees = sum(float(f["fee"]) for f in t["fills"])
    net = pnl - fees
    first, last = t["fills"][0]["time"], t["fills"][-1]["time"]

    # entry orders and their notional
    entry_oids = defaultdict(float)
    for f in ent:
        entry_oids[f["oid"]] += float(f["px"]) * float(f["sz"])
    entry_order_ts = {by_oid[o]["order"]["timestamp"] for o in entry_oids if o in by_oid}
    entry_placed = min(entry_order_ts) if entry_order_ts else first

    # stops for this coin placed from the first entry order until the trade closed
    window_end = last if closed else 10 ** 15
    stops = [o for o in coin_orders if is_stop(o) and entry_placed - 3600_000 <= o["order"]["timestamp"] <= window_end]
    stops_unique = {}
    for o in sorted(stops, key=lambda o: o["order"]["timestamp"]):
        stops_unique.setdefault(o["order"]["oid"], o)
    stop_list = sorted(stops_unique.values(), key=lambda o: o["order"]["timestamp"])
    attached = [o for o in stop_list if o["order"]["timestamp"] in entry_order_ts]
    planned = attached or stop_list[:1]
    stop_px = None
    if planned:
        w = sum(float(o["order"]["origSz"]) or 1 for o in planned)
        stop_px = sum(float(o["order"]["triggerPx"]) * (float(o["order"]["origSz"]) or 1) for o in planned) / w
    widened = False
    if stop_px is not None:
        for o in stop_list:
            px = float(o["order"]["triggerPx"])
            if o["order"]["timestamp"] > (planned[-1]["order"]["timestamp"]) and sign * (stop_px - px) > 1e-12:
                widened = True                    # a later stop further from price than the planned one
    tps = [o for o in coin_orders if is_tp(o) and entry_placed - 3600_000 <= o["order"]["timestamp"] <= window_end]
    # rule 6 (trader, 24 Oct 2026): the stop goes in with the entry; the take-profit is placed once in the trade
    tp_set = bool(tps)
    tp_px = None
    if tps:
        first_tp = min(tps, key=lambda o: o["order"]["timestamp"])
        tp_px = float(first_tp["order"].get("triggerPx") or 0) or float(first_tp["order"].get("limitPx") or 0)

    risk_usd = abs(avg_in - stop_px) * qty_in if (stop_px and avg_in) else None
    r = round(net / risk_usd, 2) if (risk_usd and closed) else None
    planned_rr = None
    if tp_px and stop_px and avg_in and abs(avg_in - stop_px) > 0:
        planned_rr = round(sign * (tp_px - avg_in) / abs(avg_in - stop_px), 2)

    checks = {
        "stop": stop_px is not None,
        "stop_at_entry": bool(attached) or bool(stop_list and stop_list[0]["order"]["timestamp"] <= first + 3600_000),
        "tp_set": tp_set,
        "max_3_entries": len(entry_oids) <= MAX_ENTRIES,
        "min_10_usd": all(v >= MIN_ORDER_USD - 0.01 for v in entry_oids.values()),
        "rr_1_5": planned_rr is not None and planned_rr > MIN_RR,
        "stop_not_widened": not widened,
        "risk_within_plan": risk_usd is not None and risk_usd <= PLAN_RISK_USD * 1.25,
    }
    return {
        "coin": t["coin"], "side": side, "closed": closed,
        "opened": iso(first), "closed_at": iso(last) if closed else None,
        "days": round((last - first) / 86_400_000, 1),
        "entries": len(entry_oids), "avg_entry": avg_in, "size": qty_in,
        "notional": round(avg_in * qty_in, 2) if avg_in else None,
        "exits": len({f["oid"] for f in ext}), "avg_exit": avg_out,
        "stop": stop_px, "tp": tp_px, "planned_rr": planned_rr,
        "risk_usd": round(risk_usd, 2) if risk_usd else None,
        "pnl": round(pnl, 2), "fees": round(fees, 3), "net": round(net, 2), "r": r,
        "maker_entries": sum(1 for f in ent if not f["crossed"]), "taker_entries": sum(1 for f in ent if f["crossed"]),
        "opened_before_journal": t["opened_before_journal"],
        "checks": checks,
        "rules_ok": all(checks.values()),
    }


def summarise(trades):
    closed = [t for t in trades if t["closed"]]
    rs = [t["r"] for t in closed if t["r"] is not None]
    return {
        "trades": len(closed),
        "open": len(trades) - len(closed),
        "wins": sum(1 for t in closed if t["net"] > 0),
        "net_usd": round(sum(t["net"] for t in closed), 2),
        "fees_usd": round(sum(t["fees"] for t in closed), 2),
        "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
        "total_r": round(sum(rs), 2) if rs else None,
        "rules_ok_pct": round(100 * sum(1 for t in closed if t["rules_ok"]) / len(closed)) if closed else None,
        "check_rates": {k: round(100 * sum(1 for t in closed if t["checks"][k]) / len(closed))
                        for k in (closed[0]["checks"] if closed else {})},
    }


def main():
    if not WALLETS:
        print("JOURNAL_WALLETS not set; skipping journal")
        return
    all_trades, wallets = [], []
    for address, start in WALLETS:
        start_ms = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * 1000)
        fills = hl({"type": "userFillsByTime", "user": address, "startTime": start_ms, "aggregateByTime": True})
        orders = hl({"type": "historicalOrders", "user": address})
        trades = build_trades(fills, orders)
        label = "…" + address[-4:]
        for t in trades:
            t["wallet"] = label
        all_trades += trades
        wallets.append({"address": address, "label": label, "since": start, "summary": summarise(trades)})
        print(f"journal {label}: {wallets[-1]['summary']['trades']} closed, {wallets[-1]['summary']['open']} open")
    all_trades.sort(key=lambda x: x["opened"])
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "since": min(w["since"] for w in wallets), "plan_risk_usd": PLAN_RISK_USD,
           "summary": summarise(all_trades), "wallets": wallets, "trades": all_trades}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))


PORTFOLIO_OUT = os.path.join(ROOT, "docs", "data", "portfolio.json")
PORTFOLIO_START = "2026-10-09"      # the chart starts with the journal (trader, 26 Oct 2026)
IST = timezone(timedelta(hours=5, minutes=30))


def week_ends(start, now):
    """Fridays 23:59:59 IST from the first Friday on/after start, up to now (the review week is Sat-Fri IST)."""
    d = datetime.fromisoformat(start).replace(tzinfo=IST)
    while d.weekday() != 4:
        d += timedelta(days=1)
    out = []
    while True:
        end = d.replace(hour=23, minute=59, second=59)
        if end > now:
            break
        out.append(end)
        d += timedelta(days=7)
    return out


def portfolio():
    """Weekly total account value per wallet (Hyperliquid's own account value: USDC including open P&L,
    plus other tokens and lending), kept in docs/data/portfolio.json so the history outlives the API's 30-day window."""
    try:
        with open(PORTFOLIO_OUT) as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {"weeks": []}
    weeks = {w["week_end"]: w for w in old.get("weeks", [])}
    now = datetime.now(timezone.utc)
    current = {}
    for address, _start in WALLETS:
        label = "…" + address[-4:]
        try:
            pf = dict(hl({"type": "portfolio", "user": address}))
        except Exception as e:
            print("portfolio error", label, e, file=sys.stderr)
            continue
        pts = {}
        for period in ("allTime", "month", "week", "day"):
            for t, v in (pf.get(period) or {}).get("accountValueHistory", []):
                pts[int(t)] = float(v)
        series = sorted(pts.items())
        if not series:
            continue
        current[label] = round(series[-1][1], 2)
        for end in week_ends(PORTFOLIO_START, now):
            key = end.date().isoformat()
            before = [v for t, v in series if t <= end.timestamp() * 1000]
            if before:
                weeks.setdefault(key, {"week_end": key, "wallets": {}})["wallets"].setdefault(label, round(before[-1], 2))
    rows = []
    for key in sorted(weeks):
        w = weeks[key]
        w["total"] = round(sum(w["wallets"].values()), 2)
        rows.append(w)
    out = {"generated_at": now.isoformat(timespec="seconds"), "start": PORTFOLIO_START,
           "note": "Week ends are Fridays 23:59 IST. Values include deposits and withdrawals.",
           "weeks": rows, "current": current, "current_total": round(sum(current.values()), 2)}
    with open(PORTFOLIO_OUT, "w") as f:
        json.dump(out, f, indent=1)
    print("portfolio:", out["current_total"], [(r["week_end"], r["total"]) for r in rows])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:             # never break the hourly scan because of the journal
        print("journal error:", e, file=sys.stderr)
    try:
        portfolio()
    except Exception as e:
        print("portfolio error:", e, file=sys.stderr)
