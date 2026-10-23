"""Monopoly: how much do people's monopolies take from the bot, versus the
bot's monopolies from people? And is the bot's hand more exposed (cards of one
resource) while the person holds an unplayed monopoly? Replays the last N
indexed games. Game-clustered bootstrap. Aggregates only."""

import json
import os
import random
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.models.enums import RESOURCES

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600


def val(x):
    return getattr(x, "value", x)


class Obs:
    def __init__(self, you, them):
        self.you, self.them = you, them
        self.monos = []  # (player, taken, victim_hand_total, turn)
        self.expo = {"bot": [], "person": []}  # max-one-resource of holder's victim
        self.seen = set()

    def before(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if s.is_initial_build_phase:
            return
        idx = {val(c): i for c, i in s.color_to_index.items()}
        p = s.player_state
        dc = val(decision.color)
        at = val(decision.action_type)
        if at == "PLAY_MONOPOLY":
            victim = self.them if dc == self.you else self.you
            vi = idx[victim]
            hand = {r: p[f"P{vi}_{r}_IN_HAND"] for r in RESOURCES}
            who = "bot" if dc == self.you else "person"
            self.monos.append(
                (
                    who,
                    hand[val(decision.value)],
                    sum(hand.values()),
                    max(hand.values()),
                    s.num_turns,
                )
            )
        # exposure: at the first decision of each turn, the player NOT on turn
        # is the possible victim; record the max single-resource count of the
        # victim when the player on turn holds an unplayed monopoly.
        cur = val(s.colors[s.current_turn_index])
        if s.num_turns in self.seen:
            return
        self.seen.add(s.num_turns)
        ci_, vi = idx[cur], idx[self.them if cur == self.you else self.you]
        holder = "bot" if cur == self.you else "person"
        hand = [p[f"P{vi}_{r}_IN_HAND"] for r in RESOURCES]
        key = holder if p[f"P{ci_}_MONOPOLY_IN_HAND"] > 0 else holder + "_none"
        self.expo.setdefault(key, []).append((max(hand), sum(hand)))

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


def ci(groups, f, reps=1000, seed=0):
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
        f"{sum(flat) / len(flat):.2f} [{vals[int(0.025 * reps)]:.2f}, "
        f"{vals[int(0.975 * reps) - 1]:.2f}] (n {len(flat)}, g {len(groups)})"
    )


def main():
    with open("data/human/index.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = {r["capture"]: r for r in rows if r.get("status") == "ok" and "bot_won" in r}
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
        o.bot_won = r["bot_won"]
        games.append(o)
    print(f"games {len(games)}, skipped {skipped}")
    for who in ("person", "bot"):
        g = [[m for m in o.monos if m[0] == who] for o in games]
        n_games = sum(1 for x in g if x)
        print(f"\n{who} monopolies: {sum(len(x) for x in g)} plays in {n_games} games")
        print(f"  cards taken      {ci(g, lambda m: m[1])}")
        print(f"  share of victim's hand {ci(g, lambda m: m[1] / max(m[2], 1))}")
        print(f"  taken / victim's max-one {ci(g, lambda m: m[1] / max(m[3], 1))}")
        print(f"  4+ cards         {ci(g, lambda m: float(m[1] >= 4))}")
        print(f"  turn             {ci(g, lambda m: m[4])}")
        for key in (who, who + "_none"):
            e = [o.expo.get(key, []) for o in games]
            print(
                f"  victim at {key} turns: max one resource {ci(e, lambda x: x[0])}; "
                f"hand {ci(e, lambda x: x[1])}; max/hand {ci(e, lambda x: x[0] / max(x[1], 1))}"
            )
    # outcome by the person's monopoly haul
    buckets = {"no person mono": [], "person took 0-3": [], "person took 4+": []}
    for o in games:
        pm = [m[1] for m in o.monos if m[0] == "person"]
        k = (
            "no person mono"
            if not pm
            else ("person took 4+" if max(pm) >= 4 else "person took 0-3")
        )
        buckets[k].append([float(o.bot_won)])
    print()
    for k, g in buckets.items():
        print(f"bot win rate, {k}: {ci(g, lambda x: x)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
