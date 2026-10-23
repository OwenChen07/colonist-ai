"""E75: does the robber take more from the bot than from the person, and is the
bot's income more concentrated? Over the 2100+ ranked games with converted
captures, at every roll from turn 20, for each side: income a roll from its
buildings (pips, cities doubled), the share the robber blocks, the share on its
single best hex, distinct hexes drawn from, and how often the robber this side
placed last sits on the other side's best hex. By outcome and length (rolls
under 70, 70-90, 90+), game-clustered bootstrap 95% ranges. Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/e75_robber.py [WORKERS]
    .venv/bin/python data/orchestrator/scratch/live-loss/e75_robber.py table

Per-game sums go to data/orchestrator/live-loss/e75_games.json; "table" reprints
from that file without replaying.
"""

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
from lvlib import ACCOUNTS, load_rows  # noqa: E402

OUT = "data/orchestrator/live-loss/e75_games.json"
FROM_TURN = 20
MIN_MMR = 2100
FIELDS = ("n", "inc", "blk", "bshare", "dist", "placed", "onbest", "rolls_any")


def pips(n):
    return 0 if n is None else 6 - abs(7 - n)


def col(c):
    return getattr(c, "value", c)


def incomes(board):
    """{color: {coord: pips a roll}} for every number hex"""
    out = {}
    for coord, tile in board.map.land_tiles.items():
        p = pips(tile.number)
        if not p:
            continue
        for node in tile.nodes.values():
            b = board.buildings.get(node)
            if b:
                d = out.setdefault(col(b[0]), {})
                d[coord] = d.get(coord, 0) + p * (2 if "CITY" in str(b[1]) else 1)
    return out


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.other = {you: them, them: you}
        self.sums = {s: dict.fromkeys(FIELDS, 0) for s in ("bot", "person")}
        self.placer = None

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase:
            return
        me = col(decision.color)
        if decision.action_type == ActionType.MOVE_ROBBER and me in self.side:
            self.placer = me
            return
        if decision.action_type != ActionType.ROLL or s.num_turns < FROM_TURN:
            return
        b = s.board
        inc = incomes(b)
        rob = b.robber_coordinate
        for c, side in self.side.items():
            d = inc.get(c, {})
            total = sum(d.values())
            t = self.sums[side]
            t["rolls_any"] += 1
            if total <= 0:
                continue
            t["n"] += 1
            t["inc"] += total
            t["blk"] += d.get(rob, 0)
            t["bshare"] += max(d.values()) / total
            t["dist"] += len(d)
            # the robber this side placed last: on the other side's best hex?
            if self.placer == c:
                od = inc.get(self.other[c], {})
                t["placed"] += 1
                if od and od.get(rob, 0) == max(od.values()):
                    t["onbest"] += 1


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
        if rep.errors or not obs.sums["bot"]["n"]:
            return None
        return {"won": won, "rolls": rolls, **obs.sums}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def jobs():
    idx = {}
    for line in open("data/human/index.jsonl"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "ok":
                idx[r["capture"]] = r
    cap = {}
    for a in ACCOUNTS:
        if os.path.exists(f"data/{a}/games.jsonl"):
            for line in open(f"data/{a}/games.jsonl"):
                if line.strip():
                    g = json.loads(line)
                    if g.get("capture"):
                        cap[(a, g["at"])] = g["capture"]
    out = []
    for r in load_rows():
        c = cap.get((r["a"], r["at"]))
        x = idx.get(c)
        if (
            not x
            or r["opp_mmr"] is None
            or r["opp_mmr"] < MIN_MMR
            or not x.get("rolls")
        ):
            continue
        out.append((f"captures/{x['account']}/{c}", r["won"], x["rolls"]))
    return out


def boot(games, f, n=1000):
    rng = random.Random(1)
    if not games:
        return float("nan"), float("nan"), float("nan")
    m = f(games)
    bs = sorted(f(rng.choices(games, k=len(games))) for _ in range(n))
    return m, bs[int(0.025 * n)], bs[int(0.975 * n)]


def ratio(side, num, den):
    def f(gs):
        d = sum(g[side][den] for g in gs)
        return sum(g[side][num] for g in gs) / d if d else float("nan")

    return f


def rel_gap(num, den):
    """bot's ratio over the person's, minus 1"""

    def f(gs):
        b, p = ratio("bot", num, den)(gs), ratio("person", num, den)(gs)
        return b / p - 1 if p else float("nan")

    return f


def fmt(t, pct=False, digits=2):
    if pct:
        return f"{100 * t[0]:.1f}% [{100 * t[1]:.1f}, {100 * t[2]:.1f}]"
    return f"{t[0]:.{digits}f} [{t[1]:.{digits}f}, {t[2]:.{digits}f}]"


def table(res):
    lengths = (
        ("under 70", 0, 70),
        ("70-90", 70, 90),
        ("90+", 90, 9999),
        ("all", 0, 9999),
    )
    for outcome, sel in (
        ("bot lost", [r for r in res if not r["won"]]),
        ("bot won", [r for r in res if r["won"]]),
    ):
        for name, lo, hi in lengths:
            gs = [r for r in sel if lo <= r["rolls"] < hi]
            rolls = sum(r["bot"]["n"] for r in gs)
            print(
                f"== {outcome}, {name} rolls: {len(gs)} games, {rolls} rolls from turn {FROM_TURN}"
            )
            for side in ("bot", "person"):
                print(
                    f"  {side:6} income/roll {fmt(boot(gs, ratio(side, 'inc', 'n')))}"
                    f"  blocked {fmt(boot(gs, ratio(side, 'blk', 'inc')), True)}"
                    f"  best hex {fmt(boot(gs, ratio(side, 'bshare', 'n')), True)}"
                    f"  hexes {fmt(boot(gs, ratio(side, 'dist', 'n')))}"
                    f"  its robber on opp best {fmt(boot(gs, ratio(side, 'onbest', 'placed')), True)}"
                )
            print(
                f"  bot vs person, relative: blocked {fmt(boot(gs, rel_gap('blk', 'inc')), True)}"
                f"  best-hex share {fmt(boot(gs, rel_gap('bshare', 'n')), True)}"
                f"  income {fmt(boot(gs, rel_gap('inc', 'n')), True)}"
            )


def same_role(res):
    """The outcome decides who is robbed more, so compare like with like: the bot
    when it lost against the person when the person lost (and the winners)."""
    lengths = (
        ("under 70", 0, 70),
        ("70-90", 70, 90),
        ("90+", 90, 9999),
        ("all", 0, 9999),
    )
    print(
        "== same role: bot vs person, relative (bot's games where it lost vs games the person lost)"
    )
    for role, bot_won in (("losing side", False), ("winning side", True)):
        for name, lo, hi in lengths:
            xs = [r for r in res if r["won"] == bot_won and lo <= r["rolls"] < hi]
            ys = [r for r in res if r["won"] != bot_won and lo <= r["rolls"] < hi]
            print(
                f"  {role:12} {name:8} ({len(xs)} vs {len(ys)} games):"
                f" blocked {fmt(_pair(xs, ys, 'blk', 'inc'), True)}"
                f"  best-hex share {fmt(_pair(xs, ys, 'bshare', 'n'), True)}"
                f"  hexes {fmt(_pair(xs, ys, 'dist', 'n'), True)}"
                f"  robber on opp best {fmt(_pair(xs, ys, 'onbest', 'placed'), True)}"
            )


def _pair(xs, ys, num, den):
    """bot's ratio over xs against the person's over ys"""
    rng = random.Random(1)
    fb, fp = ratio("bot", num, den), ratio("person", num, den)
    m = fb(xs) / fp(ys) - 1
    bs = sorted(
        fb(rng.choices(xs, k=len(xs))) / fp(rng.choices(ys, k=len(ys))) - 1
        for _ in range(1000)
    )
    return m, bs[25], bs[975]


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "table":
        res = json.load(open(OUT))
    else:
        workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        todo = jobs()
        if len(sys.argv) > 2:  # a quick check on the first few games
            todo = todo[: int(sys.argv[2])]
        print(f"{len(todo)} games with converted captures vs {MIN_MMR}+", flush=True)
        with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
            res = [r for r in pool.map(facts, todo, chunksize=8) if r]
        print(f"replayed {len(res)}, skipped {len(todo) - len(res)}")
        with open(OUT, "w") as f:
            json.dump(res, f)
    table(res)
    same_role(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
