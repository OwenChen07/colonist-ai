"""Dev-card buy timing in 2100+ games, bot vs person, bot wins vs losses.
Per side: the roll (both players' rolls counted) of every dev buy and knight
play. Bot only (its hand is known): on each of its turns whether a dev buy was
legal at some decision and whether it bought; knights in hand when the
person's first seven (not a knight) robs a bot building.
Usage: dev_timing.py [WORKERS [FIRST_N]] | dev_timing.py table"""

import json
import os
import random
import sys
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from catanatron.state_functions import get_dev_cards_in_hand  # noqa: E402
from e75_robber import col, jobs  # noqa: E402
from rob_dwell import hit  # noqa: E402

OUT = "data/orchestrator/live-loss/dev_timing.json"
BUY = ActionType.BUY_DEVELOPMENT_CARD


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.other = {you: them, them: you}
        self.you = you
        self.rolls = 0
        self.buys = {"bot": [], "person": []}
        self.knights = {"bot": [], "person": []}
        self.turns = {}  # bot turn -> [buy legal, bought]
        self.first7 = None
        self.prev = None
        self.bot_color = None

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, d):
        s = game.state
        me = col(d.color)
        t = d.action_type
        if s.is_initial_build_phase or me not in self.side:
            self.prev = (me, t)
            return
        side = self.side[me]
        if t == ActionType.ROLL:
            self.rolls += 1
        if side == "bot":
            self.bot_color = d.color
            row = self.turns.setdefault(s.num_turns, [False, False])
            if any(a.action_type == BUY for a in game.playable_actions):
                row[0] = True
            if t == BUY:
                row[1] = True
        if t == BUY:
            self.buys[side].append(self.rolls)
        elif t == ActionType.PLAY_KNIGHT_CARD:
            self.knights[side].append(self.rolls)
        elif t == ActionType.MOVE_ROBBER and side == "person" and self.first7 is None:
            if (
                self.prev != (me, ActionType.PLAY_KNIGHT_CARD)
                and self.bot_color is not None
            ):
                k, _ = hit(s.board, d.value[0], self.you)
                if k > 0:
                    self.first7 = {
                        "roll": self.rolls,
                        "knights": get_dev_cards_in_hand(s, self.bot_color, "KNIGHT"),
                    }
        self.prev = (me, t)


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
            "buys": obs.buys,
            "knights": obs.knights,
            "bot_turns": len(tv),
            "legal_turns": sum(1 for a, b in tv if a),
            "legal_bought": sum(1 for a, b in tv if a and b),
            "first7": obs.first7,
        }
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def boot(gs, f, n=1000, fmt="{:.2f}"):
    rng = random.Random(1)
    if not gs:
        return "n=0"
    m = f(gs)
    bs = sorted(f(rng.choices(gs, k=len(gs))) for _ in range(n))
    return f"{fmt.format(m)} [{fmt.format(bs[25])}, {fmt.format(bs[975])}]"


def mean(get):
    def f(gs):
        xs = [x for x in (get(g) for g in gs) if x is not None]
        return sum(xs) / len(xs) if xs else float("nan")

    return f


def ratio(num, den):
    def f(gs):
        d = sum(den(g) for g in gs)
        return sum(num(g) for g in gs) / d if d else float("nan")

    return f


def first(xs):
    return xs[0] if xs else None


def table(res):
    groups = (
        ("all", lambda g: True),
        ("bot lost", lambda g: not g["won"]),
        ("bot won", lambda g: g["won"]),
        ("lost, 70+ rolls", lambda g: not g["won"] and g["rolls"] >= 70),
        ("won, 70+ rolls", lambda g: g["won"] and g["rolls"] >= 70),
    )
    for gname, gsel in groups:
        gs = [g for g in res if gsel(g)]
        print(
            f"== {gname}: {len(gs)} games, mean rolls {mean(lambda g: g['rolls'])(gs):.1f}"
        )
        for side in ("bot", "person"):
            print(f"  {side}:")
            print(
                f"    dev buys per game          {boot(gs, mean(lambda g: len(g['buys'][side])))}"
            )
            print(
                f"    never bought a dev         "
                f"{boot(gs, mean(lambda g: float(not g['buys'][side])), fmt='{:.1%}')}"
            )
            print(
                f"    roll of first dev buy      {boot(gs, mean(lambda g: first(g['buys'][side])))}"
            )
            for r in (30, 50):
                print(
                    f"    buys by roll {r:<3}           "
                    f"{boot(gs, mean(lambda g, r=r: sum(1 for x in g['buys'][side] if x <= r)))}"
                )
            print(
                f"    knights played per game    {boot(gs, mean(lambda g: len(g['knights'][side])))}"
            )
            print(
                f"    roll of first knight       {boot(gs, mean(lambda g: first(g['knights'][side])))}"
            )
        print(
            f"  bot turns with a dev buy legal: "
            f"{boot(gs, ratio(lambda g: g['legal_turns'], lambda g: g['bot_turns']), fmt='{:.1%}')}"
        )
        print(
            f"  of those, bought one:           "
            f"{boot(gs, ratio(lambda g: g['legal_bought'], lambda g: g['legal_turns']), fmt='{:.1%}')}"
        )
        f7 = [g for g in gs if g["first7"]]
        print(f"  person's first seven on a bot building: {len(f7)} games")
        print(
            f"    at roll                      {boot(f7, mean(lambda g: g['first7']['roll']))}"
        )
        print(
            f"    bot knights in hand          {boot(f7, mean(lambda g: g['first7']['knights']))}"
        )
        print(
            f"    bot holds >= 1 knight        "
            f"{boot(f7, mean(lambda g: float(g['first7']['knights'] > 0)), fmt='{:.1%}')}"
        )


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
