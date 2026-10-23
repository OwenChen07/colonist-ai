"""Seat split against people: last N ranked games across bot3/4/5/7.
Per game: the bot's seat (first or second mover), result, and both sides'
roads, settlements, cities, dev cards bought, longest road / largest army
holder at the end, game length, and the turn the bot fell behind in public
points. Aggregates only, no names. Per-game facts cached in
data/orchestrator/analysis/seat_split.json.

    .venv/bin/python data/orchestrator/scratch/live-loss/seat_split.py [N]
"""

import json
import random
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.loss_report import wilson
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType

# bot4/5/7 retired Oct 20, 9:47 AM; bot8 (people model) left out on purpose.
ACCOUNTS = ("bot3", "bot4", "bot5", "bot7", "bot9", "bot10", "bot11", "bot12")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
CACHE = Path("data/orchestrator/analysis/seat_split.json")
LABEL = {}
E58 = datetime(2026, 10, 19, 13, 42, tzinfo=ZoneInfo("America/Toronto")).timestamp()


class Obs:
    def __init__(self, you, them):
        self.you, self.them = you, them
        self.devs = {you: 0, them: 0}
        self.vp = []  # (turn, bot public vp, person public vp) once per turn
        self.last_turn = -1

    def before(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        c = getattr(decision.color, "value", decision.color)
        if decision.action_type == ActionType.BUY_DEVELOPMENT_CARD and c in self.devs:
            self.devs[c] += 1
        if s.is_initial_build_phase or s.num_turns == self.last_turn:
            return
        self.last_turn = s.num_turns
        idx = {k.value: i for k, i in s.color_to_index.items()}
        p = s.player_state
        self.vp.append(
            (
                s.num_turns,
                p[f"P{idx[self.you]}_VICTORY_POINTS"],
                p[f"P{idx[self.them]}_VICTORY_POINTS"],
            )
        )

    def after(self, game):
        s = game.state
        idx = {k.value: i for k, i in s.color_to_index.items()}
        p = s.player_state
        self.end = {}
        for side, c in (("bot", self.you), ("opp", self.them)):
            i = idx[c]
            cities = 4 - p[f"P{i}_CITIES_AVAILABLE"]
            self.end[side] = {
                "roads": 15 - p[f"P{i}_ROADS_AVAILABLE"] - 2,
                "settlements": 5 - p[f"P{i}_SETTLEMENTS_AVAILABLE"] + cities - 2,
                "cities": cities,
                "devs": self.devs[c],
                "lr": bool(p[f"P{i}_HAS_ROAD"]),
                "la": bool(p[f"P{i}_HAS_ARMY"]),
                "vp": p[f"P{i}_VICTORY_POINTS"],
            }
        self.turns = s.num_turns


def facts(path):
    record = convert(load_capture(path)).record
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return None
    you = you[0]
    them = next(c for c in record["seats"] if c != you)
    o = Obs(you, them)
    rep = replay(record, observer=o)
    if not hasattr(o, "end"):
        return None
    first_behind = next((t for t, b, q in o.vp if b < q), None)
    # From this turn on the bot never again had at least the person's points.
    last_level = max((t for t, b, q in o.vp if b >= q), default=None)
    return {
        "first": record["seats"][0] == you,
        "ok": rep.ok,
        "turns": o.turns,
        "end": o.end,
        "first_behind": first_behind,
        "behind_for_good": (
            (last_level + 1)
            if last_level is not None and last_level < o.turns
            else None
        ),
        "resigned": bool(record.get("resigned")),
    }


def boot(xs, reps=2000, seed=0):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "n/a"
    rng = random.Random(seed)
    m = sum(xs) / len(xs)
    v = sorted(
        sum(xs[rng.randrange(len(xs))] for _ in xs) / len(xs) for _ in range(reps)
    )
    return f"{m:.2f} [{v[int(.025 * reps)]:.2f}, {v[int(.975 * reps) - 1]:.2f}]"


def main():
    idx = {}
    for line in open("data/human/index.jsonl"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "ok":
                idx[r["capture"]] = r
    games = []
    for a in ACCOUNTS:
        for line in open(f"data/{a}/games.jsonl"):
            if not line.strip():
                continue
            g = json.loads(line)
            if g.get("queue") != "ranked" or g.get("own_bot"):
                continue
            ix = idx.get(g["capture"])
            won = (
                ix["bot_won"]
                if ix and "bot_won" in ix
                else g.get("winner") == g.get("bot_color")
            )
            games.append((g["at"], a, g["capture"], bool(won)))
            LABEL[g["capture"]] = (
                a,
                g.get("label") or (f"round {g['round']}" if g.get("round") else "?"),
            )
    games.sort()
    games = games[-N:]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    for k, (at, a, cap, won) in enumerate(games):
        if cap in cache:
            continue
        path = Path("captures") / a / cap
        try:
            cache[cap] = facts(path) if path.exists() else None
        except Exception as error:  # noqa: BLE001
            print(f"{cap}: {error!r}", file=sys.stderr)
            cache[cap] = None
        if k % 100 == 0:
            CACHE.write_text(json.dumps(cache))
    CACHE.write_text(json.dumps(cache))

    tz = ZoneInfo("America/Toronto")
    rows = [(at, won, cache[cap]) for at, a, cap, won in games if cache.get(cap)]
    print(
        f"{len(games)} games, {len(rows)} parsed, span "
        f"{datetime.fromtimestamp(games[0][0], tz):%b %d %I:%M %p} .. "
        f"{datetime.fromtimestamp(games[-1][0], tz):%b %d %I:%M %p}; "
        f"after E58 fix {sum(at >= E58 for at, _, _ in rows)}; replay not fully ok "
        f"{sum(not f['ok'] for _, _, f in rows)}"
    )
    first = [r for r in rows if r[2]["first"]]
    print(f"bot moves first: {wilson(len(first), len(rows))}")
    for label, sel in (
        ("first seat", first),
        ("second seat", [r for r in rows if not r[2]["first"]]),
    ):
        print(f"{label} win rate: {wilson(sum(w for _, w, _ in sel), len(sel))}")
    pre = [r for r in rows if r[0] < E58]
    for label, sel in (
        ("first seat", [r for r in pre if r[2]["first"]]),
        ("second seat", [r for r in pre if not r[2]["first"]]),
    ):
        print(
            f"  before E58 fix, {label}: {wilson(sum(w for _, w, _ in sel), len(sel))}"
        )
    post = [
        (at, cap, won, cache[cap])
        for at, a, cap, won in games
        if cache.get(cap) and at >= E58
    ]
    for label, sel in (
        ("first seat", [r for r in post if r[3]["first"]]),
        ("second seat", [r for r in post if not r[3]["first"]]),
    ):
        print(f"  after E58 fix, {label}: {wilson(sum(r[2] for r in sel), len(sel))}")
    print("\nafter E58, by account and label: first seat | second seat")
    keys = sorted({LABEL[cap] for _, cap, _, _ in post})
    for key in keys:
        sel = [r for r in post if LABEL[r[1]] == key]
        f1 = [r for r in sel if r[3]["first"]]
        f2 = [r for r in sel if not r[3]["first"]]
        print(
            f"  {key[0]:5} {key[1][:40]:40} {wilson(sum(r[2] for r in f1), len(f1)):>24} | "
            f"{wilson(sum(r[2] for r in f2), len(f2))}"
        )

    groups = {
        "1st W": [f for _, w, f in rows if f["first"] and w],
        "1st L": [f for _, w, f in rows if f["first"] and not w],
        "2nd W": [f for _, w, f in rows if not f["first"] and w],
        "2nd L": [f for _, w, f in rows if not f["first"] and not w],
    }
    print(
        "\nmeans [95% bootstrap], end of game; roads/settlements exclude the opening two"
    )
    print(
        "| measure | "
        + " | ".join(f"{k} (n {len(v)})" for k, v in groups.items())
        + " |"
    )
    print("|---|" + "---|" * len(groups))
    for side in ("bot", "opp"):
        for m in ("roads", "settlements", "cities", "devs", "vp"):
            print(
                f"| {side} {m} | "
                + " | ".join(
                    boot([f["end"][side][m] for f in v]) for v in groups.values()
                )
                + " |"
            )
    for side in ("bot", "opp"):
        for m, name in (("lr", "holds Longest Road"), ("la", "holds Largest Army")):
            print(
                f"| {side} {name} | "
                + " | ".join(
                    wilson(sum(f["end"][side][m] for f in v), len(v))
                    for v in groups.values()
                )
                + " |"
            )
    print(
        "| turns | "
        + " | ".join(boot([f["turns"] for f in v]) for v in groups.values())
        + " |"
    )
    print(
        "| 90+ turns | "
        + " | ".join(
            wilson(sum(f["turns"] >= 90 for f in v), len(v)) for v in groups.values()
        )
        + " |"
    )
    print(
        "| resigned/left | "
        + " | ".join(
            wilson(sum(f["resigned"] for f in v), len(v)) for v in groups.values()
        )
        + " |"
    )
    print(
        "| ever behind | "
        + " | ".join(
            wilson(sum(f["first_behind"] is not None for f in v), len(v))
            for v in groups.values()
        )
        + " |"
    )
    print(
        "| first turn behind | "
        + " | ".join(boot([f["first_behind"] for f in v]) for v in groups.values())
        + " |"
    )
    print(
        "| behind for good from turn | "
        + " | ".join(boot([f["behind_for_good"] for f in v]) for v in groups.values())
        + " |"
    )
    print(
        "| bot roads minus opp roads | "
        + " | ".join(
            boot([f["end"]["bot"]["roads"] - f["end"]["opp"]["roads"] for f in v])
            for v in groups.values()
        )
        + " |"
    )
    print(
        "| bot settl. minus opp settl. | "
        + " | ".join(
            boot(
                [
                    f["end"]["bot"]["settlements"] - f["end"]["opp"]["settlements"]
                    for f in v
                ]
            )
            for v in groups.values()
        )
        + " |"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
