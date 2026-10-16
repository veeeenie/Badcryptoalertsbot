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
WARMUP_DAYS = 400                               # history needed before the first step
FEE_IN, FEE_OUT = 0.00015, 0.00045
OUT_PATH = os.path.join(scan.DATA_DIR, "backtest.json")


def play(setup, future):
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
            return result(setup, pnl, fees, risk_unit, filled, "stop" if not t1_done else "stop after T1", i)
        # 3) targets
        if not t1_done and ((side == 1 and c["h"] >= t1) or (side == -1 and c["l"] <= t1)):
            t1_done = True
            entries = [(p, 0) for p, _ in entries]  # cancel unfilled tranches
            q = open_qty if t2 is None else open_qty * scan.TRANCHE_OUT[0]
            pnl += side * (t1 - avg) * q
            fees += t1 * q * FEE_OUT
            open_qty -= q
            if open_qty <= 1e-12:
                return result(setup, pnl, fees, risk_unit, filled, "T1", i)
        if t1_done and t2 is not None and ((side == 1 and c["h"] >= t2) or (side == -1 and c["l"] <= t2)):
            pnl += side * (t2 - avg) * open_qty
            fees += t2 * open_qty * FEE_OUT
            return result(setup, pnl, fees, risk_unit, filled, "T2", i)
        # 4) time stop
        if first_fill_day is not None and i - first_fill_day >= HOLD_DAYS:
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


def run():
    started = time.time()
    coins = scan.universe()
    trades = []
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
        busy_until = -1
        for t in range(WARMUP_DAYS, len(daily) - 1, STEP):
            if t <= busy_until:
                continue
            hist = daily[:t + 1]
            px = hist[-1]["c"]
            r = dict(row, price=px, chg24h=0, vol24h=0, funding_8h=0)
            try:
                _, setups = scan.analyse(r, hist)
            except Exception as e:
                print("analyse error", coin, e, file=sys.stderr)
                continue
            if not setups:
                continue
            # same choice the live scanner makes: best setup per coin
            setups.sort(key=lambda s: (s.get("rating", "C"), -s["rr"]))
            s = setups[0]
            res = play(s, daily[t + 1:])
            if res is None:
                busy_until = t + ENTRY_DAYS
                continue
            res.update({
                "coin": coin, "date": datetime.fromtimestamp(hist[-1]["t"] / 1000, tz=timezone.utc).date().isoformat(),
                "type": s["type"], "side": s["side"], "rating": s.get("rating"), "status": s["status"],
                "planned_rr": s["rr"], "risk_pct": s["risk_pct"],
                "needs_confirmation": bool(s.get("needs_confirmation")),
            })
            trades.append(res)
            busy_until = t + 1 + res["days"]
    return trades, round(time.time() - started)


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
    trades, secs = run()
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runtime_s": secs,
        "settings": {"step_days": STEP, "entry_days": ENTRY_DAYS, "hold_days": HOLD_DAYS,
                     "min_rr_long": scan.MIN_RR, "min_rr_short": scan.MIN_RR_SHORT,
                     "max_dist": scan.MAX_DIST, "max_stop_pct": scan.MAX_STOP_PCT},
        "caveats": [
            "Universe is today's top 100 Hyperliquid perps, so coins that died are missing (flatters longs).",
            "Daily candles: when a day touches both the stop and a target, the stop is assumed first.",
            "No slippage on stops; fees at base maker/taker rates.",
            "Hyperliquid history starts when each coin was listed there, so early years have fewer coins.",
        ],
        **summarise(trades),
        "trades": trades[-400:],
    }
    os.makedirs(scan.DATA_DIR, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    s = out["summary"].get("all") or {}
    print(f"backtest: {s.get('trades', 0)} trades, win {s.get('win_rate')}%, avg {s.get('avg_r')}R, "
          f"total {s.get('total_r')}R, {secs}s")


if __name__ == "__main__":
    main()
