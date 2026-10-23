"""E78 pre-live check on robber_set.jsonl: run the search with and without
robber_policy.robber_root_actions (same seed) and compare.

    run NET SIMS [WORKERS]  -> data/orchestrator/live-loss/e78_check.jsonl
    table                   reprints
"""

import json
import os
import random
import sys
import zlib
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.models.enums import ActionType  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.colonist.robber_policy import robber_root_actions  # noqa: E402
from robber_set import OUT as SET, Obs, players  # noqa: E402

OUT = "data/orchestrator/live-loss/e78_check.jsonl"
_net = None


def root_q(root):
    q = {}
    for a, n, w in zip(root.actions, root.N, root.W):
        if a.action_type == ActionType.MOVE_ROBBER and n > 0:
            q[tuple(a.value[0])] = (w / n, int(n))
    return q


def one(args):
    global _net
    capture, rows, net_path, sims = args
    from catanatron.colonist.mcts import Search
    from catanatron.colonist.net import Net

    if _net is None:
        _net = Net.load(net_path)
    out = []
    for r in rows:
        try:
            record, you, them = players(capture)
            obs = Obs(you, them, target=r["k"])
            replay(record, observer=obs)
        except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
            continue
        game = obs.game
        if game is None:
            continue
        legal = [
            a for a in game.playable_actions if a.action_type == ActionType.MOVE_ROBBER
        ]
        kept = robber_root_actions(game, legal)
        kept_hex = {tuple(a.value[0]) for a in kept}
        seed = 7717 + 1009 * r["k"] + zlib.crc32(capture.encode()) % 1000
        res = {}
        for arm, filt in (
            ("off", None),
            ("on", lambda g, acts: robber_root_actions(g, acts)),
        ):
            search = Search(
                _net,
                simulations=sims,
                rng=random.Random(seed),
                noise=False,
                root_action_filter=filt,
            )
            action, root = search.run(game.copy())
            res[arm] = (tuple(action.value[0]), root_q(root))
        good = {tuple(c) for c in r["good"]}
        bad = {tuple(c) for c in r["bad"]}

        def cls(c):
            return "good" if c in good else "bad" if c in bad else "other"

        p_off, q_off = res["off"]
        p_on, q_on = res["on"]
        out.append(
            {
                "capture": capture,
                "k": r["k"],
                "won": r["won"],
                "live": cls(tuple(r["chosen"])),
                "n_legal": len({tuple(a.value[0]) for a in legal}),
                "n_kept": len(kept_hex),
                "bad_left": len(bad & kept_hex),
                "live_dropped": tuple(r["chosen"]) not in kept_hex,
                "off": cls(p_off),
                "on": cls(p_on),
                "changed": p_off != p_on,
                "q_off_pick": q_off[p_off][0] if p_off in q_off else None,
                "q_on_pick_in_off": q_off[p_on][0] if p_on in q_off else None,
                "q_on_pick": q_on[p_on][0] if p_on in q_on else None,
            }
        )
    return out


def run(net_path, sims, workers):
    by_game = {}
    for line in open(SET):
        r = json.loads(line)
        by_game.setdefault(r["capture"], []).append(r)
    todo = [(g, rows, net_path, sims) for g, rows in sorted(by_game.items())]
    with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
        res = [x for part in pool.map(one, todo, chunksize=1) for x in part]
    with open(OUT, "w") as f:
        for x in res:
            f.write(json.dumps(x) + "\n")
    print(f"wrote {len(res)} rows to {OUT}")
    table()


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / d
    return (100 * (c - h), 100 * (c + h))


def table():
    rows = [json.loads(line) for line in open(OUT)]
    n = len(rows)
    print(
        f"positions {n}; legal hexes mean {np.mean([x['n_legal'] for x in rows]):.1f},"
        f" kept {np.mean([x['n_kept'] for x in rows]):.1f};"
        f" bad hexes left after filter: {sum(x['bad_left'] for x in rows)}"
    )
    print(
        f"live move dropped by the filter: {sum(x['live_dropped'] for x in rows)}/{n}"
        f" (live: {dict((k, sum(x['live'] == k for x in rows)) for k in ('good', 'bad', 'other'))})"
    )
    for arm in ("off", "on"):
        c = {k: sum(x[arm] == k for x in rows) for k in ("good", "bad", "other")}
        lo, hi = wilson(c["good"], n)
        print(
            f"search {arm:3}: {c}  good {100 * c['good'] / n:.0f}% [{lo:.0f},{hi:.0f}]"
        )
    ch = [x for x in rows if x["changed"]]
    print(f"pick changed by filter: {len(ch)}/{n}")
    d = [
        x["q_off_pick"] - x["q_on_pick_in_off"]
        for x in ch
        if x["q_off_pick"] is not None and x["q_on_pick_in_off"] is not None
    ]
    if d:
        print(
            f"  unfiltered search's Q(its pick) - Q(filtered pick), {len(d)} with both visited:"
            f" median {np.median(d):+.3f}, mean {np.mean(d):+.3f}, p90 {np.percentile(d, 90):+.3f},"
            f" >0.05: {sum(v > 0.05 for v in d)}, >0.10: {sum(v > 0.10 for v in d)}"
        )
    unv = sum(1 for x in ch if x["q_on_pick_in_off"] is None)
    print(f"  filtered pick unvisited by the unfiltered search: {unv}")
    v = [
        x["q_off_pick"] - x["q_on_pick"]
        for x in ch
        if x["q_off_pick"] is not None and x["q_on_pick"] is not None
    ]
    if v:
        print(
            f"  root Q of pick, off minus on (each in own search): median {np.median(v):+.3f}, mean {np.mean(v):+.3f}"
        )


def main():
    if sys.argv[1] == "run":
        run(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 3)
    else:
        table()
    return 0


if __name__ == "__main__":
    sys.exit(main())
