"""E84 step 0: is the net too sure on people's late positions even when it
plays them out against itself? The net's stated win chance at each E71 start
(bot's chair) against three outcomes from the same start: the live game,
the net vs itself (E71 arm r73-vs-r73) and the net vs the people model.

    .venv/bin/python data/orchestrator/scratch/research/e84_cal.py [NET] [STARTS]
"""

import json
import random
import sys

from catanatron.colonist import encode as E
from catanatron.colonist.net import Net
from catanatron.colonist.starts import start_game, unpack
from catanatron.models.player import Color

D = "data/orchestrator/e71"
SEED = 7_100_000_000


def boot(xs, n=1000):
    rng = random.Random(1)
    m = lambda rs: 100 * sum(rs) / len(rs)  # noqa: E731
    bs = sorted(m(rng.choices(xs, k=len(xs))) for _ in range(n))
    return f"{m(xs):+5.1f} [{bs[25]:+.1f}, {bs[975]:+.1f}]"


def main():
    net_path = sys.argv[1] if len(sys.argv) > 1 else "runs/g1/nets/round_073.npz"
    name = sys.argv[2] if len(sys.argv) > 2 else "starts"
    net = Net.load(net_path)
    starts = [json.loads(line) for line in open(f"{D}/{name}.jsonl")]
    arms = {}
    for arm in ("r73-vs-r73", "r73-vs-people"):
        try:
            arms[arm] = [json.loads(line)["score"] for line in open(f"{D}/{arm}.jsonl")]
        except OSError:
            pass
    rows = []
    for i, s in enumerate(starts):
        try:
            game = start_game(unpack(s["start"]), SEED + i)
        except ValueError:
            continue
        static, dynamic = E.encode(game, Color[s["bot"]])
        _, v = net.forward(static, dynamic[None, :])
        p = (float(v.reshape(-1)[0]) + 1) / 2
        out = {"live": float(s["bot_won"])}
        out.update({a: sc[i] for a, sc in arms.items() if i < len(sc)})
        out["mover"] = game.state.current_color() == Color[s["bot"]]
        rows.append((p, s["opp_mmr"], out))
    print(f"{net_path}: {len(rows)} of {len(starts)} starts")
    bands = {
        "all": lambda m: True,
        "2100+": lambda m: m is not None and m >= 2100,
        "<2100": lambda m: m is not None and m < 2100,
    }
    for band, keep in bands.items():
        sel = [r for r in rows if keep(r[1])]
        bands_out(band, sel, arms)
        for mv in (True, False):
            bands_out(
                f"  bot {'to move' if mv else 'waits'}",
                [r for r in sel if r[2]["mover"] == mv],
                arms,
            )
    return by_bucket(rows, arms)


def bands_out(band, sel, arms):
    if sel:
        st = 100 * sum(r[0] for r in sel) / len(sel)
        line = f"  {band:6s} n {len(sel):4d} stated {st:5.1f}"
        for k in ["live"] + list(arms):
            xs = [r[0] - r[2][k] for r in sel if k in r[2]]
            line += f" | {k} gap {boot(xs)}"
        print(line)


def by_bucket(rows, arms):
    print("  by stated chance (all): n, stated, live, net vs itself, vs people")
    for lo in (0, 0.2, 0.4, 0.6, 0.8):
        sel = [r for r in rows if lo <= r[0] < lo + 0.2 or (lo == 0.8 and r[0] == 1)]
        if not sel:
            continue
        f = (
            lambda k: 100
            * sum(r[2][k] for r in sel if k in r[2])
            / max(1, sum(1 for r in sel if k in r[2]))
        )  # noqa: E731
        st = 100 * sum(r[0] for r in sel) / len(sel)
        print(
            f"    {lo:.1f}-{lo + 0.2:.1f}: n {len(sel):4d} {st:5.1f} {f('live'):5.1f} "
            + " ".join(f"{f(a):5.1f}" for a in arms)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
