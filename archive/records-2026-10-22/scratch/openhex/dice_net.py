"""How much does OpenHex's plain-dice input shift round 73's outputs?

Replays every saved OpenHex game; at each of our decisions (2+ legal moves)
evaluates the net with three dice blocks:
  live    - what the client feeds: balanced model switched to 'random' at the
            first impossible roll (mode 0, deck frozen, plain odds)
  none    - dice=None (all zeros + plain odds, what evaluate --plain-dice gives)
  shadow  - a balanced model fed every roll, forcing a card of that total out
            of the deck (any pair) so it never switches: looks like training
Reports mean |value diff|, mean total-variation of priors, and how often the
net's top move changes, live vs shadow and none vs shadow. As a noise floor,
shadow vs shadow2 (a second shadow that draws the last pair of a total
instead of the first: same totals, different deck pairs).

Usage: .venv/bin/python data/orchestrator/scratch/openhex/dice_net.py
"""

import glob
import json
import statistics as st

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.net import Net
from catanatron.models.dice import BalancedDice
from catanatron.models.enums import ActionType as A
from catanatron.openhex.board import Board
from catanatron.openhex.follow import Follower, color

NET = "runs/g1/nets/round_073.npz"


def forced_observe(d, who, pair, last):
    d._sevens_include(who)
    d._reshuffle_if_needed()
    i = pair[0] + pair[1] - 2
    if d.deck[i]:
        d._draw(i, d.deck[i][-1 if last else 0] if pair not in d.deck[i] else pair)
    d._record(who, pair)


def snapshot(game, me, rows, shadows):
    s = game.state
    if s.current_color() != me or len(game.playable_actions) < 2:
        return
    legal = E.legal_indices(game.playable_actions)
    live = s.dice
    out = {}
    for name, dice in (("live", live), ("none", None)) + tuple(shadows.items()):
        s.dice = dice.copy() if dice is not None else None
        out[name] = NET_.evaluate(game, me, legal)
    s.dice = live
    rows.append((s.num_turns, out))


NET_ = Net.load(NET)
rows = []
games = 0
for path in sorted(glob.glob("data/openhex/games/*.json")):
    with open(path) as f:
        g = json.load(f)
    if not g.get("tiles"):
        continue
    fol = Follower(Board(g["tiles"], g["ports"]), g["turn_order"], g["me"], g["robber"])
    events = sorted({e["seq"]: e for e in g["events"]}.values(), key=lambda e: e["seq"])
    me = color(g["me"])
    shadows = {"shadow": BalancedDice(2), "shadow2": BalancedDice(2)}
    orig = Follower._apply

    def apply(game, action, result, _orig=orig):
        snapshot(game, me, rows, shadows)
        ok = _orig(game, action, result)
        if ok and action.action_type == A.ROLL:
            pair = tuple(int(x) for x in result)
            forced_observe(shadows["shadow"], action.color, pair, False)
            forced_observe(shadows["shadow2"], action.color, pair, True)
        return ok

    Follower._apply = staticmethod(apply)
    try:
        fol.feed(events)
        fol.settle()
        fol.replay()
        games += 1
    except Exception as e:  # noqa: BLE001
        print(path[-20:], "replay failed:", repr(e)[:100])
    finally:
        Follower._apply = staticmethod(orig)


def compare(a, b, sel=lambda t: True):
    dv, tv, flip = [], [], 0
    n = 0
    for turns, out in rows:
        if not sel(turns):
            continue
        pa, va = out[a]
        pb, vb = out[b]
        dv.append(abs(va - vb))
        tv.append(0.5 * float(np.abs(pa - pb).sum()))
        flip += int(pa.argmax() != pb.argmax())
        n += 1
    return (
        f"{a:>6} vs {b:<7} n={n:5d}  |dv| mean {st.mean(dv):.4f} p90 "
        f"{np.percentile(dv, 90):.4f} max {max(dv):.3f}  TV mean {st.mean(tv):.4f} "
        f"p90 {np.percentile(tv, 90):.4f}  top move changes {flip / n:.1%}"
    )


print(f"{games} games, {len(rows)} positions")
values = [out["live"][1] for _, out in rows]
print(f"spread of live values across positions: sd {st.pstdev(values):.3f}")
for label, sel in (
    ("all", lambda t: True),
    ("turn < 20", lambda t: t < 20),
    ("turn 20-49", lambda t: 20 <= t < 50),
    ("turn 50+", lambda t: t >= 50),
):
    print("--", label)
    for a, b in (
        ("live", "shadow"),
        ("none", "shadow"),
        ("live", "none"),
        ("shadow", "shadow2"),
    ):
        print(compare(a, b, sel))
