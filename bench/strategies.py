"""
Strategies for the bench. The baselines call the live code in scanner/watchlist.py unchanged, so the
bench tests exactly what the dashboard shows.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scanner"))
import watchlist as W  # noqa: E402

# zones, swings and ATR only change when a new weekly candle closes: cache them per coin and week
_cache = {}
_cur = {"coin": None}


def _memo(fn, name):
    def wrapped(c, *a, **k):
        key = (name, _cur["coin"], len(c), c[-1]["t"] if c else 0, a, tuple(sorted(k.items())))
        if key not in _cache:
            _cache[key] = fn(c, *a, **k)
        return _cache[key]
    return wrapped


W.zones = _memo(W.zones, "zones")
W.swings = _memo(W.swings, "swings")
W.atr_series = _memo(W.atr_series, "atr")


def _to_plan(p, tag):
    if not p or p.get("status") == "wait":
        return None
    ws = [w / 100 for w in p["entry_split"]]
    s = sum(ws)
    return {"side": p["side"], "entries": list(p["entries"]), "weights": [w / s for w in ws], "stop": p["stop"],
            "tps": list(p["tps"]), "tag": tag, "rr": p.get("rr")}


def live_main(ctx):
    """Today's main trade ideas (weekly trend + supply/demand zone plan)."""
    _cur["coin"] = ctx.coin
    closed = ctx.weeks
    if len(closed) < 25:
        return None
    trend, d = W.weekly_trend(closed, ctx.price)
    if trend is None:
        return None
    p, _ = W.plan(closed, ctx.price, trend, d, ctx.price)
    return _to_plan(p, "main")


def live_testing(ctx):
    """Today's Testing · directional ideas (only when there is no main idea on that side)."""
    _cur["coin"] = ctx.coin
    closed = ctx.weeks
    if len(closed) < 25:
        return None
    trend, d = W.weekly_trend(closed, ctx.price)
    side = W.strong_side(closed, ctx.price, trend, d)
    if not side:
        return None
    if trend == side:
        pm, _ = W.plan(closed, ctx.price, trend, d, ctx.price)
        if pm is not None:
            return None              # shown as a main idea instead
    hs, ls = W.swings(closed)
    tg = [h for _, h in hs] if side == "up" else [l for _, l in ls]
    p, _ = W.plan(closed, ctx.price, side, d, ctx.price, targets=tg)
    return _to_plan(p, "testing")


def live_both(ctx):
    return live_main(ctx) or live_testing(ctx)
