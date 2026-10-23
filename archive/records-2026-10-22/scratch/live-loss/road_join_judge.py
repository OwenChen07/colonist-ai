"""Deep-search the 'stake' passed-up joins (first such decision per turn) from
road_join_count.json: r73 + people model at mix 0.5, no noise, SIMS sims.
A failure: the best join's completed Q beats the played move's by >= GAP.
-> data/orchestrator/live-loss/road_join_positions.jsonl (no names)."""

import json
import random
import sys
from multiprocessing import Pool

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.mcts import Search
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from road_join import lr_after, nets, own_lr  # noqa: E402
from road_join_sum import stake  # noqa: E402

SIMS = int(sys.argv[1]) if len(sys.argv) > 1 else 2400
GAP = 0.05
OUT = "data/orchestrator/live-loss/road_join_positions.jsonl"


class Judge:
    def __init__(self, me, turn):
        self.me, self.turn, self.out = me, turn, None
        self.i = -1

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        self.i += 1
        s = game.state
        if self.out or decision.color.value != self.me or s.num_turns != self.turn:
            return
        color = decision.color
        cur = own_lr(s.board, color)
        joins = {}
        for a in game.playable_actions:
            if a.action_type == ActionType.BUILD_ROAD:
                length, merged = lr_after(s.board, color, a.value)
                if merged and length - cur >= 2:
                    joins[tuple(sorted(a.value))] = length
        if not joins:
            return
        bot, human = nets()
        search = Search(
            bot,
            SIMS,
            rng=random.Random(11),
            noise=False,
            opponent_model=human,
            opponent_mix=0.5,
        )
        action, root = search.run(game)
        keys = [E.action_key(a) for a in root.actions]
        q = search.completed_q(root)
        played = E.action_key(decision)
        jq = [
            (
                float(q[keys.index((ActionType.BUILD_ROAD, e))]),
                e,
                int(root.N[keys.index((ActionType.BUILD_ROAD, e))]),
                float(root.prior[keys.index((ActionType.BUILD_ROAD, e))]),
            )
            for e in joins
        ]
        best = max(jq)
        pq = float(q[keys.index(played)]) if played in keys else None
        self.out = {
            "entry": self.i,
            "turn": s.num_turns,
            "played": (
                str(played[1]) if played[0] != ActionType.END_TURN else "END_TURN"
            ),
            "played_type": played[0].value,
            "played_q": pq,
            "chosen": str(E.action_key(action)),
            "join": str(best[1]),
            "join_lr": joins[best[1]],
            "lr_now": cur,
            "join_q": best[0],
            "join_N": best[2],
            "join_prior": best[3],
            "root_value": float(root.value),
            "failure": pq is not None and best[0] - pq >= GAP,
        }


def job(item):
    path, turn, won = item
    rec = convert(load_capture(path)).record
    me = next(c for c, p in rec["players"].items() if p["you"])
    j = Judge(me, turn)
    replay(rec, observer=j)
    return {"capture": path, "bot_won": won, **(j.out or {})}


def main():
    import road_join_sum as S

    items = []
    for g in S.ok:
        turns = {}
        for r in g["rows"]:
            turns.setdefault(r["turn"], []).append(r)
        for t, rs in sorted(turns.items()):
            if not any(r["took"] for r in rs) and any(stake(r) for r in rs):
                items.append((g["capture"], t, g["bot_won"]))
    with Pool(4) as pool:
        res = pool.map(job, items)
    with open(OUT, "w") as f:
        for r in res:
            f.write(json.dumps(r) + "\n")
    done = [r for r in res if "join_q" in r]
    fails = [r for r in done if r["failure"]]
    unvisited = sum(r["join_N"] == 0 for r in done)
    print(
        f"positions {len(done)}; failures (join Q >= played + {GAP}): {len(fails)}; join never visited: {unvisited}"
    )
    for r in done:
        print(
            f"  t{r['turn']} lr {r['lr_now']}->{r['join_lr']} join prior {r['join_prior']:.3f} N {r['join_N']} "
            f"Q {r['join_q']:+.3f} | played {r['played_type']} Q {r['played_q']} | v {r['root_value']:+.2f} "
            f"won {r['bot_won']} {'FAIL' if r['failure'] else ''}"
        )


if __name__ == "__main__":
    main()
