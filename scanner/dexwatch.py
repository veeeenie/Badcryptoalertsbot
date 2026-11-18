"""
DEX watchlist (trader, 18 Nov 2026): coins that aren't on Hyperliquid, read from DexScreener's free API.

The list lives in docs/data/dexwatch_list.json:
  {"tokens": [{"symbol": "CARDS", "chain": "solana", "pair": "<pool address>",
               "alerts": [{"price": 0.18, "note": "..."}]}]}
Each run writes docs/data/dexwatch.json: price, 1h/6h/24h change, 7-day change (from our own hourly record),
24h volume, pool liquidity, market cap and FDV per token.

Alerts: a Telegram message when the price crosses an alert level (either direction), once per crossing.
The first run only records which side of each level the price is on. Run hourly.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, "docs", "data", "dexwatch_list.json")
OUT = os.path.join(ROOT, "docs", "data", "dexwatch.json")


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


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fmt(p):
    return f"{p:.6g}"


def main():
    try:
        with open(LIST) as f:
            tokens = json.load(f).get("tokens", [])
    except (OSError, ValueError):
        tokens = []
    try:
        with open(OUT) as f:
            prev = {t["key"]: t for t in json.load(f).get("tokens", [])}
    except (OSError, ValueError):
        prev = {}

    now = datetime.now(timezone.utc)
    cut = (now - timedelta(days=8)).isoformat(timespec="minutes")
    out = []
    for t in tokens:
        key = f"{t['chain']}:{t['pair']}".lower()
        old = prev.get(key, {})
        row = {"key": key, "symbol": t.get("symbol"), "chain": t["chain"], "pair": t["pair"],
               "hist": old.get("hist", []), "levels": old.get("levels", {})}
        try:
            r = requests.get(f"https://api.dexscreener.com/latest/dex/pairs/{t['chain']}/{t['pair']}", timeout=30)
            r.raise_for_status()
            p = (r.json().get("pairs") or [None])[0]
            if not p:
                raise ValueError("pair not found")
        except Exception as e:
            print(f"dexwatch: {t.get('symbol')} failed: {e}", file=sys.stderr)
            out.append({**old, **{k: row[k] for k in ("key", "symbol", "chain", "pair")}, "error": str(e)[:120]})
            continue
        price = fnum(p.get("priceUsd"))
        ch = p.get("priceChange") or {}
        row.update({
            "symbol": p["baseToken"]["symbol"], "name": p["baseToken"]["name"], "quote": p["quoteToken"]["symbol"],
            "dex": p.get("dexId"), "url": p.get("url"), "price": price,
            "ch1h": fnum(ch.get("h1")), "ch6h": fnum(ch.get("h6")), "ch24h": fnum(ch.get("h24")),
            "vol24": fnum((p.get("volume") or {}).get("h24")), "liq": fnum((p.get("liquidity") or {}).get("usd")),
            "mcap": fnum(p.get("marketCap")), "fdv": fnum(p.get("fdv")),
        })
        # our own hourly price record, for the 7-day change
        if price:
            row["hist"] = [h for h in row["hist"] if h[0] >= cut] + [[now.isoformat(timespec="minutes"), price]]
            wk = (now - timedelta(days=7)).isoformat(timespec="minutes")
            old_px = [h[1] for h in row["hist"] if h[0] <= wk]
            row["ch7d"] = round((price / old_px[-1] - 1) * 100, 2) if old_px else None
        # alerts: once per crossing of each level
        alerts = []
        for a in t.get("alerts", []):
            lv = float(a["price"])
            side = "above" if price >= lv else "below"
            was = row["levels"].get(str(lv))
            if price and was and was != side:
                arrow = "📈" if side == "above" else "📉"
                d24 = f"({row['ch24h']:+.1f}% 24h) " if row["ch24h"] is not None else ""
                msg = f"{arrow} {row['symbol']} crossed {side} {fmt(lv)}: now ${fmt(price)} {d24}on {row['dex']}."
                if a.get("note"):
                    msg += f"\n{a['note']}"
                if not telegram(msg + f"\n{row['url']}"):
                    side = was          # retry next run
            if price:
                row["levels"][str(lv)] = side
            alerts.append({"price": lv, "note": a.get("note", ""), "side": row["levels"].get(str(lv))})
        row["levels"] = {k: v for k, v in row["levels"].items() if float(k) in [float(a["price"]) for a in t.get("alerts", [])]}
        row["alerts"] = alerts
        out.append(row)

    with open(OUT, "w") as f:
        json.dump({"generated_at": now.isoformat(timespec="seconds"), "tokens": out}, f, indent=1)
        f.write("\n")
    print(f"dexwatch: {len(out)} tokens")


if __name__ == "__main__":
    main()
