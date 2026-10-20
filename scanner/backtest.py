"""
Walk-forward backtest of the exact Setup Desk rules (scanner/scan.py: analyse()).

For each coin in today's top-100 Hyperliquid universe, step through its daily history.
On each step day t (every STEP days), run analyse() on the candles up to and including day t,
as if day t's close were the live price. Any setup it returns is then played forward on later
days only (no look-ahead):

  - Entries are resting limits at the three tranche prices (30/30/40). A tranche fills when the
    day's low (long) / high (short) reaches it. Unfilled tranches are cancelled after
    ENTRY_DAYS, or as soon as target 1 is hit.
  - The stop is checked every day after any fill. If a day touches both the stop and a target,
    the stop is assumed to hit first (conservative).
  - Exits: half of the filled size at target 1, the rest at target 2 (or all at T1 if no T2).
  - A trade still open after HOLD_DAYS is closed at that day's close.
  - Fees: 0.015% on limit entries, 0.045% on exits (trade.xyz/Hyperliquid base tiers).

Result per trade is in R, where 1R = the planned dollar risk of the full position
(average entry to stop). A partially filled trade risks less than 1R.

One setup per coin at a time: no new setup on a coin until the previous one has finished.

Known limits (printed in the report): the universe is today's top 100 (survivorship bias),
daily candles can't tell the order of events inside a day, and there's no slippage on stops.
"""
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan  # noqa: E402

STEP = int(os.getenv("BT_STEP", "2"))          # evaluate every N days
ENTRY_DAYS = 10                                 # limits live this long
HOLD_DAYS = 60                                  # max holding period after first fill
RUNNER_MAX_DAYS = 365                           # a weekly-exit runner can ride up to a year
WARMUP_DAYS = 400                               # history needed before the first step
FEE_IN, FEE_OUT = 0.00015, 0.00045
OUT_PATH = os.path.join(scan.DATA_DIR, "backtest.json")


def play(setup, future, be_after_t1=False, runner=False, week_end=None, start=0):
    """runner=True: after T1 the rest is not closed at T2; it is closed at the close of the first
    week that closes beyond the weekly 25 EMA against the trade (week_end maps a daily index to
    (weekly close, weekly 25 EMA) on the last day of each week)."""
    """Simulate one setup on the candles after it was created. Returns a result dict or None."""
    side = 1 if setup["side"] == "long" else -1
    entries = [(e["px"], e["pct"] / 100) for e in setup["entries"]]
    stop, t1, t2 = setup["stop"], setup["t1"], setup["t2"]
    risk_unit = abs(setup["entry"] - stop)          # per unit of full size
    filled = []                                     # (price, qty)
    open_qty = 0.0
    pnl = 0.0
    fees = 0.0
    t1_done = False
    first_fill_day = None
    for i, c in enumerate(future):
        # 1) fills (only while entries are live)
        if not t1_done and i < ENTRY_DAYS:
            for j, (px, q) in enumerate(entries):
                if q and ((side == 1 and c["l"] <= px) or (side == -1 and c["h"] >= px)):
                    filled.append((px, q))
                    open_qty += q
                    fees += px * q * FEE_IN
                    entries[j] = (px, 0)
                    if first_fill_day is None:
                        first_fill_day = i
        if open_qty == 0 and not filled:
            if i >= ENTRY_DAYS - 1:
                return None                         # never filled: not a trade
            continue
        avg = sum(p * q for p, q in filled) / sum(q for _, q in filled)
        # 2) stop first (conservative)
        if open_qty > 0 and ((side == 1 and c["l"] <= stop) or (side == -1 and c["h"] >= stop)):
            pnl += side * (stop - avg) * open_qty
            fees += stop * open_qty * FEE_OUT
            how = "stop" if not t1_done else ("breakeven after T1" if be_after_t1 else "stop after T1")
            return result(setup, pnl, fees, risk_unit, filled, how, i)
        # 3) targets
        if not t1_done and ((side == 1 and c["h"] >= t1) or (side == -1 and c["l"] <= t1)):
            t1_done = True
            entries = [(p, 0) for p, _ in entries]  # cancel unfilled tranches
            q = open_qty if (t2 is None and not runner) else open_qty * scan.TRANCHE_OUT[0]
            pnl += side * (t1 - avg) * q
            fees += t1 * q * FEE_OUT
            open_qty -= q
            if open_qty <= 1e-12:
                return result(setup, pnl, fees, risk_unit, filled, "T1", i)
            if be_after_t1:
                stop = avg                              # rest of the trade can't lose
        if runner and t1_done and week_end is not None and (start + i) in week_end:
            wclose, w25 = week_end[start + i]
            if w25 is not None and ((side == 1 and wclose < w25) or (side == -1 and wclose > w25)):
                pnl += side * (wclose - avg) * open_qty
                fees += wclose * open_qty * FEE_OUT
                return result(setup, pnl, fees, risk_unit, filled, "runner: weekly close", i)
        if not runner and t1_done and t2 is not None and ((side == 1 and c["h"] >= t2) or (side == -1 and c["l"] <= t2)):
            pnl += side * (t2 - avg) * open_qty
            fees += t2 * open_qty * FEE_OUT
            return result(setup, pnl, fees, risk_unit, filled, "T2", i)
        # 4) time stop
        if first_fill_day is not None and i - first_fill_day >= (HOLD_DAYS if not runner else RUNNER_MAX_DAYS):
            pnl += side * (c["c"] - avg) * open_qty
            fees += c["c"] * open_qty * FEE_OUT
            return result(setup, pnl, fees, risk_unit, filled, "time", i)
    if not filled:
        return None
    avg = sum(p * q for p, q in filled) / sum(q for _, q in filled)
    last = future[-1]["c"] if future else avg
    pnl += side * (last - avg) * open_qty
    return result(setup, pnl, fees, risk_unit, filled, "open at end", len(future) - 1)


def result(setup, pnl, fees, risk_unit, filled, how, days):
    return {
        "r": round((pnl - fees) / risk_unit, 3),
        "fill_pct": round(sum(q for _, q in filled) * 100),
        "exit": how,
        "days": days,
    }


def tradeable(s):
    """The rule book's entry rules: no entry that still needs a confirmed rejection."""
    return not (s.get("needs_confirmation") and s["status"] != "confirmed")


def live(s):
    """The live rules (19 Oct 2026): rating A only, played-out (stale) setups removed."""
    return tradeable(s) and s.get("rating") == "A" and not s.get("stale")


VARIANTS = {
    "Live rules · A only, stale removed":         dict(pick=live, be=True, runner=False),
    "A only, stale kept":                         dict(pick=lambda s: tradeable(s) and s.get("rating") == "A", be=True, runner=False),
    "Previous rules · all ratings (A/B/C)":       dict(pick=tradeable, be=True, runner=False),
    "Live · no daily-flip shorts":                dict(pick=lambda s: live(s) and not (s.get("trend") == "daily flip" and s["side"] == "short"), be=True, runner=False),
    "Live · daily-flip trades only":              dict(pick=lambda s: live(s) and s.get("trend") == "daily flip", be=True, runner=False),
    "Live · no breakeven (rest rides to T2/stop)": dict(pick=live, be=False, runner=False),
    "Live · stop beyond last 2 weeks' wick":      dict(pick=live, be=True, runner=False, alt=True),
    "Live · also drop if price is already past T1": dict(pick=lambda s: live(s) and not s.get("past_t1"), be=True, runner=False),
    "Live · young coins (50–99 weeks) short on weekly 25/50": dict(pick=live, be=True, runner=False, alt="young"),
    "Live · A+ only (area of interest at the zone)": dict(pick=lambda s: live(s) and s.get("grade") == "A+", be=True, runner=False),
}


def collect():
    """Run the scanner over history once; return {coin: (daily, [(t, setups)])}."""
    started = time.time()
    coins = scan.universe()
    out = {}
    for row in coins:
        coin = row["coin"]
        try:
            daily = scan.daily_candles(coin)
        except Exception as e:
            print("skip", coin, e, file=sys.stderr)
            continue
        time.sleep(0.35)
        if len(daily) < WARMUP_DAYS + 30:
            continue
        sig = []
        weekly = scan.weekly_from_daily(daily)
        w25 = scan.ema([w["c"] for w in weekly], 25)
        wk_index = {w["t"]: i for i, w in enumerate(weekly)}
        week_end = {}
        for j, dd in enumerate(daily):
            nxt = daily[j + 1] if j + 1 < len(daily) else None
            dt = datetime.fromtimestamp(dd["t"] / 1000, tz=timezone.utc)
            if nxt is None or datetime.fromtimestamp(nxt["t"] / 1000, tz=timezone.utc).weekday() == 0:
                key = int((dt - __import__("datetime").timedelta(days=dt.weekday())).replace(
                    hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
                wi = wk_index.get(key)
                if wi is not None:
                    week_end[j] = (weekly[wi]["c"], w25[wi])
        for t in range(WARMUP_DAYS, len(daily) - 1, STEP):
            hist = daily[:t + 1]
            r = dict(row, price=hist[-1]["c"], chg24h=0, vol24h=0, funding_8h=0)
            try:
                scan.RECENT_WEEKS_STOP = 0
                _, setups = scan.analyse(r, hist)
                scan.RECENT_WEEKS_STOP = 2                  # variant: stop beyond the last 2 weeks' wick
                _, alt = scan.analyse(r, hist)
                scan.RECENT_WEEKS_STOP = 0
                young = None
                if len(scan.weekly_from_daily(hist)) < 100:  # variant: young coins judged on weekly 25/50
                    scan.YOUNG_TREND = 1
                    _, young = scan.analyse(r, hist)
            except Exception as e:
                print("analyse error", coin, e, file=sys.stderr)
                continue
            finally:
                scan.RECENT_WEEKS_STOP = 0
                scan.YOUNG_TREND = 0
            if young is None:
                young = setups
            if setups or alt or young:
                sig.append((t, setups, alt, young))
        out[coin] = (daily, sig, week_end)
    return out, round(time.time() - started)


def simulate(signals, pick, be, runner=False, alt=False):
    trades = []
    for coin, (daily, sig, week_end) in signals.items():
        busy_until = -1
        for t, setups0, setups1, setups2 in sig:
            setups = setups2 if alt == "young" else setups1 if alt else setups0
            if t <= busy_until:
                continue
            cands = [s for s in setups if pick(s)]
            if not cands:
                continue
            cands.sort(key=lambda s: (s.get("rating", "C"), -s["rr"]))
            s = cands[0]
            res = play(s, daily[t + 1:], be_after_t1=be, runner=runner, week_end=week_end, start=t + 1)
            if res is None:
                busy_until = t + ENTRY_DAYS
                continue
            res.update({
                "coin": coin, "date": datetime.fromtimestamp(daily[t]["t"] / 1000, tz=timezone.utc).date().isoformat(),
                "type": s["type"], "side": s["side"], "rating": s.get("rating"), "status": s["status"],
                "planned_rr": s["rr"], "risk_pct": s["risk_pct"],
                "needs_confirmation": bool(s.get("needs_confirmation")), "trend": s.get("trend"),
                "extra_kinds": s.get("extra_kinds", []),
                "young_trend": bool(s.get("young_trend")),
            })
            trades.append(res)
            busy_until = t + 1 + res["days"]
    return trades


def summarise(trades):
    def stats(ts):
        if not ts:
            return None
        rs = [t["r"] for t in ts]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        eq, peak, dd = 0, 0, 0
        for r in rs:
            eq += r; peak = max(peak, eq); dd = min(dd, eq - peak)
        return {
            "trades": len(ts),
            "win_rate": round(100 * len(wins) / len(ts), 1),
            "avg_r": round(sum(rs) / len(ts), 3),
            "total_r": round(sum(rs), 1),
            "avg_win_r": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss_r": round(sum(losses) / len(losses), 2) if losses else 0,
            "max_drawdown_r": round(dd, 1),
            "stopped_before_t1_pct": round(100 * sum(1 for t in ts if t["exit"] == "stop") / len(ts), 1),
        }

    trades.sort(key=lambda t: t["date"])
    groups = defaultdict(list)
    for t in trades:
        groups["all"].append(t)
        groups[f"side:{t['side']}"].append(t)
        groups[f"type:{t['type']}"].append(t)
        groups[f"rating:{t['rating']}"].append(t)
        groups[f"status:{t['status']}"].append(t)
        if t["rating"] in ("A", "B"):
            groups["rated A/B"].append(t)
    exits = defaultdict(int)
    for t in trades:
        exits[t["exit"]] += 1
    by_year = defaultdict(list)
    for t in trades:
        by_year[t["date"][:4]].append(t)
    return {
        "summary": {k: stats(v) for k, v in sorted(groups.items())},
        "by_year": {k: stats(v) for k, v in sorted(by_year.items())},
        "exits": dict(exits),
    }


def main():
    scan.REQUIRE_HISTORY = True
    signals, secs = collect()
    variants = {}
    for name, v in VARIANTS.items():
        tr = simulate(signals, v["pick"], v["be"], v["runner"], v.get("alt", False))
        sm = summarise(tr)
        variants[name] = {"all": sm["summary"].get("all"), "by_year": sm["by_year"],
                          "long": sm["summary"].get("side:long"), "short": sm["summary"].get("side:short"),
                          "exits": sm["exits"]}
    main_v = VARIANTS["Live rules · A only, stale removed"]
    base = simulate(signals, main_v["pick"], main_v["be"], main_v["runner"])
    # ---- which extra confluence sources add edge (live rules only)
    def mini(ts):
        if not ts:
            return None
        rs = [t["r"] for t in ts]
        return {"trades": len(ts), "win_rate": round(100 * sum(1 for r in rs if r > 0) / len(ts), 1),
                "avg_r": round(sum(rs) / len(ts), 3), "total_r": round(sum(rs), 1)}
    confluence = {}
    for kind, label in (("fib_swing", "fib of the last weekly swing"), ("fib_macro", "fib of ATL–ATH"),
                        ("aoi", "area of interest (3+ touches)"), ("monthly", "monthly EMA")):
        confluence[f"with {label}"] = mini([t for t in base if kind in t["extra_kinds"]])
        confluence[f"without {label}"] = mini([t for t in base if kind not in t["extra_kinds"]])
    for n in (0, 1, 2, 3):
        confluence[f"{n}{'+' if n == 3 else ''} extra sources"] = mini(
            [t for t in base if (len(t["extra_kinds"]) >= 3 if n == 3 else len(t["extra_kinds"]) == n)])
    for k, v in confluence.items():
        print(f"  {k:42} {v}")
    yv = VARIANTS["Live · young coins (50–99 weeks) short on weekly 25/50"]
    ytr = simulate(signals, yv["pick"], yv["be"], yv["runner"], "young")
    young_only = [t for t in ytr if t.get("young_trend")]
    young_stats = {"young-coin shorts added by the rule": mini(young_only),
                   "young-coin shorts by year": {y: mini([t for t in young_only if t["date"][:4] == y])
                                                 for y in sorted({t["date"][:4] for t in young_only})},
                   "coins": sorted({t["coin"] for t in young_only})}
    print("young:", young_stats)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "confluence": confluence,
        "young_trend": young_stats,
        "runtime_s": secs,
        "coins": len(signals),
        "settings": {"step_days": STEP, "entry_days": ENTRY_DAYS, "hold_days": HOLD_DAYS,
                     "min_rr_long": scan.MIN_RR, "min_rr_short": scan.MIN_RR_SHORT,
                     "max_dist": scan.MAX_DIST, "max_stop_pct": scan.MAX_STOP_PCT},
        "caveats": [
            "Universe is today's top 100 Hyperliquid perps, so coins that died are missing (flatters longs).",
            "Daily candles: when a day touches both the stop and a target, the stop is assumed first.",
            "No slippage on stops; fees at base maker/taker rates.",
            "Hyperliquid history starts when each coin was listed there, so early years have fewer coins.",
        ],
        "variants": variants,
        **summarise(base),
        "trades": sorted(base, key=lambda t: t["date"])[-400:],
    }
    os.makedirs(scan.DATA_DIR, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    for name, v in variants.items():
        a = v["all"] or {}
        print(f"{name:34} {a.get('trades', 0):5} trades  win {a.get('win_rate')}%  avg {a.get('avg_r')}R  total {a.get('total_r')}R")


if __name__ == "__main__":
    main()
