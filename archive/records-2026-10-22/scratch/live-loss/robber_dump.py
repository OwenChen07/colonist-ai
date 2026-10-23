"""Why does the search prefer the self-block? For each robber_set.jsonl
position: the search's pick and Q (same seeds as robber_set.py score), plus
context: VP both sides, knights held, robber from a knight or a 7, opponent
hand size, the live hex and the best clean hex (resource, pips, the opponent's
share of that resource that the hex holds, steal available).

    run NET SIMS [WORKERS]  -> data/orchestrator/live-loss/robber_dump.jsonl
    table                   reprints the comparison from the jsonl
"""

import json
import os
import random
import sys
import zlib
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.models.enums import ActionType, RESOURCES  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from robber_set import OUT as SET, Obs, players, classify  # noqa: E402
from e75_robber import col  # noqa: E402
from e75_aim import per_hex  # noqa: E402

OUT = "data/orchestrator/live-loss/robber_dump.jsonl"
_net = None


def hexinfo(c, hexes, own, me, opp):
    if c not in hexes:
        return {"res": None, "o": 0, "m": 0, "o_share": 0.0, "m_res": 0}
    r = hexes[c][0]
    o, m = own.get(opp, {}), own.get(me, {})
    o_res = sum(v for h, v in o.items() if hexes[h][0] == r)
    m_res = sum(v for h, v in m.items() if hexes[h][0] == r)
    ov = o.get(c, 0)
    return {
        "res": r,
        "o": ov,
        "m": m.get(c, 0),
        "o_share": ov / o_res if o_res else 0.0,  # share of opp's income in r
        "o_res": o_res,
        "m_res": m_res,
    }


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
        st = game.state
        me_c = next(c for c in st.colors if col(c) == you)
        op_c = next(c for c in st.colors if col(c) == them)
        pk = {c: f"P{st.color_to_index[c]}" for c in (me_c, op_c)}
        ps = st.player_state
        seed = 7717 + 1009 * r["k"] + zlib.crc32(capture.encode()) % 1000
        search = Search(_net, simulations=sims, rng=random.Random(seed), noise=False)
        action, root = search.run(game)
        q, n_vis = {}, {}
        for a, n, w in zip(root.actions, root.N, root.W):
            if a.action_type == ActionType.MOVE_ROBBER and n > 0:
                q[tuple(a.value[0])] = w / n
                n_vis[tuple(a.value[0])] = int(n)
        hexes, own = per_hex(game.state.board)
        sc, good, bad = classify(game, you, them)
        pick = tuple(action.value[0])
        g_best = max(good, key=lambda c: q.get(c, -9.0))
        b_best = max(bad, key=lambda c: q.get(c, -9.0))
        out.append(
            {
                "capture": capture,
                "k": r["k"],
                "turn": r["turn"],
                "won": r["won"],
                "pick": (
                    "good"
                    if list(pick) in r["good"]
                    else "bad" if list(pick) in r["bad"] else "other"
                ),
                "qg": q.get(g_best),
                "qb": q.get(b_best),
                "ng": n_vis.get(g_best, 0),
                "nb": n_vis.get(b_best, 0),
                "vp_me": ps[f"{pk[me_c]}_ACTUAL_VICTORY_POINTS"],
                "vp_opp": ps[f"{pk[op_c]}_VICTORY_POINTS"],
                "knights_me": ps[f"{pk[me_c]}_KNIGHT_IN_HAND"],
                "knight_move": bool(
                    ps[f"{pk[me_c]}_HAS_PLAYED_DEVELOPMENT_CARD_IN_TURN"]
                ),
                "opp_hand": sum(ps[f"{pk[op_c]}_{x}_IN_HAND"] for x in RESOURCES),
                "live": hexinfo(tuple(r["chosen"]), hexes, own, you, them),
                "bad": hexinfo(b_best, hexes, own, you, them),
                "good": hexinfo(g_best, hexes, own, you, them),
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


def table():
    rows = [json.loads(line) for line in open(OUT)]
    grp = {"bad ahead": [], "near tie": [], "good ahead": []}
    for x in rows:
        if x["qg"] is None or x["qb"] is None:
            continue
        d = x["qb"] - x["qg"]
        grp[
            "bad ahead" if d > 0.05 else "good ahead" if d < -0.05 else "near tie"
        ].append(x)
    feats = [
        ("n", lambda x: 1),
        ("bot VP", lambda x: x["vp_me"]),
        ("opp VP (public)", lambda x: x["vp_opp"]),
        ("bot VP >= 8", lambda x: x["vp_me"] >= 8),
        ("knight move", lambda x: x["knight_move"]),
        ("knights held", lambda x: x["knights_me"]),
        ("opp hand", lambda x: x["opp_hand"]),
        ("bad: own pips", lambda x: x["bad"]["m"]),
        ("bad: opp pips", lambda x: x["bad"]["o"]),
        ("good: opp pips", lambda x: x["good"]["o"]),
        ("bad: opp share of res", lambda x: x["bad"]["o_share"]),
        ("good: opp share of res", lambda x: x["good"]["o_share"]),
        ("bad res: opp's only source", lambda x: x["bad"]["o_share"] >= 0.999),
        ("good res: opp's only source", lambda x: x["good"]["o_share"] >= 0.999),
        (
            "bad: own share of res",
            lambda x: x["bad"]["m"] / x["bad"]["m_res"] if x["bad"]["m_res"] else 0,
        ),
        ("visits bad", lambda x: x["nb"]),
        ("visits good", lambda x: x["ng"]),
        ("turn", lambda x: x["turn"]),
        ("game won", lambda x: x["won"]),
    ]
    print(f"{'':30}" + "".join(f"{k:>12}" for k in grp))
    for name, f in feats:
        vals = []
        for g in grp.values():
            v = [float(f(x)) for x in g]
            vals.append(sum(v) if name == "n" else np.mean(v) if v else float("nan"))
        print(f"{name:30}" + "".join(f"{v:12.2f}" for v in vals))
    res_n = {}
    for x in grp["bad ahead"]:
        res_n.setdefault((x["bad"]["res"], x["good"]["res"]), 0)
        res_n[(x["bad"]["res"], x["good"]["res"])] += 1
    print("bad-ahead (bad res, good res):", sorted(res_n.items(), key=lambda t: -t[1]))
    picks = {}
    for x in rows:
        picks[x["pick"]] = picks.get(x["pick"], 0) + 1
    print("search picks:", picks)
    s = [json.loads(line) for line in open(SET)]
    g = np.mean([len(r["good"]) for r in s])
    b = np.mean([len(r["bad"]) for r in s])
    legal = 18  # 19 hexes minus the robber's current one
    print(
        f"hexes a position: good {g:.2f}, bad {b:.2f} of {legal} legal;"
        f" a uniform pick would be good {100 * g / legal:.0f}%, bad {100 * b / legal:.0f}%"
    )
    gaps = sorted(
        abs(x["qb"] - x["qg"])
        for x in rows
        if x["qg"] is not None and x["qb"] is not None
    )
    print(
        f"|Q bad - Q good|: median {np.median(gaps):.3f}, 90th pct {np.percentile(gaps, 90):.3f}"
    )


def main():
    if sys.argv[1] == "run":
        run(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 3)
    else:
        table()
    return 0


if __name__ == "__main__":
    sys.exit(main())
