"""Thin Longest Road leads (1-2) after roll 50, live games of 70+ rolls vs 2100+.
For each holder side (bot, person), at each of the holder's turn ends while it
holds by 1-2: roads built that turn (and how many lengthened its longest road),
how many open edges would lengthen the holder's road ("room") and the chaser's.
When a thin-lead holder loses the road: was it out-built (chaser's length rose)
or cut (holder's length fell). Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/road_lead.py [WORKERS] [LIMIT]
    .venv/bin/python data/orchestrator/scratch/live-loss/road_lead.py table
"""

import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from e75_robber import col, jobs  # noqa: E402
from long_games import boot, mean  # noqa: E402

OUT = "data/orchestrator/live-loss/road_lead.json"


def room(b, color):
    n = 0
    for e in b.buildable_edges(color):
        b2 = b.copy()
        before = b2.road_lengths[color]
        b2.build_road(color, e)
        n += b2.road_lengths[color] > before
    return n


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.rolls = 0
        self.cmap = None
        self.prev = None  # (has, len) per side
        self.turn = {"roads": 0, "len": 0}
        self.ends = []  # [holder side, margin, roads, len_roads, room_h, room_c]
        self.losses = []  # [holder side, how, rolls]

    def before(self, game):
        pass

    def after(self, game):
        pass

    def snap(self, s):
        p = s.player_state
        out = {}
        for c, i in s.color_to_index.items():
            sd = self.side.get(col(c))
            if sd:
                out[sd] = (
                    int(p[f"P{i}_HAS_ROAD"]),
                    int(p[f"P{i}_LONGEST_ROAD_LENGTH"]),
                )
        return out

    def step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase:
            return
        if self.cmap is None:
            self.cmap = {
                self.side[col(c)]: c for c in s.color_to_index if col(c) in self.side
            }
        cur = self.snap(s)
        pv = self.prev
        self.prev = cur
        if pv and self.rolls >= 50:
            for h, c in (("bot", "person"), ("person", "bot")):
                if pv[h][0] and not cur[h][0]:
                    m = pv[h][1] - pv[c][1]
                    if 1 <= m <= 2:
                        how = (
                            "cut"
                            if cur[h][1] < pv[h][1]
                            else ("outbuilt" if cur[c][1] > pv[c][1] else "other")
                        )
                        self.losses.append([h, how, self.rolls])
        at = decision.action_type
        if at == ActionType.ROLL:
            self.rolls += 1
        me = self.side.get(col(decision.color))
        if not me:
            return
        if at == ActionType.BUILD_ROAD:
            b2 = s.board.copy()
            before = b2.road_lengths[decision.color]
            b2.build_road(decision.color, tuple(decision.value))
            self.turn["roads"] += 1
            self.turn["len"] += b2.road_lengths[decision.color] > before
            return
        if at != ActionType.END_TURN:
            return
        t, self.turn = self.turn, {"roads": 0, "len": 0}
        if self.rolls < 50:
            return
        c = "person" if me == "bot" else "bot"
        if not cur[me][0]:
            return
        m = cur[me][1] - cur[c][1]
        if not 1 <= m <= 2:
            return
        b = s.board
        self.ends.append(
            [me, m, t["roads"], t["len"], room(b, self.cmap[me]), room(b, self.cmap[c])]
        )


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
        if rep.errors or obs.rolls < 70:
            return None
        return {"won": won, "rolls": rolls, "ends": obs.ends, "losses": obs.losses}
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        AttributeError,
        TypeError,
        StopIteration,
    ):
        return None


def wil(k, n):
    if not n:
        return "-"
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return f"{100 * p:.1f}% [{100 * (c - h):.1f}, {100 * (c + h):.1f}] ({k}/{n})"


def table(res):
    print(f"{len(res)} games of 70+ rolls")
    for h in ("bot", "person"):
        gs = [g for g in res if any(e[0] == h for e in g["ends"])]
        ends = [e for g in gs for e in g["ends"] if e[0] == h]
        n = len(ends)
        print(f"\n===== {h} holds by 1-2 after roll 50: {len(gs)} games, {n} turn ends")
        if not n:
            continue

        def per(g, k):
            es = [e for e in g["ends"] if e[0] == h]
            return mean([e[k] for e in es]) if es else None

        for k, lab in (
            (2, "holder roads a turn"),
            (3, "  ...that lengthen its road"),
            (4, "room: edges that would lengthen holder"),
            (5, "room: chaser"),
        ):
            m, a, b = boot(
                gs,
                lambda rs, k=k: mean(
                    [x for x in (per(g, k) for g in rs) if x is not None]
                ),
            )
            print(f"  {lab:40s} {m:.2f} [{a:.2f}, {b:.2f}]")
        boxed = sum(e[4] == 0 for e in ends)
        print(f"  turn ends with holder boxed in (room 0): {wil(boxed, n)}")
        nobuild = sum(e[2] == 0 for e in ends)
        print(f"  turn ends with no road built: {wil(nobuild, n)}")
        ls = [x for g in res for x in g["losses"] if x[0] == h]
        for how in ("outbuilt", "cut", "other"):
            print(f"  lost by {how:8s}: {wil(sum(x[1] == how for x in ls), len(ls))}")
        # boxed-in share by whether this game's thin lead was later lost
        lost_g = [g for g in gs if any(x[0] == h for x in g["losses"])]
        kept_g = [g for g in gs if not any(x[0] == h for x in g["losses"])]
        for lab, sub in (("games lead lost", lost_g), ("games lead kept", kept_g)):
            es = [e for g in sub for e in g["ends"] if e[0] == h]
            if es:
                print(
                    f"  {lab} ({len(sub)}): room holder {mean([e[4] for e in es]):.2f},"
                    f" chaser {mean([e[5] for e in es]):.2f}, boxed {wil(sum(e[4] == 0 for e in es), len(es))}"
                )


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "table":
        res = json.load(open(OUT))
    else:
        workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        todo = [j for j in jobs() if j[2] >= 70]
        if len(sys.argv) > 2:
            todo = todo[: int(sys.argv[2])]
        print(f"{len(todo)} games", flush=True)
        with Pool(workers, initializer=os.nice, initargs=(19,)) as pool:
            res = [r for r in pool.map(facts, todo, chunksize=8) if r]
        print(f"replayed {len(res)}, skipped {len(todo) - len(res)}")
        with open(OUT, "w") as f:
            json.dump(res, f)
    table(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
