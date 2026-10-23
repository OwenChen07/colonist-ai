"""What unplayed dev cards do people hold from turn 60 on? At the bot's first
decision of each of its own turns (turn >= 60), the person's hand by card type
from the replay (cards later played are known; unplayed VP cards come from the
end-of-game summary; other never-played cards are filled in by
resolve_hidden_draws, counted apart as "unplayed_other"). For comparison with
training's self-play numbers (2.41 held: 1.61 VP, 0.62 knight, 0.08 YoP,
0.08 monopoly, 0.10 road building). Game-clustered bootstrap. Aggregates only."""

import json
import os
import random
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.state import DEVELOPMENT_CARDS

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600
T0 = 60
ACTION = [c for c in DEVELOPMENT_CARDS if c != "VICTORY_POINT"]


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
        if s.is_initial_build_phase:
            return
        idx = {c.value: i for c, i in s.color_to_index.items()}
        ti = idx[self.them]
        self.final = {
            c: s.player_state[f"P{ti}_{c}_IN_HAND"] for c in DEVELOPMENT_CARDS
        }
        if s.num_turns < T0 or s.num_turns in self.seen:
            return
        dc = getattr(decision.color, "value", decision.color)
        own_turn = getattr(s.colors[s.current_turn_index], "value", None) == self.you
        if dc != self.you or not own_turn:
            return
        self.seen.add(s.num_turns)
        p = s.player_state
        row = {c: p[f"P{ti}_{c}_IN_HAND"] for c in DEVELOPMENT_CARDS}
        row["turn"] = s.num_turns
        row["army"] = p[f"P{ti}_HAS_ARMY"]
        self.rows.append(row)

    def after(self, game):
        pass


def unplayed_other(record, them):
    """Never-played non-VP cards the person bought (assigned by the resolver)."""
    from catanatron.colonist.record import resolve_hidden_draws

    log = resolve_hidden_draws(record)
    bought, played = 0, 0
    for action, _ in log:
        if action[0] != them:
            continue
        if action[1] == "BUY_DEVELOPMENT_CARD":
            bought += 1
        elif action[1].startswith("PLAY_"):
            played += 1
    vp = record.get("hidden_vp_cards", {}).get(them, 0)
    return bought - played - vp


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
    obs.unplayed_other = unplayed_other(record, them)
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
        f"{sum(flat) / len(flat):.3f} [{vals[int(0.025 * reps)]:.3f}, "
        f"{vals[int(0.975 * reps) - 1]:.3f}] (n {len(flat)}, g {len(groups)})"
    )


def report(name, games):
    print(f"\n{name}:")
    print(f"  total   {ci(games, lambda r: sum(r[c] for c in DEVELOPMENT_CARDS))}")
    print(f"  action  {ci(games, lambda r: sum(r[c] for c in ACTION))}")
    for c in DEVELOPMENT_CARDS:
        print(f"  {c:15s} {ci(games, lambda r: r[c])}")
    print(
        f"  any action held: {ci(games, lambda r: float(sum(r[c] for c in ACTION) > 0))}"
    )


def main():
    with open("data/human/index.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = {r["capture"]: r for r in rows if r.get("status") == "ok" and "bot_won" in r}
    caps = sorted(rows)[-LAST:]
    games, skipped, other = [], 0, []
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
        for x in o.rows:
            x["bot_won"] = r["bot_won"]
        games.append(o.rows)
        if o.rows:
            other.append(o.unplayed_other)
    print(
        f"games {len(games)}, skipped {skipped}, with turn>={T0}: {sum(1 for g in games if g)}"
    )
    if other:
        print(
            f"never-played non-VP cards per late game (resolver-assigned): "
            f"mean {sum(other) / len(other):.2f}, games with any {sum(o > 0 for o in other)}"
        )
    report("all turn>=60", games)
    report("person holds army", [[r for r in g if r["army"]] for g in games])
    report("bot lost", [g for g in games if g and not g[0]["bot_won"]])
    report("bot won", [g for g in games if g and g[0]["bot_won"]])
    with open("data/orchestrator/live-loss/dev_mix_rows.json", "w") as f:
        json.dump(games, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
