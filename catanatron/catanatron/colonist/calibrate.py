"""How well a net's value predicts real games (live calibration).

    python -m catanatron.colonist.calibrate runs/sp1/best.npz data/ranked/bot_value

Reads value shards exported from live captures (human_data.py --value-only, or
the ranked runner's bot_value folder): positions from the bot's side with the
real result. Reports the value's mean squared error against the result (+1 a
win, -1 a loss) and how often its sign picks the winner, overall and in the
opening, middle and late game. A net that is no better here than an older one
has learned something about self-play that doesn't carry over to people.

--net-sha256 limits it to games a given net played (shards record the hash of
the net from the capture); by default every game counts, whichever net played.
"""

import argparse
import glob
import json
import os
import sys

import numpy as np

PHASES = (("opening", 0, 20), ("middle", 20, 60), ("late", 60, 10**6))


def load_value_shards(folders, net_sha256=None):
    """[(static (games, S), dynamic (n, D), game index (n,), z (n,), turn (n,))]."""
    parts = []
    for folder in folders:
        for path in sorted(glob.glob(os.path.join(folder, "*.npz"))):
            with np.load(path) as d:
                if net_sha256 and str(d["net"][0] if "net" in d else "") != net_sha256:
                    continue
                parts.append(
                    (
                        d["static"].astype(np.float32),
                        d["dynamic"].astype(np.float32),
                        d["game"].astype(np.int64),
                        d["z"].astype(np.float32),
                        d["turn"].astype(np.int64),
                    )
                )
    return parts


def calibrate(net, parts):
    """{'positions', 'games', 'mse', 'accuracy', per phase ...} for `net`."""
    values, results, turns = [], [], []
    for static, dynamic, game, z, turn in parts:
        _, v = net.forward_rows([static[g] for g in game], dynamic)
        values.append(np.asarray(v, np.float64))
        results.append(z)
        turns.append(turn)
    if not values:
        return {"positions": 0, "games": 0}
    v, z, t = (np.concatenate(x) for x in (values, results, turns))
    out = {"positions": int(len(v)), "games": len(parts), **_scores(v, z)}
    for name, start, end in PHASES:
        rows = (t >= start) & (t < end)
        if rows.any():
            out[name] = _scores(v[rows], z[rows])
    return out


def _scores(v, z):
    return {
        "mse": round(float(np.mean((v - z) ** 2)), 4),
        "accuracy": round(float(np.mean(np.sign(v) == np.sign(z))), 4),
        "n": int(len(v)),
    }


def describe(result):
    if not result.get("positions"):
        return "no live positions"
    line = (
        f"live value MSE {result['mse']:.3f}, picks the winner "
        f"{result['accuracy']:.1%} ({result['positions']} positions, "
        f"{result['games']} games)"
    )
    phases = [
        f"{name} {result[name]['mse']:.3f}" for name, _, _ in PHASES if name in result
    ]
    return line + ("; MSE by phase: " + ", ".join(phases) if phases else "")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("net", help="net.npz")
    parser.add_argument("folders", nargs="+", help="value shard folders")
    parser.add_argument("--net-sha256", help="only games this net played")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    from catanatron.colonist.net import Net

    result = calibrate(
        Net.load(args.net), load_value_shards(args.folders, args.net_sha256)
    )
    print(json.dumps(result) if args.json else describe(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
