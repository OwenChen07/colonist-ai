"""Second-seat openings, offline: at each of our setup settlement picks, the
net's value and prior, the move we played (and its live sims), and a big
search's verdict on it.

Usage: open_study.py NET SIMS MAXGAMES [seat 1st|2nd] [pick 0|1]
Prints one row per decision, then a summary. One core.
"""

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import glob
import json
import random
import sys
import time

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.mcts import Search
from catanatron.colonist.net import Net
from catanatron.openhex.board import Board
from catanatron.openhex.client import openhex_legal
from catanatron.openhex.follow import Follower

PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
net_path, sims, maxg = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
want_seat = sys.argv[4] if len(sys.argv) > 4 else "2nd"
want_pick = int(sys.argv[5]) if len(sys.argv) > 5 else 0
net = Net.load(net_path)
rng = random.Random(1)
rows = []
paths = sorted(glob.glob("data/openhex/games/*.json"), key=os.path.getmtime)
for path in paths:
    if len(rows) >= maxg:
        break
    g = json.load(open(path))
    fs = g.get("final_state")
    if not fs or not fs.get("winner") or not g.get("tiles"):
        continue
    if (g.get("game") or {}).get("status") != "completed":
        continue
    me = g["me"]
    seat = "1st" if g["turn_order"][0] == me else "2nd"
    if seat != want_seat:
        continue
    events = sorted({e["seq"]: e for e in g["events"]}.values(), key=lambda e: e["seq"])
    mine = [
        i
        for i, e in enumerate(events)
        if e["event"]["type"] == "setupSettlementPlaced"
        and e["event"].get("player") == me
    ]
    if len(mine) <= want_pick:
        continue
    idx = mine[want_pick]
    board = Board(g["tiles"], g["ports"])
    fol = Follower(board, g["turn_order"], me, g["robber"])
    try:
        fol.feed(events[:idx])
        fol.settle()
        game = fol.replay()
    except Exception as ex:  # noqa: BLE001
        print(g["id"][:8], "replay failed", repr(ex)[:100])
        continue
    played = board.node(events[idx]["event"]["vertex"])
    actions = openhex_legal(game, game.playable_actions)
    keys = E.legal_indices(actions)
    priors, value = net.evaluate(game, game.state.current_color(), keys)
    order = list(np.argsort(-priors))
    ai = next(i for i, a in enumerate(actions) if a.value == played)
    t0 = time.time()
    s = Search(net, sims, rng=rng, noise=False, root_action_filter=openhex_legal)
    best, root = s.run(game)
    dt = time.time() - t0
    q = s.completed_q(root)
    ri = next(i for i, a in enumerate(root.actions) if a.value == played)
    bi = next(i for i, a in enumerate(root.actions) if a.value == best.value)
    setl = [m for m in g.get("moves", []) if "SETTLEMENT" in str(m[0])]
    live_sims = setl[want_pick][1] if len(setl) > want_pick else None
    s2 = Search(
        net, live_sims or 20000, rng=rng, noise=False, root_action_filter=openhex_legal
    )
    live_best, _ = s2.run(game)
    adj = board.catan_map.adjacent_tiles
    pips = lambda n: sum(PIPS.get(t.number, 0) for t in adj[n])  # noqa: E731
    row = dict(
        id=g["id"][:8],
        won=fs["winner"] == me,
        v=float(value),
        played=played,
        prior_rank=order.index(ai) + 1,
        prior=float(priors[ai]),
        q_played=float(q[ri]),
        best=best.value,
        q_best=float(q[bi]),
        same=best.value == played,
        pips_played=pips(played),
        pips_best=pips(best.value),
        n_played=int(root.N[ri]),
        live_sims=live_sims,
        rerun_same=live_best.value == played,
        rerun_vs_big=live_best.value == best.value,
        sec=dt,
    )
    rows.append(row)
    print(json.dumps(row), flush=True)

if rows:
    n = len(rows)
    same = sum(r["same"] for r in rows)
    gap = [r["q_best"] - r["q_played"] for r in rows]
    print(
        f"SUMMARY {want_seat} pick {want_pick} n={n} sims={sims} "
        f"same={same}/{n} mean_gap={np.mean(gap):.3f} max_gap={max(gap):.3f} "
        f"net_v={np.mean([r['v'] for r in rows]):+.3f} "
        f"won={sum(r['won'] for r in rows)} "
        f"won_when_same={sum(r['won'] for r in rows if r['same'])}/{same} "
        f"pips_played={np.mean([r['pips_played'] for r in rows]):.1f} "
        f"pips_best={np.mean([r['pips_best'] for r in rows]):.1f} "
        f"rerun_same={sum(r['rerun_same'] for r in rows)} "
        f"rerun_vs_big={sum(r['rerun_vs_big'] for r in rows)} "
        f"sec={np.mean([r['sec'] for r in rows]):.0f}"
    )
