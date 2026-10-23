"""Long games (90+ rolls) vs 2100+ players: how the winner scored, and each side's
state at roll 60 and income after it. Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/long_games.py [WORKERS] [LIMIT]
    .venv/bin/python data/orchestrator/scratch/live-loss/long_games.py table
"""

import json
import os
import random
import sys
from collections import Counter
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import CITY, SETTLEMENT, ActionType  # noqa: E402
from catanatron.state_functions import (  # noqa: E402
    get_actual_victory_points,
    get_largest_army,
    get_longest_road_color,
    player_key,
)
from e75_robber import col, incomes, jobs  # noqa: E402

OUT = "data/orchestrator/live-loss/long_games.json"
SNAP = 60


def side_now(game, color):
    s = game.state
    b = s.board
    st = ct = 0
    for _, (bc, kind) in b.buildings.items():
        if bc == color:
            st += kind == SETTLEMENT
            ct += kind == CITY
    k = player_key(s, color)
    army = get_largest_army(s)[0] == color
    road = get_longest_road_color(s) == color
    vp = get_actual_victory_points(s, color)
    hand = sum(
        s.player_state.get(f"{k}_{r}_IN_HAND", 0)
        for r in ("WOOD", "BRICK", "SHEEP", "WHEAT", "ORE")
    )
    return {
        "vp": vp,
        "s": st,
        "c": ct,
        "army": int(army),
        "road": int(road),
        "vpc": vp - st - 2 * ct - 2 * army - 2 * road,
        "kn": s.player_state.get(f"{k}_PLAYED_KNIGHT", 0),
        "hand": hand,
        "rl": s.player_state.get(f"{k}_LONGEST_ROAD_LENGTH", 0),
        "roads": (
            len(b.roads_by_color.get(color, []))
            if hasattr(b, "roads_by_color")
            else None
        ),
    }


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.cc = {}
        self.game = None
        self.rolls = 0
        self.snap = None
        self.late = {"bot": [0, 0, 0], "person": [0, 0, 0]}  # rolls, income, blocked

    def before(self, game):
        pass

    def after(self, game):
        self.game = game

    def step(self, game, decision):
        self.game = game
        side = self.side.get(col(decision.color))
        if side:
            self.cc[side] = decision.color
        s = game.state
        if s.is_initial_build_phase or decision.action_type != ActionType.ROLL:
            return
        self.rolls += 1
        if self.rolls == SNAP and len(self.cc) == 2:
            self.snap = {sd: side_now(game, c) for sd, c in self.cc.items()}
        if self.rolls >= SNAP:
            inc = incomes(s.board)
            rob = s.board.robber_coordinate
            for c, sd in self.side.items():
                d = inc.get(c, {})
                t = self.late[sd]
                t[0] += 1
                t[1] += sum(d.values())
                t[2] += d.get(rob, 0)


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
        if rep.errors or len(obs.cc) != 2 or obs.game is None:
            return None
        end = {sd: side_now(obs.game, c) for sd, c in obs.cc.items()}
        return {
            "won": won,
            "rolls": rolls,
            "nrolls": obs.rolls,
            "end": end,
            "snap": obs.snap,
            "late": obs.late,
        }
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError) as e:
        if sys.argv[-1] == "debug":
            print("skip:", repr(e), flush=True)
        return None


def boot(xs, f, n=1000):
    rng = random.Random(1)
    bs = sorted(f(rng.choices(xs, k=len(xs))) for _ in range(n))
    return f(xs), bs[25], bs[975]


def wil(k, n):
    if not n:
        return "-"
    p = k / n
    z = 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return f"{100 * p:.1f}% [{100 * (c - h):.1f}, {100 * (c + h):.1f}] ({k}/{n})"


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def plan(f):
    """the winner's point sources beyond buildings"""
    tags = []
    if f["army"]:
        tags.append("army")
    if f["road"]:
        tags.append("road")
    if f["vpc"] > 0:
        tags.append("vpc")
    return "+".join(tags) or "buildings only"


def table(res):
    full = [
        r for r in res if max(r["end"]["bot"]["vp"], r["end"]["person"]["vp"]) >= 10
    ]
    print(f"{len(res)} games, {len(full)} played to 10")
    print(
        "winner vp at end:",
        sorted(
            Counter(
                max(r["end"]["bot"]["vp"], r["end"]["person"]["vp"]) for r in res
            ).items()
        ),
    )
    for lo, hi in ((0, 70), (70, 90), (90, 999)):
        g = [r for r in full if lo <= r["rolls"] < hi]
        k = sum(r["won"] for r in g)
        print(f"\n===== rolls {lo}-{hi}: bot {wil(k, len(g))}")
        for won in (True, False):
            sub = [r for r in g if r["won"] == won]
            if not sub:
                continue
            w = "bot" if won else "person"
            l = "person" if won else "bot"
            tag = "bot wins" if won else "bot losses"
            print(
                f"  {tag} ({len(sub)}): winner's extras {dict(Counter(plan(r['end'][w]) for r in sub).most_common())}"
            )
            for who in (w, l):
                e = [r["end"][who] for r in sub]
                print(
                    f"    {who:6s} end: vp {mean([x['vp'] for x in e]):.2f} s {mean([x['s'] for x in e]):.2f}"
                    f" c {mean([x['c'] for x in e]):.2f} army {mean([x['army'] for x in e]):.2f}"
                    f" road {mean([x['road'] for x in e]):.2f} vpc {mean([x['vpc'] for x in e]):.2f}"
                    f" kn {mean([x['kn'] for x in e]):.2f}"
                )
    # long games: state at roll 60, and income after it
    g = [r for r in full if r["rolls"] >= 90 and r["snap"]]
    print(f"\n===== 90+ roll games with a roll-60 snapshot: {len(g)}")
    for label, sel in (
        (
            "leader at 60 (actual vp)",
            lambda r: r["snap"]["bot"]["vp"] - r["snap"]["person"]["vp"],
        ),
    ):
        for name, cond in (
            ("bot ahead", lambda d: d > 0),
            ("level", lambda d: d == 0),
            ("bot behind", lambda d: d < 0),
        ):
            sub = [r for r in g if cond(sel(r))]
            print(f"  {label}, {name}: bot {wil(sum(r['won'] for r in sub), len(sub))}")
    for won in (True, False):
        sub = [r for r in g if r["won"] == won]
        tag = "bot wins" if won else "bot losses"
        print(f"  {tag} ({len(sub)}):")
        for who in ("bot", "person"):
            sn = [r["snap"][who] for r in sub]
            lt = [r["late"][who] for r in sub]
            print(
                f"    {who:6s} @60: vp {mean([x['vp'] for x in sn]):.2f} s {mean([x['s'] for x in sn]):.2f}"
                f" c {mean([x['c'] for x in sn]):.2f} hand {mean([x['hand'] for x in sn]):.1f}"
                f" | after 60: pips/roll {mean([t[1] / t[0] for t in lt if t[0]]):.2f}"
                f" blocked {mean([t[2] / t[0] for t in lt if t[0]]):.2f}"
                f" | gained to end: vp {mean([r['end'][who]['vp'] - r['snap'][who]['vp'] for r in sub]):.2f}"
            )
    # road and army: held at roll 60, held at the end
    for lo, hi in ((70, 90), (90, 999)):
        gg = [r for r in full if lo <= r["rolls"] < hi and r["snap"]]
        for won in (True, False):
            sub = [r for r in gg if r["won"] == won]
            tag = "wins" if won else "losses"
            for k in ("road", "army"):
                for who in ("bot", "person"):
                    a60 = sum(r["snap"][who][k] for r in sub)
                    kept = sum(r["snap"][who][k] and r["end"][who][k] for r in sub)
                    new = sum((not r["snap"][who][k]) and r["end"][who][k] for r in sub)
                    print(
                        f"  rolls {lo}-{hi} {tag:6s} {k:4s} {who:6s}: held@60 {a60}/{len(sub)},"
                        f" kept {kept}/{a60}, took later {new}"
                    )
        # winner's vp gained after 60, by source
        for won in (True, False):
            sub = [r for r in gg if r["won"] == won]
            for who in ("bot", "person"):
                d = {
                    k: mean([r["end"][who][k] - r["snap"][who][k] for r in sub])
                    for k in ("s", "c", "vpc", "kn")
                }
                print(
                    f"  rolls {lo}-{hi} {'wins' if won else 'losses':6s} {who:6s} after 60: "
                    + " ".join(f"{k} {v:+.2f}" for k, v in d.items())
                )
    # keep rate of road / army held at 60, by holder and whether the holder won (70+ rolls)
    gg = [r for r in full if r["rolls"] >= 70 and r["snap"]]
    print(f"\n===== keep rate after roll 60, games of 70+ rolls ({len(gg)})")
    for k in ("road", "army"):
        for who in ("bot", "person"):
            for hw in (True, False):
                sub = [
                    r
                    for r in gg
                    if r["snap"][who][k] and (r["won"] == (who == "bot")) == hw
                ]
                kept = sum(r["end"][who][k] for r in sub)
                print(
                    f"  {k:4s} held by {who:6s}, holder {'won ' if hw else 'lost'}: kept {wil(kept, len(sub))}"
                )
            sub = [r for r in gg if r["snap"][who][k]]
            print(
                f"  {k:4s} held by {who:6s}, all: kept {wil(sum(r['end'][who][k] for r in sub), len(sub))}"
            )
    # road margin at 60 (holder's length - other's), and holder's length gained to the end
    if "rl" in gg[0]["snap"]["bot"]:
        print("\n===== road margin at roll 60 (70+ rolls)")
        for who, oth in (("bot", "person"), ("person", "bot")):
            sub = [r for r in gg if r["snap"][who]["road"]]
            m = boot(
                sub,
                lambda rs: mean(
                    [r["snap"][who]["rl"] - r["snap"][oth]["rl"] for r in rs]
                ),
            )
            g1 = boot(
                sub,
                lambda rs: mean(
                    [r["end"][who]["rl"] - r["snap"][who]["rl"] for r in rs]
                ),
            )
            g2 = boot(
                sub,
                lambda rs: mean(
                    [r["end"][oth]["rl"] - r["snap"][oth]["rl"] for r in rs]
                ),
            )
            print(
                f"  {who} holds ({len(sub)}): margin {m[0]:.2f} [{m[1]:.2f}, {m[2]:.2f}], holder len"
                f" {mean([r['snap'][who]['rl'] for r in sub]):.2f}; to end holder +{g1[0]:.2f}"
                f" [{g1[1]:.2f}, {g1[2]:.2f}], chaser +{g2[0]:.2f} [{g2[1]:.2f}, {g2[2]:.2f}]"
            )
            for mg in (1, 2, 3):
                ss = [
                    r
                    for r in sub
                    if min(r["snap"][who]["rl"] - r["snap"][oth]["rl"], 3) == mg
                ]
                print(
                    f"    margin {mg}{'+' if mg == 3 else ''}: kept {wil(sum(r['end'][who]['road'] for r in ss), len(ss))}"
                )
    # income gap after roll 60 (bot - person, unblocked pips a roll), with a range
    for won in (True, False, None):
        sub = [r for r in g if won is None or r["won"] == won]

        def gap(rs):
            return mean(
                [
                    (
                        r["late"]["bot"][1]
                        - r["late"]["bot"][2]
                        - r["late"]["person"][1]
                        + r["late"]["person"][2]
                    )
                    / r["late"]["bot"][0]
                    for r in rs
                    if r["late"]["bot"][0]
                ]
            )

        m, a, b = boot(sub, gap)
        tag = {True: "wins", False: "losses", None: "all"}[won]
        print(
            f"  net income gap after 60 (bot - person, pips/roll), {tag}: {m:+.2f} [{a:+.2f}, {b:+.2f}]"
        )


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "table":
        res = json.load(open(OUT))
    else:
        workers = int(sys.argv[1]) if len(sys.argv) > 1 else 3
        todo = jobs()
        if len(sys.argv) > 2 and sys.argv[2] != "debug":
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
