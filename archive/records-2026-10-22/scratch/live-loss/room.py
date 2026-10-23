"""Expansion room in 2100+ games: is the person's settlement lead room or choice?
Per side at rolls 20/40/60/80 (both players' rolls counted): settlements,
cities, open spots on its own roads (0 roads away) and spots 1 road away.
Bot only (hand known): on each of its turns whether it could pay for a
settlement, whether it had a spot then, and whether it built one.
Usage: room.py [WORKERS [FIRST_N]] | room.py table"""

import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.decks import SETTLEMENT_COST_FREQDECK  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from catanatron.state_functions import (  # noqa: E402
    get_player_buildings,
    player_resource_freqdeck_contains,
)
from dev_timing import boot, mean, ratio  # noqa: E402
from e75_robber import col, jobs  # noqa: E402

OUT = "data/orchestrator/live-loss/room.json"
MARKS = (20, 40, 60, 80)
SETTLE = ActionType.BUILD_SETTLEMENT


def snapshot(s, color):
    b = s.board
    r0 = set(b.buildable_node_ids(color))
    r1 = set()
    for e in b.buildable_edges(color):
        for n in e:
            if n in b.board_buildable_ids and n not in r0:
                r1.add(n)
    return {
        "settlements": len(get_player_buildings(s, color, "SETTLEMENT")),
        "cities": len(get_player_buildings(s, color, "CITY")),
        "r0": len(r0),
        "r1": len(r1),
    }


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.rolls = 0
        self.marks = {}  # mark -> {side: snapshot}
        self.turns = {}  # bot turn -> [affordable, spot, built]
        self.colors = {}

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, d):
        s = game.state
        me = col(d.color)
        t = d.action_type
        if s.is_initial_build_phase or me not in self.side:
            return
        side = self.side[me]
        self.colors[side] = d.color
        if t == ActionType.ROLL:
            self.rolls += 1
        for m in MARKS:
            if self.rolls >= m and m not in self.marks and len(self.colors) == 2:
                self.marks[m] = {sd: snapshot(s, c) for sd, c in self.colors.items()}
        if side == "bot":
            row = self.turns.setdefault(s.num_turns, [False, False, False])
            if player_resource_freqdeck_contains(s, d.color, SETTLEMENT_COST_FREQDECK):
                row[0] = True
                if s.board.buildable_node_ids(d.color):
                    row[1] = True
            if t == SETTLE:
                row[2] = True


def facts(job):
    path, won, rolls = job
    try:
        record = convert(load_capture(path)).record
        you = [c for c, p in record["players"].items() if p["you"]]
        if len(you) != 1 or len(record["seats"]) != 2:
            return None
        you = you[0]
        them = next(c for c in record["seats"] if c != you)
        obs = Obs(you, them)
        rep = replay(record, observer=obs)
        if rep.errors:
            return None
        tv = list(obs.turns.values())
        return {
            "won": won,
            "rolls": obs.rolls,
            "marks": {str(k): v for k, v in obs.marks.items()},
            "bot_turns": len(tv),
            "afford": sum(1 for a, b, c in tv if a),
            "afford_spot": sum(1 for a, b, c in tv if a and b),
            "afford_spot_built": sum(1 for a, b, c in tv if a and b and c),
            "built": sum(1 for a, b, c in tv if c),
        }
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def table(res):
    groups = (
        ("bot lost", lambda g: not g["won"]),
        ("bot won", lambda g: g["won"]),
        ("lost, 70+ rolls", lambda g: not g["won"] and g["rolls"] >= 70),
        ("won, 70+ rolls", lambda g: g["won"] and g["rolls"] >= 70),
    )
    for gname, gsel in groups:
        gs = [g for g in res if gsel(g)]
        print(f"== {gname}: {len(gs)} games")
        for m in MARKS:
            hs = [g for g in gs if str(m) in g["marks"]]
            if len(hs) < 20:
                continue
            print(f"  roll {m} ({len(hs)} games)")
            for side in ("bot", "person"):
                cells = []
                for k in ("settlements", "cities", "r0", "r1"):
                    cells.append(
                        f"{k} {boot(hs, mean(lambda g, k=k: g['marks'][str(m)][side][k]))}"
                    )
                no = boot(
                    hs,
                    mean(lambda g: float(g["marks"][str(m)][side]["r0"] == 0)),
                    fmt="{:.0%}",
                )
                print(f"    {side:<6} " + "; ".join(cells) + f"; no spot {no}")
        print(
            f"  bot turns able to pay for a settlement: {boot(gs, ratio(lambda g: g['afford'], lambda g: g['bot_turns']), fmt='{:.1%}')}"
        )
        print(
            f"    of those, had a spot:   {boot(gs, ratio(lambda g: g['afford_spot'], lambda g: g['afford']), fmt='{:.1%}')}"
        )
        print(
            f"    had a spot, built:      {boot(gs, ratio(lambda g: g['afford_spot_built'], lambda g: g['afford_spot']), fmt='{:.1%}')}"
        )
        print(f"  bot settlements built a game: {boot(gs, mean(lambda g: g['built']))}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "table":
        res = json.load(open(OUT))
    else:
        workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        todo = jobs()
        if len(sys.argv) > 2:
            todo = todo[: int(sys.argv[2])]
        print(f"{len(todo)} games vs 2100+", flush=True)
        with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
            res = [r for r in pool.map(facts, todo, chunksize=8) if r]
        print(f"replayed {len(res)}, skipped {len(todo) - len(res)}")
        with open(OUT, "w") as f:
            json.dump(res, f)
    table(res)


if __name__ == "__main__":
    main()
