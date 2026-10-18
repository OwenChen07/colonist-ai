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
the mean value, then by what the person holds (no dev card / dev cards /
largest army + dev cards). Captures stay private; output is aggregate only.
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
from catanatron.state import DEVELOPMENT_CARDS

ACCOUNTS = ("bot3", "bot4", "bot5", "bot7")
SET = "data/orchestrator/live-loss/late_value_set.jsonl"
TURN = 75
_nets = None


def _slice(state, me):
    """'army_dev' if the opponent holds largest army and 1+ unplayed dev cards,
    'dev' if only the cards, else ''. Live finding (Oct 17): these are where
    the late overconfidence sits (bias +0.285 with army+dev, about 0 with no
    cards)."""
    them = next(c for c in state.colors if c != me)
    i = state.color_to_index[them]
    p = state.player_state
    if not any(p[f"P{i}_{c}_IN_HAND"] for c in DEVELOPMENT_CARDS):
        return ""
    return "army_dev" if p[f"P{i}_HAS_ARMY"] else "dev"


class _Collector:
    def __init__(self, me, nets):
        self.me, self.nets = me, nets
        self.values = []  # one list of per-net values per position
        self.slices = []

    def before(self, game):
        pass

    def step(self, game, decision):
        if decision.color != self.me or game.state.num_turns < TURN:
            return
        self.values.append([net.evaluate(game, self.me)[1] for net in self.nets])
        self.slices.append(_slice(game.state, self.me))

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
    return {
        "capture": path,
        "won": game_winner(rec, rep) == you[0],
        "values": obs.values,
        "slices": obs.slices,
    }


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


def _half_width(xs):
    """95% half-width of the mean of xs, a list of (value, game), with games as
    clusters: positions in one game share its result, so the naive binomial
    interval is several times too narrow."""
    n = len(xs)
    mean = sum(x for x, _ in xs) / n
    per_game = {}
    for x, g in xs:
        per_game[g] = per_game.get(g, 0.0) + x - mean
    k = len(per_game)
    if k < 2:
        return float("nan")
    var = sum(s * s for s in per_game.values()) / n**2 * k / (k - 1)
    return 1.96 * math.sqrt(var)


def _stats(pairs):
    n = len(pairs)
    if not n:
        return "n=0"
    bias = sum(v - z for v, z, _ in pairs) / n
    mse = sum((v - z) ** 2 for v, z, _ in pairs) / n
    h = _half_width([(v - z, g) for v, z, g in pairs])
    return f"n={n} bias {bias:+.3f} (+-{h:.3f}) mse {mse:.3f}"


def _confident(pairs):
    """pairs: (value, result, game). The gap is implied minus actual win rate
    on v > 0.5, with a game-clustered interval."""
    hi = [(v, z, g) for v, z, g in pairs if v > 0.5]
    if not hi:
        return "n=0"
    n = len(hi)
    mean_v = sum(v for v, _, _ in hi) / n
    p = sum(z > 0 for _, z, _ in hi) / n
    h = _half_width([(float(z > 0), g) for _, z, g in hi])
    gap = _half_width([((v + 1) / 2 - (z > 0), g) for v, z, g in hi])
    games = len({g for _, _, g in hi})
    return (
        f"n={n} ({games} games) mean v {mean_v:+.3f} -> implied win"
        f" {50 * (mean_v + 1):.1f}%, actual {100 * p:.1f}% (+-{100 * h:.1f}),"
        f" gap {50 * (mean_v + 1) - 100 * p:.1f} pp (+-{100 * gap:.1f})"
    )


def evaluate(net_paths, workers, src=SET):
    rows = [json.loads(x) for x in open(src)]
    with Pool(workers, _init, (net_paths,)) as pool:
        res = [r for r in pool.imap_unordered(_work, [r["capture"] for r in rows]) if r]
    print(f"games {len(res)} of {len(rows)} ({sum(r['won'] for r in res)} won)")
    res.sort(key=lambda r: r["capture"])
    for i, path in enumerate(net_paths):
        pairs = [
            (v[i], 1.0 if r["won"] else -1.0, g)
            for g, r in enumerate(res)
            for v in r["values"]
        ]
        print(f"{path}: all {_stats(pairs)} | v>0.5 {_confident(pairs)}")
        # per-game: first position, so each game counts once
        first = [
            (r["values"][0][i], 1.0 if r["won"] else -1.0, g) for g, r in enumerate(res)
        ]
        print(f"  first position per game: {_stats(first)} | v>0.5 {_confident(first)}")
        for name, keep in (
            ("person holds no dev card", ("",)),
            ("person holds dev cards", ("dev", "army_dev")),
            ("person holds army + dev cards", ("army_dev",)),
        ):
            sub = [
                (v[i], 1.0 if r["won"] else -1.0, g)
                for g, r in enumerate(res)
                for v, s in zip(r["values"], r["slices"])
                if s in keep
            ]
            print(f"  {name}: {_stats(sub)} | v>0.5 {_confident(sub)}")
        if i:
            # paired on the same positions: much tighter than two intervals
            for name, keep in (("all", None), ("army + dev cards", ("army_dev",))):
                d = [
                    (v[i] - v[0], g)
                    for g, r in enumerate(res)
                    for v, s in zip(r["values"], r["slices"])
                    if keep is None or s in keep
                ]
                if not d:
                    continue
                mean = sum(x for x, _ in d) / len(d)
                print(
                    f"  bias minus {net_paths[0]}, {name}:"
                    f" {mean:+.4f} (+-{_half_width(d):.4f})"
                )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--last", type=int, default=150)
    b.add_argument("--workers", type=int, default=6)
    b.add_argument("--set", default=SET, help="where to write the set")
    e = sub.add_parser("eval")
    e.add_argument("nets", nargs="+")
    e.add_argument("--workers", type=int, default=6)
    e.add_argument("--set", default=SET, help="which set to score")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.last, a.workers, a.set)
    else:
        evaluate(a.nets, a.workers, a.set)


if __name__ == "__main__":
    main()
