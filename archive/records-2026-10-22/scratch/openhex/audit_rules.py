"""Audit item 2: rules seen in colonist's games against our engine's rules.

Replays N ok captures (checks on visible points each update, so a Longest
Road / Largest Army award that differs from colonist fails the replay) and
counts: rule settings, award changes and ties held, who ends the game and on
whose turn, final points, hidden VP cards, development cards bought.
Usage: audit_rules.py [N]
"""

import collections
import json
import random
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.state_functions import (
    get_largest_army,
    get_longest_road_color,
    get_longest_road_length,
    get_actual_victory_points,
)

N = int(sys.argv[1]) if len(sys.argv) > 1 else 600
rows = [json.loads(l) for l in open("data/human/index.jsonl")]
rows = [r for r in rows if r["status"] == "ok"]
random.Random(7).shuffle(rows)
rows = rows[:N]

C = collections.Counter()
final_pts = collections.Counter()
loser_pts = collections.Counter()
buys = collections.Counter()
known = collections.Counter()
hidden_vp = collections.Counter()
fails = []


class Obs:
    def before(self, game):
        self.lr = None
        self.la = None
        self.last_color = None
        self.tie_lr = False
        self.tie_la = False

    def _look(self, game):
        s = game.state
        lr = get_longest_road_color(s)
        la = get_largest_army(s)[0]
        if lr != self.lr:
            C["lr_change"] += 1
            if self.lr is not None:
                C["lr_taken_over"] += 1
        if la != self.la:
            C["la_change"] += 1
            if self.la is not None:
                C["la_taken_over"] += 1
        self.lr, self.la = lr, la
        if lr is not None:
            ls = [get_longest_road_length(s, c) for c in s.colors]
            if ls[0] == ls[1] and not self.tie_lr:
                self.tie_lr = True
                C["games_lr_tie_held"] += 1
        if la is not None:
            k = [s.player_state[f"P{i}_PLAYED_KNIGHT"] for i in range(2)]
            if k[0] == k[1] and not self.tie_la:
                self.tie_la = True
                C["games_la_tie_held"] += 1

    def step(self, game, decision):
        self._look(game)
        self.last_color = decision.color

    def after(self, game):
        self._look(game)
        self.points = {
            c: get_actual_victory_points(game.state, c) for c in game.state.colors
        }


for r in rows:
    path = f"captures/{r['account']}/{r['capture']}"
    try:
        rec = convert(load_capture(path)).record
    except Exception as e:  # noqa
        C["convert_error"] += 1
        continue
    rules = rec["rules"]
    C[
        f"rules vp={rules['vps_to_win']} discard={rules['discard_limit']} "
        f"friendly={rules['friendly_robber']} dice={rules.get('dice')}"
    ] += 1
    obs = Obs()
    rep = replay(rec, name=path, observer=obs)
    if not rep.ok:
        fails.append((path, rep.errors[:1]))
        continue
    C["replayed_ok"] += 1
    for action, result in rec["log"]:
        if action[1] == "BUY_DEVELOPMENT_CARD":
            C["dev_buys"] += 1
            if result is not None:
                known[result] += 1
    nbuy = sum(1 for a, _ in rec["log"] if a[1] == "BUY_DEVELOPMENT_CARD")
    buys[min(nbuy // 5 * 5, 25)] += 1
    for c, n in rec.get("hidden_vp_cards", {}).items():
        hidden_vp[n] += 1
    if rec.get("resigned"):
        C["forfeit" if rec.get("forfeit") else "resigned"] += 1
        continue
    if rep.winner:
        C["won_on_points"] += 1
        C[
            (
                "ended_on_winners_own_action"
                if obs.last_color.value == rep.winner
                else "ended_on_others_action"
            )
        ] += 1
        for c, p in obs.points.items():
            (final_pts if c.value == rep.winner else loser_pts)[p] += 1
    else:
        C["no_winner"] += 1

print(f"captures {len(rows)}")
for k, v in sorted(C.items()):
    print(f"{v:6d}  {k}")
print("winner's final points:", sorted(final_pts.items()))
print("loser's final points:", sorted(loser_pts.items()))
print("dev buys per game (bucket of 5):", sorted(buys.items()))
print("known draws (own seat):", dict(known))
print("hidden VP cards of the opponent at the end:", sorted(hidden_vp.items()))
print("replay failures:", len(fails))
for p, e in fails[:5]:
    print("  ", p, e)
