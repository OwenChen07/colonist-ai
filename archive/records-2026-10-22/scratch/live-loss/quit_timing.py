"""When people resign: rolls and VP gap at the end of each ranked game with an 'ended' field.
Compares opponent-quit games with finished bot wins/losses, per arm.
Usage: quit_timing.py [--since '2026-10-21 10:26:41'] [--until ...] [--accounts bot3,bot10]
(times are Toronto; with --accounts the groups are accounts, not arms)"""

import argparse
import json
import math
import os
import sys
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from catanatron.state_functions import get_actual_victory_points  # noqa: E402
from e75_robber import col  # noqa: E402

ACCOUNTS = ["bot3", "bot10", "bot9", "bot11", "bot12", "bot8"]
TZ = ZoneInfo("America/Toronto")
E54 = datetime(2026, 10, 20, 20, 41, tzinfo=TZ).timestamp()
CACHE = Path("data/orchestrator/live-loss/quit_timing.json")


class Obs:
    def __init__(self, you, them):
        self.side = {you: "bot", them: "person"}
        self.cc = {}
        self.game = None
        self.rolls = 0

    def before(self, game):
        pass

    def after(self, game):
        self.game = game

    def step(self, game, decision):
        self.game = game
        sd = self.side.get(col(decision.color))
        if sd:
            self.cc[sd] = decision.color
        if (
            not game.state.is_initial_build_phase
            and decision.action_type == ActionType.ROLL
        ):
            self.rolls += 1


def facts(path):
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
        vp = {
            sd: get_actual_victory_points(obs.game.state, c) for sd, c in obs.cc.items()
        }
        return {"rolls": obs.rolls, "bvp": vp["bot"], "pvp": vp["person"]}
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


def arm(a, g):
    if a in ("bot9", "bot11") and g["at"] >= E54:
        return "E54"
    if a == "bot9" and g.get("round") == 152:
        return "bot9 r152 base"
    return a


def mean_ci(xs):
    n = len(xs)
    if n == 0:
        return "n=0"
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / max(1, n - 1))
    return f"{m:5.1f} ± {1.96 * sd / math.sqrt(n):4.1f} (n={n})"


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def wilson(k, n):
    if n == 0:
        return "n=0"
    p, z = k / n, 1.96
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return f"{k}/{n} {100 * p:4.1f}% [{100 * (c - h):4.1f},{100 * (c + h):4.1f}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="Toronto time, e.g. '2026-10-21 10:26:41'")
    ap.add_argument("--until", help="Toronto time")
    ap.add_argument(
        "--accounts", help="comma list, e.g. bot3,bot10; groups by account only"
    )
    args = ap.parse_args()
    ts = lambda s: (
        datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp() if s else None
    )
    since, until = ts(args.since), ts(args.until)
    accounts = args.accounts.split(",") if args.accounts else ACCOUNTS
    games = []
    for a in accounts:
        for line in open(f"data/{a}/games.jsonl"):
            g = json.loads(line)
            if (
                g.get("queue", "ranked") != "ranked"
                or "ended" not in g
                or g.get("winner") is None
            ):
                continue
            if (since and g["at"] < since) or (until and g["at"] >= until):
                continue
            g["account"] = a
            games.append(g)
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [g for g in games if g["capture"] not in cache]
    print(f"{len(games)} games, replaying {len(todo)}", file=sys.stderr)
    with Pool(4) as pool:
        res = pool.map(
            facts,
            [Path("captures") / g["account"] / g["capture"] for g in todo],
            chunksize=4,
        )
    for g, r in zip(todo, res):
        cache[g["capture"]] = r
    CACHE.write_text(json.dumps(cache))
    groups = {}
    for g in games:
        f = cache.get(g["capture"])
        if not f:
            continue
        won = g["winner"] == g["bot_color"]
        kind = (
            "opp quit"
            if g["ended"] == "resignation"
            else ("fin win" if won else "fin loss")
        )
        f = dict(f, gap=f["bvp"] - f["pvp"], kind=kind)
        groups.setdefault(
            g["account"] if args.accounts else arm(g["account"], g), []
        ).append(f)
        groups.setdefault("ALL", []).append(f)
    names = (
        ["ALL"] + accounts
        if args.accounts
        else ["ALL", "E54", "bot9 r152 base", "bot3", "bot10", "bot12", "bot8"]
    )
    if since or until:
        print(f"window: {args.since or 'start'} to {args.until or 'now'} Toronto")
    for name in names:
        rows = groups.get(name, [])
        q = [r for r in rows if r["kind"] == "opp quit"]
        print(f"\n== {name}: {len(rows)} replayed")
        print(
            f"  quit share {wilson(len(q), len(rows))} | early quits (<40 rolls) of all games "
            f"{wilson(sum(r['rolls'] < 40 for r in q), len(rows))} | quits at gap<=1 "
            f"{wilson(sum(r['gap'] <= 1 for r in q), len(q))}"
        )
        for kind in ["opp quit", "fin win", "fin loss"]:
            sel = [r for r in rows if r["kind"] == kind]
            if not sel:
                continue
            gaps = [r["gap"] for r in sel]
            print(
                f"  {kind:8s} n={len(sel):3d} rolls {mean_ci([r['rolls'] for r in sel])} | "
                f"bot VP {mean_ci([r['bvp'] for r in sel])} | gap {mean_ci(gaps)} "
                f"median {pct(gaps, 0.5)} p10 {pct(gaps, 0.1)} | gap<=1: "
                f"{sum(x <= 1 for x in gaps)} | bot VP>=13: {sum(r['bvp'] >= 13 for r in sel)}"
            )
        if q:
            for lo, hi in [(0, 40), (40, 60), (60, 80), (80, 999)]:
                s = [r for r in q if lo <= r["rolls"] < hi]
                print(f"    quits at rolls {lo}-{hi}: {len(s)}")


if __name__ == "__main__":
    main()
