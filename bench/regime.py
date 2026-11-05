"""Market regime for the bench: an equal-weight index of the point-in-time top 100, and BTC,
each compared with its own N-week simple average (above = up, below = down)."""
import bench as B


def alt_index(coins, uni):
    W = {n: {w["t"]: w for w in B.weekly(v["candles"])} for n, v in coins.items()}
    weeks = sorted({t for m in W.values() for t in m})
    out, lvl = [], 100.0
    for i in range(1, len(weeks)):
        t, tp = weeks[i], weeks[i - 1]
        mem = [n for n in uni.get(t, ()) if t in W[n] and tp in W[n] and W[n][tp]["c"] > 0]
        if len(mem) < 15:
            continue
        r = sum(W[n][t]["c"] / W[n][tp]["c"] - 1 for n in mem) / len(mem)
        lvl *= 1 + max(-0.9, r)
        out.append({"t": t, "c": lvl})
    return out


def regime_by_week(series, n):
    """{week_start: +1/-1} for weeks whose previous closed week is above/below its n-week average."""
    reg = {}
    for i in range(n, len(series) - 1):
        sma = sum(x["c"] for x in series[i - n + 1:i + 1]) / n
        reg[series[i]["t"] + B.WEEK] = 1 if series[i]["c"] > sma else -1
    return reg


def lookup(reg, day):
    wk = day - (day - 345_600_000) % B.WEEK       # Monday 00:00 UTC of that day's week
    return reg.get(wk)
