"""Run the bench: python bench/run.py [strategy ...]  (default: live_main live_testing)."""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench as B  # noqa: E402
import strategies as S  # noqa: E402

START = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def main(names):
    coins, gen = B.load()
    end = max(v["t"][-1] for v in coins.values()) + B.DAY
    print(f"data {gen}: {len(coins)} coins, {B.iso(START)} to {B.iso(end)}")
    out = {"data": gen, "start": B.iso(START), "end": B.iso(end), "runs": {}}
    for n in names:
        t0 = time.time()
        trades = B.simulate(coins, getattr(S, n), START, end, progress=True)
        rep = B.report(trades)
        out["runs"][n] = {"report": rep, "trades": trades}
        print(f"\n## {n}  ({time.time() - t0:.0f}s)\n" + B.table(rep) + "\n" + json.dumps(rep["diagnostics"]))
    os.makedirs(os.path.join(B.HERE, "results"), exist_ok=True)
    path = os.path.join(B.HERE, "results", "latest.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print("saved", path)


if __name__ == "__main__":
    main(sys.argv[1:] or ["live_main", "live_testing"])
