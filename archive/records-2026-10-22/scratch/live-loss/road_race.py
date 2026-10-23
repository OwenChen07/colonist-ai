"""Road race in live games: how close was the bot when the person took longest
road first, did the bot ever take it back, and did it spend wood+brick on roads
in the 10 turns before the claim. Aggregates only, no names."""

import json
import os
import sys
from collections import Counter, defaultdict

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.loss_report import wilson
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600


class Obs:
    def __init__(self, you, them):
        self.you, self.them = you, them
        self.first = None  # (holder, turn, holder_len, other_len)
        self.holders = []
        self.turns = {}  # turn -> dict(bot_wb, bot_road_builds, lens)
        self.final = None

    def before(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        idx = {c.value: i for c, i in s.color_to_index.items()}
        p = s.player_state
        yi, ti = idx[self.you], idx[self.them]
        t = s.num_turns
        rec = self.turns.setdefault(
            t, {"wb": False, "builds": 0, "bot_turn": False, "lens": None}
        )
        rec["lens"] = (p[f"P{yi}_LONGEST_ROAD_LENGTH"], p[f"P{ti}_LONGEST_ROAD_LENGTH"])
        dc = getattr(decision.color, "value", decision.color)
        if dc == self.you:
            rec["bot_turn"] = True
            if p[f"P{yi}_WOOD_IN_HAND"] >= 1 and p[f"P{yi}_BRICK_IN_HAND"] >= 1:
                rec["wb"] = True
            if decision.action_type == ActionType.BUILD_ROAD:
                rec["builds"] += 1
        holder = None
        if p[f"P{yi}_HAS_ROAD"]:
            holder = "bot"
        elif p[f"P{ti}_HAS_ROAD"]:
            holder = "person"
        if holder and (not self.holders or self.holders[-1] != holder):
            self.holders.append(holder)
            if self.first is None:
                ly, lt = rec["lens"]
                pair = (lt, ly) if holder == "person" else (ly, lt)
                self.first = (holder, t, *pair)

    def after(self, game):
        self.final = game


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
    pf = [(r, o) for r, o in games if o.first and o.first[0] == "person"]
    bf = [(r, o) for r, o in games if o.first and o.first[0] == "bot"]
    none = [(r, o) for r, o in games if not o.first]
    for label, sel in (("person first", pf), ("bot first", bf), ("nobody", none)):
        print(
            f"{label}: n={len(sel)} bot wins {wilson(sum(r['bot_won'] for r, _ in sel), len(sel))}"
        )
    print("\nperson took LR first: bot's road length at the claim (gap = 5+ minus bot)")
    by_gap = defaultdict(list)
    for r, o in pf:
        gap = o.first[2] - o.first[3]
        key = "0-1" if gap <= 1 else "2" if gap == 2 else "3+"
        by_gap[key].append(r["bot_won"])
    for key in ("0-1", "2", "3+"):
        v = by_gap[key]
        print(
            f"  gap {key}: share {wilson(len(v), len(pf))}, bot wins {wilson(sum(v), len(v))}"
        )
    dist = Counter(o.first[3] for _, o in pf)
    print("  bot length at claim:", dict(sorted(dist.items())))
    retake = [o for _, o in pf if "bot" in o.holders]
    print(f"  bot later held LR: {wilson(len(retake), len(pf))}")
    rw = [r["bot_won"] for r, o in pf if "bot" in o.holders]
    print(f"  bot wins when it retook: {wilson(sum(rw), len(rw))}")
    lost_back = [o for _, o in bf if "person" in o.holders]
    print(f"bot took LR first, person later took it: {wilson(len(lost_back), len(bf))}")

    print("\n10 turns before the claim, bot turns with wood+brick in hand:")
    for label, sel in (("person first", pf), ("bot first", bf)):
        wb_turns = bot_turns = wb_road = 0
        for _, o in sel:
            t0 = o.first[1]
            for t, rec in o.turns.items():
                if t0 - 10 <= t < t0 and rec["bot_turn"]:
                    bot_turns += 1
                    if rec["wb"]:
                        wb_turns += 1
                        wb_road += rec["builds"] > 0
        print(
            f"  {label}: bot turns {bot_turns}, with wood+brick {wilson(wb_turns, bot_turns)},"
            f" built a road on those {wilson(wb_road, wb_turns)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
