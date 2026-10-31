"""
Telegram alerts for the trading wallet (trader, 31 Oct 2026): every order that fills (entries, adds,
take-profits, stops, manual closes) and every order Hyperliquid rejects (e.g. a take-profit under the
$10 minimum). Covers all markets, trade.xyz stocks included.

Run every couple of minutes by the scan workflow's hourly loop. The cursor lives in
docs/data/fills_state.json and is only rewritten when something new was seen, so the workflow commits
it rarely. The first run only sets the cursor (no alerts for old fills).
"""
import json
import os
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "docs", "data", "fills_state.json")
HL_URL = "https://api.hyperliquid.xyz/info"
WALLET = os.getenv("FILLS_WALLET", "0xaBA792B28CA16629b3CFF232F30E9dC0868e5EaE")
IST = timezone(timedelta(hours=5, minutes=30))
OVERLAP_MS = 10 * 60_000          # re-read the last 10 minutes; fills already sent are skipped by id


def hl(body):
    r = requests.post(HL_URL, json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def fmt(x):
    x = float(x)
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.4f}".rstrip("0").rstrip(".") if abs(x) >= 1 else f"{x:.6g}"


def name(coin):
    if ":" in coin:
        dex, c = coin.split(":", 1)
        return f"{c} ({'trade.xyz stocks' if dex == 'xyz' else dex})"
    return coin


def when(ms):
    return datetime.fromtimestamp(ms / 1000, IST).strftime("%d-%b %H:%M IST")


def telegram(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("TELEGRAM not set:\n" + text)
        return True
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=20)
        return r.ok
    except Exception as e:
        print("telegram error", e, file=sys.stderr)
        return False


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(st):
    st["seen_fills"] = st["seen_fills"][-400:]
    st["seen_rejects"] = st["seen_rejects"][-200:]
    with open(STATE, "w") as f:
        json.dump(st, f, indent=1)
        f.write("\n")


def kind(order_type, d, closing):
    t = (order_type or "").lower()
    if "take profit" in t:
        return "🎯", "take-profit hit"
    if "stop" in t:
        return "🛑", "stop hit"
    if closing:
        return "✋", "closed"
    return "✅", "entry filled"


def main():
    now = int(time.time() * 1000)
    st = load()
    if st is None:   # first run: start from now, don't replay history
        save({"since_ms": now, "seen_fills": [], "seen_rejects": []})
        print("fills: cursor set")
        return
    start = max(0, st["since_ms"] - OVERLAP_MS)
    fills = hl({"type": "userFillsByTime", "user": WALLET, "startTime": start, "aggregateByTime": False}) or []
    seen = set(st["seen_fills"])
    new = [f for f in fills if str(f.get("tid")) not in seen]
    orders = {}
    rejects = []
    try:   # order types (to tell a take-profit from a stop) and rejected orders
        for o in hl({"type": "historicalOrders", "user": WALLET}) or []:
            orders.setdefault(o["order"]["oid"], o["order"])
            if o.get("statusTimestamp", 0) >= start and str(o.get("status", "")).lower().endswith("rejected"):
                rejects.append(o)
    except Exception as e:
        print("historicalOrders failed", e, file=sys.stderr)

    # one message per order: fills of the same order (oid) are added up
    by_oid = OrderedDict()
    for f in sorted(new, key=lambda x: x["time"]):
        by_oid.setdefault(f["oid"], []).append(f)
    msgs = []
    for oid, fs in by_oid.items():
        f0 = fs[0]
        sz = sum(float(f["sz"]) for f in fs)
        px = sum(float(f["px"]) * float(f["sz"]) for f in fs) / sz
        pnl = sum(float(f.get("closedPnl") or 0) for f in fs)
        fee = sum(float(f.get("fee") or 0) for f in fs)
        d = f0.get("dir", "")
        closing = d.startswith("Close") or "Liquidat" in d
        buy = f0["side"] == "B"
        o = orders.get(oid, {})
        icon, what = kind(o.get("orderType"), d, closing)
        if "Liquidat" in d:
            icon, what = "🚨", "LIQUIDATED"
        start_pos = float(f0.get("startPosition") or 0)
        end_pos = start_pos + (sz if buy else -sz)
        if abs(end_pos) < 1e-9:
            pos = "position closed"
        else:
            pos = f"position now {fmt(abs(end_pos))} {'long' if end_pos > 0 else 'short'}"
        side = d if d else ("Buy" if buy else "Sell")
        line = (f"{icon} {name(f0['coin'])}: {what}\n"
                f"{side.lower()} {fmt(sz)} at {fmt(px)} (${sz * px:,.2f}) · {pos}")
        if closing:
            line += f"\nP&L {'+' if pnl - fee >= 0 else '−'}${abs(pnl - fee):,.2f} after fees"
        line += f"\n{when(fs[-1]['time'])}"
        msgs.append(line)

    rseen = set(st["seen_rejects"])
    for o in rejects:
        key = f"{o['order']['oid']}:{o['status']}"
        if key in rseen:
            continue
        od = o["order"]
        val = float(od.get("origSz") or 0) * float(od.get("triggerPx") or od.get("limitPx") or 0)
        why = "under Hyperliquid's $10 minimum order" if "minTradeNtl" in o["status"] else o["status"]
        msgs.append(f"⚠️ {name(od['coin'])}: {od.get('orderType', 'order')} at {fmt(od.get('triggerPx') or od.get('limitPx'))} "
                    f"was triggered but REJECTED ({why}{f', ${val:,.2f}' if val else ''}). Nothing was closed: check the position.\n"
                    f"{when(o['statusTimestamp'])}")
        st["seen_rejects"].append(key)

    if not msgs:
        print("fills: nothing new")
        return
    if telegram("\n\n".join(msgs)):
        st["seen_fills"] += [str(f["tid"]) for f in new]
        st["since_ms"] = max([st["since_ms"]] + [f["time"] for f in new])
        save(st)
        print(f"fills: sent {len(msgs)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("fills failed", e, file=sys.stderr)
