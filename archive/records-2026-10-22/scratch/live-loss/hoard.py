"""Hoarding: turns a player ends with 8+ cards while a build was legal, bot vs
person, and how often a discard followed before their next turn.
Game-clustered bootstrap CIs. Aggregates only, no names."""

import json
import os
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType

sys.path.insert(0, os.path.dirname(__file__))
from road_race2 import diff_ci, ratio_ci  # noqa: E402

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600
RES = ("WOOD", "BRICK", "SHEEP", "WHEAT", "ORE")
BUILDS = {
    ActionType.BUILD_ROAD: "road",
    ActionType.BUILD_SETTLEMENT: "settle",
    ActionType.BUILD_CITY: "city",
    ActionType.BUY_DEVELOPMENT_CARD: "dev",
}


class Obs:
    def __init__(self, you, them):
        self.who = {you: "bot", them: "person"}
        self.c = {
            k: {"ends": 0, "ends8": 0, "ends8_build": 0, "disc_after8": 0, "disc": 0}
            for k in ("bot", "person")
        }
        self.legal = {k: {} for k in ("bot", "person")}
        self.pending = {}  # player -> True if ended last turn with 8+

    def before(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase:
            return
        dc = getattr(decision.color, "value", decision.color)
        who = self.who.get(dc)
        if who is None:
            return
        c = self.c[who]
        if decision.action_type == ActionType.DISCARD_RESOURCE:
            key = (who, s.num_turns)
            if key != getattr(self, "_last_disc", None):
                self._last_disc = key
                c["disc"] += 1
                if self.pending.get(who):
                    c["disc_after8"] += 1
            return
        turn_color = getattr(s.colors[s.current_turn_index], "value", None)
        if turn_color != dc or decision.action_type != ActionType.END_TURN:
            return
        i = {k.value: v for k, v in s.color_to_index.items()}[dc]
        size = sum(s.player_state[f"P{i}_{r}_IN_HAND"] for r in RES)
        c["ends"] += 1
        self.pending[who] = size > s.discard_limit
        if size > s.discard_limit:
            c["ends8"] += 1
            legal = {
                BUILDS[a.action_type]
                for a in game.playable_actions
                if a.action_type in BUILDS
            }
            if legal:
                c["ends8_build"] += 1
                for k in legal:
                    self.legal[who][k] = self.legal[who].get(k, 0) + 1

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
        if os.environ.get("HOARD_DEBUG") or LAST <= 3:
            print("replay:", rep.errors[:1])
        return None
    return obs


def main():
    with open("data/human/index.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = {r["capture"]: r for r in rows if r.get("status") == "ok"}
    caps = sorted(rows)[-LAST:]
    games = []
    for cap in caps:
        r = rows[cap]
        try:
            o = facts(os.path.join("captures", r["account"], cap))
        except (OSError, ValueError, KeyError, IndexError, AttributeError) as e:
            if not games and cap == caps[0]:
                print("first error:", repr(e))
            o = None
        if o is not None:
            games.append(o)
    print(f"games {len(games)} of {len(caps)}")
    pairs = {}
    for who in ("bot", "person"):
        cs = [o.c[who] for o in games]
        pairs[who] = {
            "ends8/ends": [(c["ends8"], c["ends"]) for c in cs],
            "ends8_build/ends": [(c["ends8_build"], c["ends"]) for c in cs],
            "build_legal/ends8": [(c["ends8_build"], c["ends8"]) for c in cs],
            "discard_after/ends8": [(c["disc_after8"], c["ends8"]) for c in cs],
        }
        print(f"\n{who}: discards total {sum(c['disc'] for c in cs)}")
        for k, v in pairs[who].items():
            print(f"  {k}: {ratio_ci(v)}")
        tot = {}
        for o in games:
            for k, n in o.legal[who].items():
                tot[k] = tot.get(k, 0) + n
        print(f"  legal builds on 8+ ends: {tot}")
    print("\nbot minus person:")
    for k in ("ends8/ends", "ends8_build/ends"):
        print(f"  {k}: {diff_ci(pairs['bot'][k], pairs['person'][k])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
