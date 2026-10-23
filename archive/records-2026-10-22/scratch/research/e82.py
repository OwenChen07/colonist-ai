"""E82: does the net see a thin Longest Road lead slipping? Live games vs 2100+
(live-loss's long_games.py set). At every bot ROLL decision from roll 40 on:
the best net's value from the bot's chair, who holds the road, both road
lengths, both VPs. Aggregates only.

    .venv/bin/python data/orchestrator/scratch/research/e82.py run [WORKERS] [NET]
    .venv/bin/python data/orchestrator/scratch/research/e82.py table
"""

import json
import os
import random
import sys

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist import encode as E  # noqa: E402
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.net import Net  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from e75_robber import col, jobs  # noqa: E402

OUT = "data/orchestrator/e82/rows.json"
FROM = 40
NET = None


class Obs:
    def __init__(self, you, them):
        self.you, self.them = you, them
        self.rolls = 0
        self.rows = []

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase or decision.action_type != ActionType.ROLL:
            return
        self.rolls += 1
        if self.rolls < FROM or col(decision.color) != self.you:
            return
        idx = {c.value: i for c, i in s.color_to_index.items()}
        cc = {c.value: c for c in s.color_to_index}
        p = s.player_state
        b, t = idx[self.you], idx[self.them]
        static, dynamic = E.encode(game, cc[self.you])
        _, v = NET.forward(static, dynamic[None, :])
        self.rows.append(
            [
                self.rolls,
                round(float(v.reshape(-1)[0]), 4),
                int(p[f"P{b}_HAS_ROAD"]),
                int(p[f"P{t}_HAS_ROAD"]),
                int(p[f"P{b}_LONGEST_ROAD_LENGTH"]),
                int(p[f"P{t}_LONGEST_ROAD_LENGTH"]),
                int(p[f"P{b}_ACTUAL_VICTORY_POINTS"]),
                int(p[f"P{t}_VICTORY_POINTS"]),
            ]
        )


def init(net):
    global NET
    os.nice(19)
    NET = Net.load(net)


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
        if rep.errors or not obs.rows:
            return None
        return {"won": won, "rolls": rolls, "nrolls": obs.rolls, "rows": obs.rows}
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def wil(k, n):
    if not n:
        return "-"
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return f"{100 * p:.1f}% [{100 * (c - h):.1f}, {100 * (c + h):.1f}] (n {n})"


def boot(xs, f, n=1000):
    if not xs:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(1)
    bs = sorted(f(rng.choices(xs, k=len(xs))) for _ in range(n))
    return f(xs), bs[25], bs[975]


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def gap(sel):
    """stated win chance minus realised, points, game-bootstrapped"""
    m = boot(sel, lambda rs: 100 * mean([(r[0] + 1) / 2 - r[1] for r in rs]))
    st = 100 * mean([(r[0] + 1) / 2 for r in sel])
    wr = 100 * mean([r[1] for r in sel])
    return f"n {len(sel):4d} stated {st:5.1f} won {wr:5.1f} gap {m[0]:+5.1f} [{m[1]:+.1f}, {m[2]:+.1f}]"


def snap(g, at=60):
    for r in g["rows"]:
        if r[0] >= at:
            return r if r[0] <= at + 3 else None
    return None


def table(res):
    print(f"{len(res)} games replayed")
    for lo in (70, 0):
        gg = [g for g in res if g["rolls"] >= lo]
        print(
            f"\n===== games of {lo}+ rolls ({len(gg)}): net's value at the bot's roll 60-63"
        )
        groups = {}
        for g in gg:
            r = snap(g)
            if r is None:
                continue
            _, v, br, pr, bl, pl, bvp, pvp = r
            last = g["rows"][-1]
            if br:
                m = min(bl - pl, 3)
                key = ("bot", m)
                kept = last[2] == 1
            elif pr:
                m = min(pl - bl, 3)
                key = ("person", m)
                kept = last[3] == 1
            else:
                key, kept = ("nobody", 0), None
            groups.setdefault(key, []).append((v, int(g["won"]), kept, bvp - pvp))
        for who in ("bot", "person"):
            for m in (1, 2, 3):
                sel = groups.get((who, m), [])
                if m < 3:
                    sel += []
                lab = f"{who:6s} holds by {m}{'+' if m == 3 else ' '}"
                print(
                    f"  {lab}: all  {gap(sel)}  kept {wil(sum(1 for x in sel if x[2]), len(sel))}"
                )
                for k in (True, False):
                    ss = [x for x in sel if x[2] == k]
                    print(f"  {'':20s}{'kept' if k else 'lost'} {gap(ss)}")
            thin = groups.get((who, 1), []) + groups.get((who, 2), [])
            print(f"  {who:6s} holds by 1-2 : all  {gap(thin)}")
            # same VP lead, to strip the mix: thin vs wide at equal VP gap
            for d in (-1, 0, 1, 2, 3):
                a = [x for x in thin if max(min(x[3], 3), -1) == d]
                w = [x for x in groups.get((who, 3), []) if max(min(x[3], 3), -1) == d]
                if a and w:
                    print(
                        f"      vp gap {d:+d}{'+' if d == 3 else ' '}: thin {gap(a)} | wide {gap(w)}"
                    )
        print(f"  nobody holds   : all  {gap(groups.get(('nobody', 0), []))}")
    # the moment the bot loses the road after roll 60 (70+ roll games)
    print(
        "\n===== bot loses the road after roll 60 (70+ rolls): value at its roll before vs after"
    )
    drops = []
    for g in res:
        if g["rolls"] < 70:
            continue
        rows = [r for r in g["rows"] if r[0] >= 60]
        for a, b in zip(rows, rows[1:]):
            if a[2] == 1 and b[2] == 0:
                drops.append((a[1], b[1], int(g["won"]), a[4] - a[5]))
                break
    for lab, sel in (
        ("all", drops),
        ("margin 1-2", [d for d in drops if d[3] <= 2]),
        ("margin 3+", [d for d in drops if d[3] >= 3]),
    ):
        if not sel:
            continue
        before = 100 * mean([(d[0] + 1) / 2 for d in sel])
        after = 100 * mean([(d[1] + 1) / 2 for d in sel])
        dv = boot(sel, lambda rs: 100 * mean([(d[1] - d[0]) / 2 for d in rs]))
        print(
            f"  {lab:10s} n {len(sel):3d}: stated before {before:.1f}, after {after:.1f} (drop {dv[0]:+.1f} [{dv[1]:+.1f}, {dv[2]:+.1f}]); "
            f"won {wil(sum(d[2] for d in sel), len(sel))}; gap after {gap([(d[1], d[2]) for d in sel])}"
        )


def main():
    if sys.argv[1] == "table":
        table(json.load(open(OUT)))
        return 0
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    net = sys.argv[3] if len(sys.argv) > 3 else "runs/g1/best.npz"
    todo = [j for j in jobs() if j[2] >= 60]
    if len(sys.argv) > 4:
        todo = todo[: int(sys.argv[4])]
        OUT_ = OUT + ".test"
    else:
        OUT_ = OUT
    print(f"{len(todo)} games of 60+ rolls, net {net}", flush=True)
    from multiprocessing import Pool

    with Pool(workers, initializer=init, initargs=(net,)) as pool:
        res = [r for r in pool.map(facts, todo, chunksize=8) if r]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT_, "w") as f:
        json.dump(res, f)
    table(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
