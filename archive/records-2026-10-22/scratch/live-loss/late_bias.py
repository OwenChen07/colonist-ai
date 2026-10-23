"""Where does the late value overconfidence sit? At the bot's first decision of
each of its own turns from turn 60 on, the bot net's value v (runs/g1/best.npz)
vs the result z (index bot_won, resignation-safe), split by what the bot can
see of the person: unplayed dev cards held, visible VP, VP gap, longest
road / largest army holder. Game-clustered bootstrap CIs. Aggregates only."""

import json
import os
import random
import sys

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.net import Net
from catanatron.colonist.replay import replay
from catanatron.state import DEVELOPMENT_CARDS

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600
T0 = 60
BOT = Net.load("runs/g1/best.npz")


class Obs:
    def __init__(self, you, them):
        self.you, self.them = you, them
        self.seen = set()
        self.rows = []
        self.final = None

    def before(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase or s.num_turns < T0 or s.num_turns in self.seen:
            return
        dc = getattr(decision.color, "value", decision.color)
        own_turn = getattr(s.colors[s.current_turn_index], "value", None) == self.you
        if dc != self.you or not own_turn:
            return
        self.seen.add(s.num_turns)
        idx = {c.value: i for c, i in s.color_to_index.items()}
        col = {c.value: c for c in s.color_to_index}
        p = s.player_state
        yi, ti = idx[self.you], idx[self.them]
        static, dynamic = E.encode(game, col[self.you])
        _, v = BOT.forward(static, dynamic[None, :])
        self.rows.append(
            {
                "turn": s.num_turns,
                "v": float(v.reshape(-1)[0]),
                "opp_dev": sum(p[f"P{ti}_{c}_IN_HAND"] for c in DEVELOPMENT_CARDS),
                "opp_vp_true": p[f"P{ti}_VICTORY_POINT_IN_HAND"],
                "opp_vp": p[f"P{ti}_VICTORY_POINTS"],
                "my_vp": p[f"P{yi}_ACTUAL_VICTORY_POINTS"],
                "opp_road": p[f"P{ti}_HAS_ROAD"],
                "opp_army": p[f"P{ti}_HAS_ARMY"],
                "my_road": p[f"P{yi}_HAS_ROAD"],
                "my_army": p[f"P{yi}_HAS_ARMY"],
            }
        )

    def after(self, game):
        pass


def facts(path):
    record = convert(load_capture(path)).record
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return None
    you = you[0]
    them = next(c for c in record["seats"] if c != you)
    obs = Obs(you, them)
    rep = replay(record, observer=obs)
    if not rep.ok:
        return None
    return obs


def ci(groups, f, reps=1000, seed=0):
    """groups: per-game lists of (v, z) rows; returns mean f over rows with a
    game-clustered bootstrap."""
    groups = [g for g in groups if g]
    flat = [f(r) for g in groups for r in g]
    if not flat:
        return "n/a"
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        s = [f(r) for _ in groups for r in groups[rng.randrange(len(groups))]]
        vals.append(sum(s) / len(s))
    vals.sort()
    return (
        f"{sum(flat) / len(flat):+.3f} [{vals[int(0.025 * reps)]:+.3f}, "
        f"{vals[int(0.975 * reps) - 1]:+.3f}] (n {len(flat)}, g {len(groups)})"
    )


SPLITS = {
    "opp_dev": lambda r: min(r["opp_dev"], 3),
    "opp_vp_true(replay)": lambda r: min(r["opp_vp_true"], 2),
    "vp_gap(me-opp vis)": lambda r: max(-2, min(r["my_vp"] - r["opp_vp"], 4)),
    "opp_road": lambda r: int(r["opp_road"]),
    "opp_army": lambda r: int(r["opp_army"]),
    "turn": lambda r: (r["turn"] >= 75) + (r["turn"] >= 90),
}


def main():
    with open("data/human/index.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = {r["capture"]: r for r in rows if r.get("status") == "ok" and "bot_won" in r}
    caps = sorted(rows)[-LAST:]
    games, skipped = [], 0
    for c in caps:
        r = rows[c]
        path = os.path.join("captures", r["account"], c)
        try:
            o = facts(path)
        except (OSError, ValueError, KeyError, IndexError, AttributeError):
            o = None
        if o is None:
            skipped += 1
            continue
        z = 1.0 if r["bot_won"] else -1.0
        for x in o.rows:
            x["z"] = z
        games.append(o.rows)
    print(
        f"games {len(games)}, skipped {skipped}, with turn>={T0}: {sum(1 for g in games if g)}"
    )
    bias = lambda r: r["v"] - r["z"]
    print(f"all: bias {ci(games, bias)}")
    for conf in (False, True):
        tag = " | v>=0.5" if conf else ""
        for name, key in SPLITS.items():
            print(f"\nby {name}{tag}:")
            for k in sorted({key(r) for g in games for r in g}):
                sub = [
                    [r for r in g if key(r) == k and (not conf or r["v"] >= 0.5)]
                    for g in games
                ]
                if sum(map(len, sub)) < 30:
                    continue
                print(
                    f"  {k}: bias {ci(sub, bias)}  v {ci(sub, lambda r: r['v'])[:6]}"
                    f"  won {ci(sub, lambda r: (r['z'] + 1) / 2)}"
                )
    with open("data/orchestrator/live-loss/late_bias_rows.json", "w") as f:
        json.dump(games, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
