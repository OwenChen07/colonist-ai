"""Bot decisions in blown-lead games where the person took longest road,
re-searched deeply (SIMS, no noise, runs/g1/best.npz). A position is a
failure when the deep search's best move differs from the move played and
the played move's Q is at least GAP lower (both with >= MIN_N visits), or
the played move was not among the deep search's considered candidates.
Writes data/orchestrator/live-loss/road_stolen_positions.jsonl.
Usage: road_stolen_set.py [sims]"""

import json
import random
import sys
from collections import Counter
from multiprocessing import Pool

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.mcts import Search
from catanatron.colonist.net import Net
from catanatron.colonist.replay import replay
from catanatron.models.player import Color

NET = "runs/g1/best.npz"
SRC = "data/orchestrator/live-loss/blown_leads.jsonl"
OUT = "data/orchestrator/live-loss/road_stolen_positions.jsonl"
GAP, MIN_N = 0.15, 40
SIMS = int(sys.argv[1]) if len(sys.argv) > 1 else 3200
_net = None


class Obs:
    def __init__(self, me, from_turn):
        self.me, self.from_turn = me, from_turn
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
        played = E.action_key(decision)
        search = Search(
            _net,
            simulations=SIMS,
            rng=random.Random(4242 + len(self.rows)),
            noise=False,
        )
        action, root = search.run(game)
        best = E.action_key(action)
        keys = [E.action_key(a) for a in root.actions]
        q = search.completed_q(root)
        i_best = keys.index(best)
        i_played = keys.index(played) if played in keys else None
        row = {
            "turn": game.state.num_turns,
            "prompt": str(game.state.current_prompt),
            "n_actions": len(keys),
            "played": str(played),
            "played_type": decision.action_type.name,
            "best": str(best),
            "best_type": action.action_type.name,
            "q_best": round(float(q[i_best]), 3),
            "n_best": int(root.N[i_best]),
            "immediate_win": root.immediate_win is not None,
        }
        if i_played is not None:
            row["q_played"] = round(float(q[i_played]), 3)
            row["n_played"] = int(root.N[i_played])
        diff = best != played
        visited = i_played is not None and root.N[i_played] >= MIN_N
        row["failure"] = bool(
            diff
            and (
                root.immediate_win is not None
                or (visited and row["q_best"] - row["q_played"] >= GAP)
                or (i_played is not None and root.N[i_played] == 0)
            )
        )
        self.rows.append(row)


def scan(lead):
    global _net
    if _net is None:
        _net = Net.load(NET)
    conv = convert(load_capture(lead["capture"]))
    rec = conv.record
    you = [c for c, p in rec["players"].items() if p["you"]][0]
    obs = Obs(Color[you], lead["lead_turn"])
    rep = replay(rec, observer=obs)
    return (
        [
            {"capture": lead["capture"], "lead_turn": lead["lead_turn"], **r}
            for r in obs.rows
        ]
        if rep.ok
        else []
    )


def main():
    leads = [json.loads(x) for x in open(SRC)]
    leads = [r for r in leads if r["road_stolen"]]
    rows = []
    with Pool(4) as pool, open(OUT, "w") as f:
        for rs in pool.imap_unordered(scan, leads):
            rows += rs
            for r in rs:
                f.write(json.dumps(r) + "\n")
            f.flush()
            print("game done", len(rs), flush=True)
    fails = [r for r in rows if r["failure"]]
    print(f"games {len(leads)} positions {len(rows)} failures {len(fails)} sims {SIMS}")
    print("games with a failure", len({r["capture"] for r in fails}))
    print(
        "failure played types", Counter(r["played_type"] for r in fails).most_common()
    )
    print("failure best types", Counter(r["best_type"] for r in fails).most_common())
    print(
        "pairs",
        Counter((r["played_type"], r["best_type"]) for r in fails).most_common(10),
    )
    print("missed immediate wins", sum(r["immediate_win"] for r in fails))
    gaps = sorted(r["q_best"] - r.get("q_played", -1) for r in fails)
    if gaps:
        print("q gap median %.3f max %.3f" % (gaps[len(gaps) // 2], gaps[-1]))


if __name__ == "__main__":
    main()
