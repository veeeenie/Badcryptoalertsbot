"""
Add, list and remove the trader's price alerts (docs/data/alerts.json), from two places:
  - Telegram: the hourly scan reads new messages sent to the bot (poll_telegram) and replies.
  - The website: the "Add alert" form opens a pre-filled GitHub issue whose title is the command;
    .github/workflows/alerts.yml runs  python scanner/alerts_cmd.py --command "<title>"  for the repo owner only.

Commands (case doesn't matter for the coin):
  /alert COIN PRICE [note]                                  flat price level
  /trendline COIN YYYY-MM-DD PRICE YYYY-MM-DD PRICE [note]   straight line through two points
  /alerts                                                   list active alerts
  /remove ID                                                remove one alert (IDs are shown by /alerts)
Alerts are checked by the hourly scan (check_custom_alerts in scan.py) against the day's high and low.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERTS_PATH = os.path.join(ROOT, "docs", "data", "alerts.json")
HL_URL = "https://api.hyperliquid.xyz/info"
HELP = ("Commands:\n"
        "/alert COIN PRICE [note]\n"
        "/trendline COIN YYYY-MM-DD PRICE YYYY-MM-DD PRICE [note]\n"
        "/alerts  (list)\n"
        "/remove ID")


def load():
    try:
        with open(ALERTS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"alerts": []}


def save(cfg):
    with open(ALERTS_PATH, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def markets():
    """{UPPER name: (exact name, mark price)} for Hyperliquid perps."""
    meta, ctxs = requests.post(HL_URL, json={"type": "metaAndAssetCtxs"}, timeout=30).json()
    out = {}
    for a, c in zip(meta["universe"], ctxs):
        if not a.get("isDelisted"):
            out[a["name"].upper()] = (a["name"], float(c.get("markPx") or 0))
    return out


def num(s):
    s = s.replace(",", "").replace("$", "")
    v = float(s)
    if v <= 0:
        raise ValueError
    return v


def day(s):
    return datetime.strptime(s, "%Y-%m-%d").date().isoformat()


def fmt(x):
    return f"{x:,.0f}" if x >= 1000 else f"{x:.4f}".rstrip("0").rstrip(".") if x >= 1 else f"{x:.6g}"


def handle(text, source="telegram"):
    """Runs one command. Returns (reply text, changed?)."""
    parts = text.strip().split()
    if not parts:
        return HELP, False
    cmd = parts[0].lower().split("@")[0]          # "/alert@Badcryptoalertsbot" in groups
    cfg = load()
    alerts = cfg.setdefault("alerts", [])
    if cmd in ("/alerts", "/list"):
        if not alerts:
            return "No alerts set.", False
        lines = []
        for a in alerts:
            what = fmt(a["price"]) if a.get("type") == "level" else \
                f"line {a['p1']['date']} {fmt(a['p1']['price'])} → {a['p2']['date']} {fmt(a['p2']['price'])}"
            lines.append(f"• {a['id']}: {a['coin']} {what}" + (f" · {a['name']}" if a.get("name") else ""))
        return "Active alerts:\n" + "\n".join(lines), False
    if cmd == "/remove":
        if len(parts) < 2:
            return "Usage: /remove ID (see /alerts for IDs)", False
        aid = parts[1]
        keep = [a for a in alerts if a["id"].lower() != aid.lower()]
        if len(keep) == len(alerts):
            return f"No alert with ID {aid}. Send /alerts to see them.", False
        cfg["alerts"] = keep
        save(cfg)
        return f"Removed alert {aid}.", True
    if cmd in ("/alert", "/trendline"):
        try:
            mk = markets()
        except Exception as e:
            return f"Couldn't reach Hyperliquid to check the coin ({e}). Try again later.", False
        if len(parts) < 3:
            return HELP, False
        coin = mk.get(parts[1].upper())
        if not coin:
            return f"{parts[1]} isn't a Hyperliquid perp I can find.", False
        name, mark = coin
        try:
            if cmd == "/alert":
                price = num(parts[2])
                note = " ".join(parts[3:])
                aid = f"{name}-{fmt(price)}"
                if any(a["id"] == aid for a in alerts):
                    return f"An alert {aid} already exists.", False
                alerts.append({"id": aid, "coin": name, "type": "level", "price": price,
                               "name": note or f"{name} level {fmt(price)}", "touch_pct": 1.0,
                               "source": f"added via {source}, {datetime.now(timezone.utc).date().isoformat()}"})
                save(cfg)
                side = "above" if price > mark else "below"
                return (f"✅ Alert {aid} set: {name} at {fmt(price)} ({side} today's {fmt(mark)}, "
                        f"{abs(price / mark - 1) * 100:.1f}% away). Checked hourly against the day's high and low."), True
            if len(parts) < 6:
                return "Usage: /trendline COIN YYYY-MM-DD PRICE YYYY-MM-DD PRICE [note]", False
            d1, p1, d2, p2 = day(parts[2]), num(parts[3]), day(parts[4]), num(parts[5])
            if d1 == d2:
                return "The two points need different dates.", False
            note = " ".join(parts[6:])
            aid = f"{name}-line-{d2}"
            if any(a["id"] == aid for a in alerts):
                return f"An alert {aid} already exists.", False
            alerts.append({"id": aid, "coin": name, "type": "trendline", "name": note or f"{name} trendline",
                           "p1": {"date": d1, "price": p1}, "p2": {"date": d2, "price": p2}, "touch_pct": 1.0,
                           "source": f"added via {source}, {datetime.now(timezone.utc).date().isoformat()}"})
            save(cfg)
            return f"✅ Trendline alert {aid} set on {name}. Telegram when price reaches the line and on a weekly close through it.", True
        except ValueError:
            return "I couldn't read the numbers or dates. " + HELP, False
    return HELP, False


def poll_telegram(state):
    """Called by the hourly scan: answers commands sent to the bot since the last check (chat must match)."""
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    changed = False
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                         params={"offset": state.get("tg_offset", 0) + 1, "timeout": 0}, timeout=20).json()
    except Exception as e:
        print("telegram poll error", e, file=sys.stderr)
        return False
    for u in r.get("result", []):
        state["tg_offset"] = max(state.get("tg_offset", 0), u["update_id"])
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(chat):
            continue                                   # only the trader's chat
        text = (m.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        reply, ch = handle(text, "Telegram")
        changed |= ch
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": reply, "disable_web_page_preview": True}, timeout=20)
        except Exception as e:
            print("telegram reply error", e, file=sys.stderr)
    return changed


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--command":
        reply, changed = handle(sys.argv[2], "the website")
        print(reply)
        # tell the workflow whether alerts.json changed
        if os.getenv("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(f"changed={'1' if changed else '0'}\n")
    else:
        print(__doc__)
