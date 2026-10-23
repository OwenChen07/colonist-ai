"""Robustness of the END_TURN -> MARITIME_TRADE disagreements in
road_stolen_positions.jsonl: re-search each such position with SEEDS extra
seeds at 3200 sims and one at 6400, and the same for a control set of
END_TURN positions where the first deep search agreed but a trade was legal.
Writes data/orchestrator/live-loss/endturn_trade_positions.jsonl."""

import json
import random
from collections import defaultdict
from multiprocessing import Pool

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.mcts import Search
from catanatron.colonist.net import Net
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType
from catanatron.models.player import Color

NET = "runs/g1/best.npz"
SRC = "data/orchestrator/live-loss/road_stolen_positions.jsonl"
OUT = "data/orchestrator/live-loss/endturn_trade_positions.jsonl"
RUNS = [(3200, 101), (3200, 202), (3200, 303), (6400, 404)]
_net = None


class Obs:
    def __init__(self, me, from_turn, targets):
        self.me, self.from_turn, self.targets = me, from_turn, targets
        self.k = 0
        self.rows = []

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        if decision.color != self.me or game.state.num_turns < self.from_turn:
            return
        if len(game.playable_actions) < 2:
            return
        k, self.k = self.k, self.k + 1
        if k not in self.targets:
            return
        trades = [
            a
            for a in game.playable_actions
            if a.action_type == ActionType.MARITIME_TRADE
        ]
        out = {
            "k": k,
            "group": self.targets[k],
            "turn": game.state.num_turns,
            "runs": [],
        }
        for sims, seed in RUNS:
            search = Search(
                _net, simulations=sims, rng=random.Random(seed + k), noise=False
            )
            action, root = search.run(game)
            keys = [E.action_key(a) for a in root.actions]
            q = search.completed_q(root)
            end = keys.index(E.action_key(decision))
            tq = [
                float(q[i])
                for i, a in enumerate(root.actions)
                if a.action_type == ActionType.MARITIME_TRADE
            ]
            out["runs"].append(
                {
                    "sims": sims,
                    "best_type": action.action_type.name,
                    "q_end": round(float(q[end]), 3),
                    "q_best_trade": round(max(tq), 3) if tq else None,
                }
            )
        out["n_trades"] = len(trades)
        self.rows.append(out)


def scan(item):
    global _net
    if _net is None:
        _net = Net.load(NET)
    capture, lead_turn, targets = item
    rec = convert(load_capture(capture)).record
    you = [c for c, p in rec["players"].items() if p["you"]][0]
    obs = Obs(Color[you], lead_turn, targets)
    rep = replay(rec, observer=obs)
    return [{"capture": capture, **r} for r in obs.rows] if rep.ok else []


def main():
    rows = [json.loads(x) for x in open(SRC)]
    by_cap = defaultdict(list)
    for r in rows:
        by_cap[r["capture"]].append(r)
    items = []
    for cap, rs in by_cap.items():
        targets = {}
        for k, r in enumerate(rs):
            if r["played_type"] != "END_TURN":
                continue
            if r["best_type"] == "MARITIME_TRADE":
                targets[k] = "disagree"
            elif r["best_type"] == "END_TURN" and r["n_actions"] >= 3:
                targets[k] = "control"
        if targets:
            items.append((cap, rs[0]["lead_turn"], targets))
    print(
        "games", len(items), "positions", sum(len(t) for _, _, t in items), flush=True
    )
    with Pool(4) as pool, open(OUT, "w") as f:
        for rs in pool.imap_unordered(scan, items):
            for r in rs:
                f.write(json.dumps(r) + "\n")
            f.flush()
            print("game done", len(rs), flush=True)


if __name__ == "__main__":
    main()
