"""Late-game value benchmark from the bot's own ranked games.

Live finding (Oct 17, 600 ranked games): after turn 75 the bot wins only
69.8% of the games in which the net first rates its position above 0.5, and
deep re-search of the lost ones mostly agrees with the moves played. The
error is in the value, not the move. This set measures it: every bot decision
at turn >= TURN in ranked games that reached that turn, labelled with the
game's result.

  build: python -m catanatron.colonist.late_value_set build [--last 150]
  eval:  python -m catanatron.colonist.late_value_set eval NET [NET ...]

eval prints, per net: positions, bias (mean v - z), MSE, and the same on
the "confident" positions (v > 0.5), where the observed win rate should match
the mean value. Captures stay private; output is aggregate only.
"""

import argparse
import json
import math
from multiprocessing import Pool

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.net import Net
from catanatron.colonist.replay import game_winner, replay
from catanatron.models.player import Color

ACCOUNTS = ("bot3", "bot4", "bot5", "bot7")
SET = "data/orchestrator/live-loss/late_value_set.jsonl"
TURN = 75
_nets = None


class _Collector:
    def __init__(self, me, nets):
        self.me, self.nets = me, nets
        self.values = []  # one list of per-net values per position

    def before(self, game):
        pass

    def step(self, game, decision):
        if decision.color != self.me or game.state.num_turns < TURN:
            return
        self.values.append([net.evaluate(game, self.me)[1] for net in self.nets])

    def after(self, game):
        pass


def _game(path, nets):
    conv = convert(load_capture(path))
    if conv.unsupported or conv.ended not in ("win", "resignation"):
        return None
    rec = conv.record
    you = [c for c, p in rec["players"].items() if p["you"]]
    if len(you) != 1 or len(rec["seats"]) != 2:
        return None
    obs = _Collector(Color[you[0]], nets)
    rep = replay(rec, observer=obs)
    if not rep.ok or not obs.values:
        return None
    return {"capture": path, "won": game_winner(rec, rep) == you[0], "values": obs.values}


def _init(net_paths):
    global _nets
    _nets = [Net.load(p) for p in net_paths]


def _work(path):
    try:
        return _game(path, _nets)
    except Exception:  # noqa: BLE001
        return None


def build(last, workers, out=SET):
    paths = []
    for acc in ACCOUNTS:
        rows = [json.loads(x) for x in open(f"data/{acc}/games.jsonl")]
        rows = [r for r in rows if r.get("queue") == "ranked"][-last:]
        paths += [f"captures/{acc}/{r['capture']}" for r in rows]
    with Pool(workers, _init, ([],)) as pool:
        res = [r for r in pool.imap_unordered(_work, paths, chunksize=4) if r]
    res.sort(key=lambda r: r["capture"])
    with open(out, "w") as f:
        for r in res:
            f.write(json.dumps({"capture": r["capture"], "won": r["won"]}) + "\n")
    print(f"captures {len(paths)} games reaching turn {TURN}: {len(res)} -> {out}")


def _stats(pairs):
    n = len(pairs)
    if not n:
        return "n=0"
    bias = sum(v - z for v, z in pairs) / n
    mse = sum((v - z) ** 2 for v, z in pairs) / n
    return f"n={n} bias {bias:+.3f} mse {mse:.3f}"


def _confident(pairs):
    hi = [(v, z) for v, z in pairs if v > 0.5]
    if not hi:
        return "n=0"
    n = len(hi)
    mean_v = sum(v for v, _ in hi) / n
    p = sum(z > 0 for _, z in hi) / n
    h = 1.96 * math.sqrt(p * (1 - p) / n)
    return (
        f"n={n} mean v {mean_v:+.3f} -> implied win {50 * (mean_v + 1):.1f}%,"
        f" actual {100 * p:.1f}% (+-{100 * h:.1f}, positions correlated within games)"
    )


def evaluate(net_paths, workers, src=SET):
    rows = [json.loads(x) for x in open(src)]
    with Pool(workers, _init, (net_paths,)) as pool:
        res = [r for r in pool.imap_unordered(_work, [r["capture"] for r in rows]) if r]
    print(f"games {len(res)} of {len(rows)} ({sum(r['won'] for r in res)} won)")
    for i, path in enumerate(net_paths):
        pairs = [(v[i], 1.0 if r["won"] else -1.0) for r in res for v in r["values"]]
        print(f"{path}: all {_stats(pairs)} | v>0.5 {_confident(pairs)}")
        # per-game: first position, so each game counts once
        first = [(r["values"][0][i], 1.0 if r["won"] else -1.0) for r in res]
        print(f"  first position per game: {_stats(first)} | v>0.5 {_confident(first)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--last", type=int, default=150)
    b.add_argument("--workers", type=int, default=6)
    e = sub.add_parser("eval")
    e.add_argument("nets", nargs="+")
    e.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.last, a.workers)
    else:
        evaluate(a.nets, a.workers)


if __name__ == "__main__":
    main()
