"""Road race follow-up: in the 10 turns before the first longest-road claim, the
bot's wood+brick turns with a game-clustered bootstrap CI, split by whether a
settlement was buildable, and what the bot built instead of a road.
Aggregates only, no names."""

import json
import os
import random
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600
WINDOW = 10
BUILDS = {
    ActionType.BUILD_ROAD: "road",
    ActionType.BUILD_SETTLEMENT: "settle",
    ActionType.BUILD_CITY: "city",
    ActionType.BUY_DEVELOPMENT_CARD: "dev",
}


class Obs:
    def __init__(self, you, them):
        self.you, self.them = you, them
        self.first = None  # (holder, turn)
        self.turns = {}

    def before(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase:
            return
        idx = {c.value: i for c, i in s.color_to_index.items()}
        col = {c.value: c for c in s.color_to_index}
        p = s.player_state
        yi, ti = idx[self.you], idx[self.them]
        t = s.num_turns
        rec = self.turns.setdefault(
            t,
            {
                "bot": False,
                "wb": False,
                "settle_ok": False,
                "built": set(),
                "hand": 0,
                "edges": 0,
                "roads_left": 0,
                "end_hand": 0,
            },
        )
        dc = getattr(decision.color, "value", decision.color)
        own_turn = getattr(s.colors[s.current_turn_index], "value", None) == self.you
        if dc == self.you and own_turn:
            rec["bot"] = True
            h = {
                r: p[f"P{yi}_{r}_IN_HAND"] for r in ("WOOD", "BRICK", "SHEEP", "WHEAT")
            }
            size = sum(
                p[f"P{yi}_{r}_IN_HAND"]
                for r in ("WOOD", "BRICK", "SHEEP", "WHEAT", "ORE")
            )
            rec["end_hand"] = size
            if h["WOOD"] >= 1 and h["BRICK"] >= 1:
                if not rec["wb"]:
                    rec["hand"] = size
                    rec["roads_left"] = p[f"P{yi}_ROADS_AVAILABLE"]
                    rec["edges"] = len(s.board.buildable_edges(col[self.you]))
                rec["wb"] = True
                if (
                    h["SHEEP"] >= 1
                    and h["WHEAT"] >= 1
                    and p[f"P{yi}_SETTLEMENTS_AVAILABLE"] > 0
                    and s.board.buildable_node_ids(col[self.you])
                ):
                    rec["settle_ok"] = True
            kind = BUILDS.get(decision.action_type)
            if kind:
                rec["built"].add(kind)
        if self.first is None:
            if p[f"P{yi}_HAS_ROAD"]:
                self.first = ("bot", t)
            elif p[f"P{ti}_HAS_ROAD"]:
                self.first = ("person", t)

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
    if not replay(record, observer=obs).ok:
        return None
    return obs


def ratio_ci(pairs, reps=2000, seed=0):
    """pairs: per-game (num, den). Ratio of sums with a game-clustered bootstrap."""
    num = sum(a for a, _ in pairs)
    den = sum(b for _, b in pairs)
    if not den:
        return "n/a"
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        d = sum(b for _, b in sample)
        if d:
            vals.append(sum(a for a, _ in sample) / d)
    vals.sort()
    lo, hi = vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]
    return f"{num}/{den} = {num / den:.1%} [{lo:.1%}, {hi:.1%}] (games {sum(1 for _, b in pairs if b)})"


def diff_ci(a_pairs, b_pairs, reps=2000, seed=1):
    rng = random.Random(seed)

    def r(ps):
        d = sum(b for _, b in ps)
        return sum(a for a, _ in ps) / d if d else 0.0

    base = r(a_pairs) - r(b_pairs)
    vals = sorted(
        r([a_pairs[rng.randrange(len(a_pairs))] for _ in a_pairs])
        - r([b_pairs[rng.randrange(len(b_pairs))] for _ in b_pairs])
        for _ in range(reps)
    )
    return f"{base:+.1%} [{vals[int(0.025 * reps)]:+.1%}, {vals[int(0.975 * reps) - 1]:+.1%}]"


def main():
    with open("data/human/index.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = {r["capture"]: r for r in rows if r.get("status") == "ok"}
    caps = sorted(rows)[-LAST:]
    games, skipped = [], 0
    for c in caps:
        r = rows[c]
        try:
            o = facts(os.path.join("captures", r["account"], c))
        except (OSError, ValueError, KeyError, IndexError, AttributeError):
            o = None
        if o is None:
            skipped += 1
            continue
        games.append((r, o))
    print(f"games {len(games)}, skipped {skipped}")
    groups = {
        "person first": [o for _, o in games if o.first and o.first[0] == "person"],
        "bot first": [o for _, o in games if o.first and o.first[0] == "bot"],
    }
    road = {}
    for label, sel in groups.items():
        print(f"\n{label}: games {len(sel)}")
        per = {
            k: [] for k in ("wb", "road|wb", "road|wb,settle_ok", "road|wb,no_settle")
        }
        instead = {"settle": 0, "city": 0, "dev": 0, "nothing": 0}
        for o in sel:
            t0 = o.first[1]
            bt = [
                rec
                for t, rec in o.turns.items()
                if t0 - WINDOW <= t < t0 and rec["bot"]
            ]
            wb = [rec for rec in bt if rec["wb"]]
            so = [rec for rec in wb if rec["settle_ok"]]
            ns = [rec for rec in wb if not rec["settle_ok"]]
            built = lambda recs: sum(
                "road" in rec["built"] for rec in recs
            )  # noqa: E731
            per["wb"].append((len(wb), len(bt)))
            per["road|wb"].append((built(wb), len(wb)))
            per["road|wb,settle_ok"].append((built(so), len(so)))
            per["road|wb,no_settle"].append((built(ns), len(ns)))
            for rec in wb:
                if "road" in rec["built"]:
                    continue
                other = [k for k in ("settle", "city", "dev") if k in rec["built"]]
                instead[other[0] if other else "nothing"] += 1
        for k, v in per.items():
            print(f"  {k}: {ratio_ci(v)}")
        print(f"  wb turns without a road, built instead: {instead}")
        # Why nothing? On wb, no-settlement turns: road impossible (no pieces or
        # no edge), discard risk (hand >= 8 at the end), and hand size.
        why = {k: [] for k in ("no_edge|ns", "no_pieces|ns", "end_hand>=8|ns_nothing")}
        hands = {"road": [], "nothing": []}
        for o in sel:
            t0 = o.first[1]
            ns = [
                rec
                for t, rec in o.turns.items()
                if t0 - WINDOW <= t < t0
                and rec["bot"]
                and rec["wb"]
                and not rec["settle_ok"]
            ]
            nothing = [
                r for r in ns if not (r["built"] & {"road", "settle", "city", "dev"})
            ]
            why["no_edge|ns"].append((sum(r["edges"] == 0 for r in ns), len(ns)))
            why["no_pieces|ns"].append((sum(r["roads_left"] == 0 for r in ns), len(ns)))
            why["end_hand>=8|ns_nothing"].append(
                (sum(r["end_hand"] >= 8 for r in nothing), len(nothing))
            )
            for r in ns:
                if "road" in r["built"]:
                    hands["road"].append(r["hand"])
                elif r in nothing:
                    hands["nothing"].append(r["hand"])
        for k, v in why.items():
            print(f"  {k}: {ratio_ci(v)}")
        for k, v in hands.items():
            if v:
                print(
                    f"  hand size at wb, {k}: mean {sum(v) / len(v):.2f} (n {len(v)})"
                )
        road[label + " why"] = why
        road[label] = per
    print("\nperson-first minus bot-first, road on wood+brick turns:")
    for k in ("road|wb", "road|wb,no_settle"):
        print(f"  {k}: {diff_ci(road['person first'][k], road['bot first'][k])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
