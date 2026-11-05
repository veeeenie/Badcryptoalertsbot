"""
Setup Desk test bench: replays a strategy day by day over the candle history and reports results in R.

Rules of the bench (the same for every strategy, so versions can be compared fairly):
  - Point-in-time universe: each day, the top N coins by trailing 7-day dollar volume, using only
    days before it (delisted coins included while they traded), not today's survivors.
  - No look-ahead: at the start of day d, a strategy sees daily candles up to d-1 and weekly candles
    that had closed by d. Its orders can only fill from day d on.
  - While a coin is flat, the plan is rebuilt every day (like the hourly live scan) and its limit
    orders replace the previous day's. Once an order fills, the plan's levels are fixed.
  - Fills: a long's limit fills when the day's low reaches it (short: the high).
  - Same-day ambiguity (daily candles can't show the order of events), resolved against us:
    a day that touches the stop is a stop, even if it also touched a take-profit; entries above the
    stop fill first; no take-profit on the day of the first fill.
  - Exits: half at take-profit 1 (unfilled entries are cancelled then), the rest at take-profit 2.
    With one take-profit, all of it. A position still open after MAX_HOLD days closes at that close.
  - 1R = the planned loss if every order fills and the stop is hit. Costs are taken off in R:
    0.015% on limit entries, 0.045% on stops, take-profits and time exits, and funding of 0.0313% a
    day on longs (Hyperliquid's baseline rate; shorts are charged nothing; real funding varies).
  - One trade per coin at a time; coins are independent (no cap on trades open at once).
  - Split: in-sample = trades signalled before 2026-01-01, out-of-sample = 2026.

Strategies are functions strategy(ctx) -> plan dict or None, where the plan has "side",
"entries" (prices), "weights" (sum 1), "stop", "tps" (1 or 2 prices) and optional "tag".
"""
import gzip
import json
import math
import os
import sys
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timezone

DAY = 86_400_000
WEEK = 7 * DAY
FEE_MAKER, FEE_TAKER = 0.00015, 0.00045
FUNDING_LONG_DAY = 0.000313
MAX_HOLD = 180
OOS_FROM = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "candles_1d.json.gz")


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def load(path=DATA):
    with gzip.open(path, "rt") as f:
        raw = json.load(f)
    coins = {}
    for name, v in raw["coins"].items():
        cs = [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]} for r in v["candles"]]
        if len(cs) >= 30:
            coins[name] = {"candles": cs, "t": [c["t"] for c in cs], "delisted": v["delisted"], "max_lev": v.get("max_lev")}
    return coins, raw.get("generated_at")


def weekly(daily):
    """Weeks starting Monday 00:00 UTC (same as the live scanner)."""
    out, cur = [], None
    for c in daily:
        dt = datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc)
        wk = c["t"] - (dt.weekday() * DAY) - (c["t"] % DAY)
        if cur is None or cur["t"] != wk:
            cur = {"t": wk, "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"]}
            out.append(cur)
        else:
            cur["h"] = max(cur["h"], c["h"]); cur["l"] = min(cur["l"], c["l"]); cur["c"] = c["c"]
    return out


def universe_by_day(coins, top_n):
    """{day_ms: set(coins)}: top_n by mean dollar volume over the 7 days before."""
    vol = defaultdict(dict)
    for name, v in coins.items():
        cs = v["candles"]
        pref = [0.0]
        for c in cs:
            pref.append(pref[-1] + c["v"] * c["c"])
        for i in range(7, len(cs) + 1):
            day = cs[i - 1]["t"] + DAY   # the day after the last of the 7
            vol[day][name] = (pref[i] - pref[i - 7]) / 7
    return {d: set(sorted(m, key=m.get, reverse=True)[:top_n]) for d, m in vol.items()}


class Ctx:
    """What a strategy may see at the start of day index i of one coin."""
    def __init__(self, coin, daily, weeks, i, max_lev):
        self.coin, self.max_lev = coin, max_lev
        self.day = daily[i]["t"]
        self.daily = daily[:i]                       # closed days only
        self.price = daily[i - 1]["c"]               # last close
        self.weeks = [w for w in weeks if w["t"] + WEEK <= self.day]


def simulate(coins, strategy, start, end, top_n=100, ready_pct=10.0, max_hold=MAX_HOLD, progress=False):
    uni = universe_by_day(coins, top_n)
    trades = []
    names = sorted(coins)
    for k, name in enumerate(names):
        v = coins[name]
        daily = v["candles"]
        weeks = weekly(daily)
        i0 = max(2, bisect_left(v["t"], start))
        i = i0
        if progress and k % 20 == 0:
            print(f"  {k}/{len(names)} {name}", file=sys.stderr)
        while i < len(daily) and daily[i]["t"] < end:
            d = daily[i]["t"]
            if name not in uni.get(d, ()):
                i += 1; continue
            ctx = Ctx(name, daily, weeks, i, v["max_lev"])
            try:
                p = strategy(ctx)
            except Exception as e:
                p = None
            if not p:
                i += 1; continue
            first = p["entries"][0]
            dist = (first / ctx.price - 1) if p["side"] == "short" else (1 - first / ctx.price)
            if ready_pct is not None and dist * 100 > ready_pct:
                i += 1; continue
            res, j = play(p, daily, i, max_hold)
            if res is None:          # nothing filled today: rebuild the plan tomorrow
                i += 1; continue
            res.update({"coin": name, "side": p["side"], "tag": p.get("tag", ""), "signal": iso(d), "signal_ms": d})
            trades.append(res)
            i = j + 1                # next plan from the day after the exit
    return trades


def play(p, daily, i, max_hold):
    """Play a plan from day i. Returns (trade, exit_index) or (None, i) if nothing filled on day i."""
    long = p["side"] == "long"
    sgn = 1 if long else -1
    es, ws, stop, tps = p["entries"], p["weights"], p["stop"], list(p["tps"])
    r_unit = sum(w * abs(e - stop) for e, w in zip(es, ws))
    if r_unit <= 0:
        return None, i
    qty = [w / r_unit for w in ws]              # sized so that all filled + stopped = -1R
    filled = [False] * len(es)
    pos = 0.0; cost = 0.0; pnl = 0.0; avg = 0.0
    split = [0.5, 0.5] if len(tps) == 2 else [1.0]
    tp_done = 0
    first_fill = None
    mfe = 0.0
    reason = "time"
    j = i
    while j < len(daily):
        c = daily[j]
        new_fill = False
        if first_fill is None or tp_done == 0:
            for k, e in enumerate(es):
                if not filled[k] and (c["l"] <= e if long else c["h"] >= e):
                    filled[k] = True; new_fill = True
                    avg = (avg * pos + e * qty[k]) / (pos + qty[k]); pos += qty[k]
                    cost += FEE_MAKER * e * qty[k]
        if first_fill is None:
            if not new_fill:
                return None, i
            first_fill = j
        if pos > 0:
            hit_stop = c["l"] <= stop if long else c["h"] >= stop
            if hit_stop:
                pnl += sgn * (stop - avg) * pos; cost += FEE_TAKER * stop * pos; pos = 0
                reason = "stop" if tp_done == 0 else "tp1+stop"
                break
            fav = (c["h"] - avg) if long else (avg - c["l"])
            mfe = max(mfe, fav)
            if j > first_fill or tp_done > 0:
                while tp_done < len(tps):
                    t = tps[tp_done]
                    if (c["h"] >= t) if long else (c["l"] <= t):
                        q = pos if tp_done == len(tps) - 1 else pos * split[tp_done] / sum(split[tp_done:])
                        pnl += sgn * (t - avg) * q; cost += FEE_TAKER * t * q; pos -= q
                        tp_done += 1
                    else:
                        break
                if tp_done == len(tps) or pos <= 1e-12:
                    reason = "tp2" if len(tps) == 2 else "tp"
                    pos = 0
                    break
            if long:
                cost += FUNDING_LONG_DAY * c["c"] * pos
            if j - first_fill >= max_hold:
                pnl += sgn * (c["c"] - avg) * pos; cost += FEE_TAKER * c["c"] * pos; pos = 0
                reason = "time"
                break
        j += 1
    else:   # ran out of data: mark at the last close, flagged as open
        c = daily[-1]
        pnl += sgn * (c["c"] - avg) * pos; pos = 0
        reason = "open"
        j = len(daily) - 1
    nfill = sum(filled)
    return {"r": round(pnl - cost, 4), "gross_r": round(pnl, 4), "fills": nfill, "orders": len(es), "reason": reason,
            "entry_day": iso(daily[first_fill]["t"]), "exit_day": iso(daily[j]["t"]), "exit_ms": daily[j]["t"],
            "days": j - first_fill, "mfe_r": round(mfe * sum(q for q, f in zip(qty, filled) if f), 3)}, j


def stats(trades):
    if not trades:
        return {"trades": 0}
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    loss = [r for r in rs if r <= 0]
    eq, peak, dd = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda t: t["exit_ms"]):
        eq += t["r"]; peak = max(peak, eq); dd = min(dd, eq - peak)
    return {"trades": len(rs), "win_pct": round(100 * len(wins) / len(rs)), "avg_r": round(sum(rs) / len(rs), 3),
            "total_r": round(sum(rs), 1), "max_dd_r": round(dd, 1),
            "profit_factor": round(sum(wins) / -sum(loss), 2) if loss and sum(loss) < 0 else None,
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None, "avg_loss": round(sum(loss) / len(loss), 2) if loss else None}


def report(trades, label=""):
    groups = {"all": trades,
              "in-sample (to 2025)": [t for t in trades if t["signal_ms"] < OOS_FROM],
              "out-of-sample (2026)": [t for t in trades if t["signal_ms"] >= OOS_FROM],
              "longs": [t for t in trades if t["side"] == "long"],
              "shorts": [t for t in trades if t["side"] == "short"]}
    for y in sorted({t["signal"][:4] for t in trades}):
        groups[y] = [t for t in trades if t["signal"][:4] == y]
    out = {k: stats(v) for k, v in groups.items()}
    stopped = [t for t in trades if t["reason"] == "stop"]
    out["diagnostics"] = {
        "stopped": len(stopped),
        "stopped_after_1r_in_favour": sum(1 for t in stopped if t["mfe_r"] >= 1),
        "exit_reasons": dict(sorted(defaultdict(int, {r: sum(1 for t in trades if t["reason"] == r) for r in {t["reason"] for t in trades}}).items())),
        "avg_fills": round(sum(t["fills"] for t in trades) / len(trades), 2) if trades else None,
    }
    return out


def table(rep):
    lines = ["| Group | Trades | Win | Avg R | Total R | Max DD | PF |", "|---|---|---|---|---|---|---|"]
    for k, s in rep.items():
        if k == "diagnostics" or not s.get("trades"):
            continue
        lines.append(f"| {k} | {s['trades']} | {s['win_pct']}% | {s['avg_r']:+.2f} | {s['total_r']:+.1f} | {s['max_dd_r']:.1f} | {s['profit_factor']} |")
    return "\n".join(lines)
