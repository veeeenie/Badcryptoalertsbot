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


# ---- engine v2 (bench/engine.py): one trend score + one level map
import engine as E  # noqa: E402


def v2_factory(long_min=E.SCORE_MIN, short_max=-E.SCORE_MIN, tag="v2"):
    def strat(ctx):
        if len(ctx.weeks) < 27:
            return None
        sc, _ = E.trend_score(ctx.weeks, ctx.price)
        if sc is None or not (sc >= long_min or sc <= short_max):
            return None
        p = E.plan(ctx.weeks, ctx.daily, ctx.price, ctx.day, W.ladder)
        if p and ((p["side"] == "long" and sc >= long_min) or (p["side"] == "short" and sc <= short_max)):
            p["tag"] = tag
            return p
        return None
    return strat


v2 = v2_factory()


# ---- variants for the bench (market regime from BTC, exit handling)
_btc = {}


def btc_score(ctx, coins):
    """BTC's trend score at the start of ctx.day (cached per week)."""
    import bench as B
    wk_key = ctx.weeks[-1]["t"] if ctx.weeks else 0
    if wk_key not in _btc:
        bw = [w for w in B.weekly(coins["BTC"]["candles"]) if w["t"] + B.WEEK <= ctx.day]
        sc, _ = E.trend_score(bw, bw[-1]["c"]) if len(bw) > 30 else (None, None)
        _btc[wk_key] = sc
    return _btc[wk_key]


def v2x(coins, long_min=0.3, short_max=-0.3, regime=False, tp1_1r=False, be=False, tag="v2x"):
    base = v2_factory(long_min, short_max, tag)

    def strat(ctx):
        p = base(ctx)
        if not p:
            return None
        if regime:
            b = btc_score(ctx, coins)
            if b is None or (p["side"] == "long" and b <= 0) or (p["side"] == "short" and b >= 0):
                return None
        if tp1_1r:
            avg = sum(e * w for e, w in zip(p["entries"], p["weights"]))
            r = abs(avg - p["stop"]); sg = 1 if p["side"] == "long" else -1
            one = avg + sg * r
            later = [t for t in p["tps"] if sg * (t - one) > 0]
            p["tps"] = [one, (later[0] if later else avg + sg * 3 * r)]
        if be:
            p["be_after_tp1"] = True
        return p
    return strat
