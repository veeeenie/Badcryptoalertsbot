"""
Rates strip (trader, 10 Nov 2026): US vs India policy rate, inflation, 10-year yield and real yield,
shown under the market strip, plus a Telegram alert on the US 10-year real yield.

Sources (all read automatically except India's inflation):
  US policy rate   - New York Fed: effective fed funds rate and the target range.
  US inflation     - BLS consumer price index (CPI-U, all items), change on a year earlier.
  US 10Y / real    - US Treasury daily par yield curve and real yield curve (10-year). The real yield
                     is the market yield on 10-year inflation-protected bonds (TIPS).
  India repo       - RBI website, "Policy Repo Rate".
  India 10Y        - CNBC quote feed (IN10Y; not an official source).
  India inflation  - docs/data/rates_manual.json, updated by the twice-daily research task after each
                     MOSPI release (~12th of the month). India has no traded inflation-linked bond, so its
                     real yield is an estimate: 10-year yield minus the latest CPI inflation.

Alert: the US 10-year real yield closes below 2.40% (squeeze on gold easing) or above 2.95% (new
high for 2026). Fires once on a crossing; the first run only records where it is.

Writes docs/data/rates.json (keeps 40 days of daily values for the week-ago change). Run hourly.
"""
import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data", "rates.json")
MANUAL = os.path.join(ROOT, "docs", "data", "rates_manual.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
LOW, HIGH = 2.40, 2.95          # US 10-year real yield alert levels (%), agreed 10 Nov 2026
IST = timezone(timedelta(hours=5, minutes=30))


def treasury(kind):
    """[(iso_date, 10-year %)] for this year and last, oldest first. kind: 'real' or 'nominal'."""
    t = "daily_treasury_real_yield_curve" if kind == "real" else "daily_treasury_yield_curve"
    out = {}
    y = datetime.now(timezone.utc).year
    for yr in (y - 1, y):
        r = requests.get(f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{yr}/all",
                         params={"type": t, "field_tdr_date_value": yr, "page": "", "_format": "csv"}, headers=UA, timeout=40)
        r.raise_for_status()
        rows = list(csv.reader(io.StringIO(r.text)))
        if not rows:
            continue
        i = [h.strip() for h in rows[0]].index("10 YR" if kind == "real" else "10 Yr")
        for row in rows[1:]:
            if len(row) > i and row[i].strip():
                m, d, yy = row[0].split("/")
                out[f"{yy}-{m}-{d}"] = float(row[i])
    return sorted(out.items())


def fed():
    r = requests.get("https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json", timeout=30)
    r.raise_for_status()
    x = r.json()["refRates"][0]
    return {"effective": x["percentRate"], "lo": x["targetRateFrom"], "hi": x["targetRateTo"], "as_of": x["effectiveDate"]}


def us_cpi():
    r = requests.get("https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0", timeout=30)
    r.raise_for_status()
    data = r.json()["Results"]["series"][0]["data"]
    idx = {(int(d["year"]), d["period"]): float(d["value"]) for d in data if d["period"].startswith("M") and d["period"] != "M13"}
    (y, p), v = max(idx.items())
    prev = idx.get((y - 1, p))
    if not prev:
        raise ValueError("no year-earlier CPI")
    month = datetime(y, int(p[1:]), 1).strftime("%b %Y")
    return {"value": round((v / prev - 1) * 100, 1), "month": month}


def rbi_repo():
    r = requests.get("https://www.rbi.org.in/", headers=UA, timeout=40)
    r.raise_for_status()
    txt = re.sub(r"\s+", " ", r.text)
    m = re.search(r"Policy Repo Rate.{0,80}?:\s*([\d.]+)\s*%", txt)
    if not m:
        raise ValueError("repo rate not found on rbi.org.in")
    return float(m.group(1))


def in10y():
    r = requests.get("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol",
                     params={"symbols": "IN10Y", "requestMethod": "itv", "noform": 1, "partnerId": 2, "fund": 1, "exthrs": 1, "output": "json"},
                     headers=UA, timeout=30)
    r.raise_for_status()
    q = r.json()["FormattedQuoteResult"]["FormattedQuote"][0]
    return float(q["last"].rstrip("%")), q.get("last_time", "")[:10]


def gold_price():
    """trade.xyz GOLD, else PAXG (gold token) from Hyperliquid; None if neither."""
    for body, key in (({"type": "allMids", "dex": "xyz"}, "xyz:GOLD"), ({"type": "allMids"}, "PAXG")):
        try:
            m = requests.post("https://api.hyperliquid.xyz/info", json=body, timeout=20).json()
            if key in m:
                return float(m[key])
        except Exception:
            pass
    return None


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


def week_ago(series, date):
    """Value on the last date at least 7 days before `date` in [(iso, v)]."""
    cut = (datetime.fromisoformat(date) - timedelta(days=7)).date().isoformat()
    old = [v for d, v in series if d <= cut]
    return old[-1] if old else None


def safe(fn, errors, name):
    try:
        return fn()
    except Exception as e:
        errors.append(f"{name}: {type(e).__name__}")
        print(f"rates: {name} failed: {e}", file=sys.stderr)
        return None


def main():
    try:
        with open(OUT) as f:
            prev = json.load(f)
    except (OSError, ValueError):
        prev = {}
    try:
        with open(MANUAL) as f:
            manual = json.load(f)
    except (OSError, ValueError):
        manual = {}
    errors = []
    real = safe(lambda: treasury("real"), errors, "US real yield") or []
    nom = safe(lambda: treasury("nominal"), errors, "US 10Y") or []
    fd = safe(fed, errors, "fed funds")
    # BLS allows few keyless requests a day: refresh US CPI every 6 hours only
    cpi = prev.get("us", {}).get("cpi")
    last = prev.get("us_cpi_checked", "")
    if not cpi or not last or datetime.now(timezone.utc) - datetime.fromisoformat(last) > timedelta(hours=6):
        got = safe(us_cpi, errors, "US CPI")
        if got:
            cpi, last = got, datetime.now(timezone.utc).isoformat(timespec="seconds")
    repo = safe(rbi_repo, errors, "RBI repo")
    iy = safe(in10y, errors, "India 10Y")

    # India 10Y has no free history feed: keep our own daily record for the week-ago change
    hist = prev.get("in10y_hist", {})
    if iy:
        hist[iy[1] or datetime.now(timezone.utc).date().isoformat()] = iy[0]
    cut = (datetime.now(timezone.utc) - timedelta(days=40)).date().isoformat()
    hist = {d: v for d, v in sorted(hist.items()) if d >= cut}
    old = prev.get("us", {})
    oldi = prev.get("india", {})

    us = {
        "policy": {"lo": fd["lo"], "hi": fd["hi"], "effective": fd["effective"], "as_of": fd["as_of"]} if fd else old.get("policy"),
        "cpi": cpi or old.get("cpi"),
        "y10": {"value": nom[-1][1], "as_of": nom[-1][0], "wk": week_ago(nom, nom[-1][0])} if nom else old.get("y10"),
        "real10": {"value": real[-1][1], "as_of": real[-1][0], "wk": week_ago(real, real[-1][0]),
                   "ytd_lo": min((v for d, v in real if d[:4] == real[-1][0][:4]), default=None),
                   "ytd_hi": max((v for d, v in real if d[:4] == real[-1][0][:4]), default=None)} if real else old.get("real10"),
    }
    hs = sorted(hist.items())
    ic = manual.get("india_cpi")
    india = {
        "repo": repo if repo is not None else oldi.get("repo"),
        "cpi": ic,
        "y10": {"value": iy[0], "as_of": iy[1], "wk": week_ago(hs, hs[-1][0]) if hs else None} if iy else oldi.get("y10"),
    }
    if india["y10"] and ic:
        india["real10_est"] = round(india["y10"]["value"] - ic["value"], 2)
    if us["policy"] and us["cpi"]:
        us["policy_real"] = round((us["policy"]["lo"] + us["policy"]["hi"]) / 2 - us["cpi"]["value"], 2)
    if india["repo"] is not None and ic:
        india["policy_real"] = round(india["repo"] - ic["value"], 2)

    # alert on the US 10-year real yield: once per crossing of LOW or HIGH
    zone = prev.get("alert_zone")
    if real:
        v, d = real[-1][1], real[-1][0]
        now_zone = "low" if v < LOW else "high" if v > HIGH else "mid"
        if zone and now_zone != zone and now_zone in ("low", "high"):
            g = gold_price()
            gl = f"\nGold ${g:,.0f}" if g else ""
            msg = (f"📉 US 10-year real yield closed at {v:.2f}% ({d}), below {LOW:.2f}%.\n"
                   f"Real rates are falling: the squeeze on gold is easing.{gl}") if now_zone == "low" else (
                   f"📈 US 10-year real yield closed at {v:.2f}% ({d}), above {HIGH:.2f}%: a new high for the year.\n"
                   f"Real rates rising: a headwind for gold.{gl}")
            if not telegram(msg):
                now_zone = zone          # retry next run
        zone = now_zone

    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "us": us, "india": india,
           "alert": {"low": LOW, "high": HIGH}, "alert_zone": zone, "in10y_hist": hist, "us_cpi_checked": last, "errors": errors}
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print("rates: written", "; errors: " + "; ".join(errors) if errors else "")


if __name__ == "__main__":
    main()
