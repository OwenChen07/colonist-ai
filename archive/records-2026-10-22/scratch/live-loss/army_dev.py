"""The army+dev overconfidence pattern (late_bias.py): from turn 60, the bot's
value v >= 0.5 while the person holds largest army and >= 1 unplayed dev card.
Trigger = the first such own-turn decision of the bot. From there on: what the
person plays (knight / monopoly / year of plenty / road building), what the bot
does (knights, dev buys, taking army back), how the person's final VP is made,
and at each bot turn the human model's vs the bot net's probability on
BUY_DEVELOPMENT_CARD and PLAY_KNIGHT_CARD where legal. Lost vs won games.
Aggregates only, no names. Positions -> live-loss/army_dev_positions.json."""

import json
import os
import random
import sys

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.net import Net
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType
from catanatron.state import DEVELOPMENT_CARDS

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600
T0 = 60
HUMAN = Net.load("models/human/net.npz")
BOT = Net.load("runs/g1/best.npz")
PLAYS = {
    ActionType.PLAY_KNIGHT_CARD: "knight",
    ActionType.PLAY_MONOPOLY: "monopoly",
    ActionType.PLAY_YEAR_OF_PLENTY: "yop",
    ActionType.PLAY_ROAD_BUILDING: "roadb",
    ActionType.BUY_DEVELOPMENT_CARD: "buy",
}
JUDGE = {ActionType.BUY_DEVELOPMENT_CARD: "buy", ActionType.PLAY_KNIGHT_CARD: "knight"}


def probs(net, game, color, actions):
    legal = E.legal_indices(actions)
    static, dynamic = E.encode(game, color)
    logits, v = net.forward(static, dynamic[None, :])
    x = logits[0, legal].astype(np.float64)
    p = np.exp(x - x.max())
    p /= p.sum()
    return p, float(v.reshape(-1)[0])


class Obs:
    def __init__(self, you, them):
        self.you, self.them = you, them
        self.seen = set()
        self.trig = None
        self.i = -1
        self.person = {}
        self.bot = {}
        self.judged = {}  # (turn, kind) -> row
        self.army_back = None
        self.final = None

    def before(self, game):
        pass

    def step(self, game, decision):
        self.i += 1
        s = game.state
        if s.is_initial_build_phase or s.num_turns < T0:
            return
        idx = {c.value: i for c, i in s.color_to_index.items()}
        col = {c.value: c for c in s.color_to_index}
        p = s.player_state
        yi, ti = idx[self.you], idx[self.them]
        t = s.num_turns
        dc = getattr(decision.color, "value", decision.color)
        own_turn = getattr(s.colors[s.current_turn_index], "value", None) == self.you
        mine = dc == self.you and own_turn
        if self.trig is None:
            if not mine or t in self.seen:
                return
            self.seen.add(t)
            if not p[f"P{ti}_HAS_ARMY"]:
                return
            opp_dev = sum(p[f"P{ti}_{c}_IN_HAND"] for c in DEVELOPMENT_CARDS)
            if opp_dev < 1:
                return
            static, dynamic = E.encode(game, col[self.you])
            _, v = BOT.forward(static, dynamic[None, :])
            v = float(v.reshape(-1)[0])
            if v < 0.5:
                return
            self.trig = {
                "turn": t,
                "entry": self.i,
                "v": round(v, 3),
                "my_vp": p[f"P{yi}_ACTUAL_VICTORY_POINTS"],
                "opp_vp": p[f"P{ti}_ACTUAL_VICTORY_POINTS"],
                "opp_vp_vis": p[f"P{ti}_VICTORY_POINTS"],
                "opp_hand": {c: p[f"P{ti}_{c}_IN_HAND"] for c in DEVELOPMENT_CARDS},
                "my_hand": {c: p[f"P{yi}_{c}_IN_HAND"] for c in DEVELOPMENT_CARDS},
                "my_knights": p[f"P{yi}_PLAYED_KNIGHT"],
                "opp_knights": p[f"P{ti}_PLAYED_KNIGHT"],
                "my_road": p[f"P{yi}_HAS_ROAD"],
                "opp_road": p[f"P{ti}_HAS_ROAD"],
            }
        kind = PLAYS.get(decision.action_type)
        if kind and dc == self.them:
            self.person[kind] = self.person.get(kind, 0) + 1
        if kind and dc == self.you:
            self.bot[kind] = self.bot.get(kind, 0) + 1
        if self.army_back is None and p[f"P{yi}_HAS_ARMY"]:
            self.army_back = t
        if mine:
            acts = game.playable_actions
            for at, name in JUDGE.items():
                if (t, name) in self.judged or not any(
                    a.action_type == at for a in acts
                ):
                    continue
                ph, _ = probs(HUMAN, game, col[self.you], acts)
                pb, vb = probs(BOT, game, col[self.you], acts)
                m = [a.action_type == at for a in acts]
                self.judged[(t, name)] = {
                    "turn": t,
                    "kind": name,
                    "entry": self.i,
                    "v": round(vb, 3),
                    "p_human": round(float(ph[m].sum()), 4),
                    "p_bot": round(float(pb[m].sum()), 4),
                    "took": int(decision.action_type == at),
                    "n_legal": len(acts),
                }
        self.final = {
            "turn": t,
            "opp_vp": p[f"P{ti}_ACTUAL_VICTORY_POINTS"],
            "opp_vpcards": p[f"P{ti}_VICTORY_POINT_IN_HAND"],
            "opp_army": p[f"P{ti}_HAS_ARMY"],
            "opp_road": p[f"P{ti}_HAS_ROAD"],
            "opp_cities": 4 - p[f"P{ti}_CITIES_AVAILABLE"],
            "my_vp": p[f"P{yi}_ACTUAL_VICTORY_POINTS"],
            "my_army": p[f"P{yi}_HAS_ARMY"],
            "my_knights": p[f"P{yi}_PLAYED_KNIGHT"],
            "opp_knights": p[f"P{ti}_PLAYED_KNIGHT"],
        }

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


def mean_ci(groups, reps=2000, seed=0):
    groups = [g for g in groups if g]
    flat = [v for g in groups for v in g]
    if not flat:
        return "n/a"
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        s = [v for _ in groups for v in groups[rng.randrange(len(groups))]]
        vals.append(sum(s) / len(s))
    vals.sort()
    return (
        f"{sum(flat) / len(flat):.3f} [{vals[int(0.025 * reps)]:.3f}, "
        f"{vals[int(0.975 * reps) - 1]:.3f}] (n {len(flat)}, g {len(groups)})"
    )


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
        if o.trig:
            games.append((path, bool(r["bot_won"]), o))
    print(f"scanned {len(caps)}, skipped {skipped}, triggered {len(games)}")
    out = []
    for won in (False, True):
        sel = [o for _, w, o in games if w == won]
        print(f"\n=== bot {'won' if won else 'lost'}: games {len(sel)}")
        if not sel:
            continue
        print(f"  trigger turn {mean_ci([[o.trig['turn']] for o in sel])}")
        print(f"  v {mean_ci([[o.trig['v']] for o in sel])}")
        print(
            f"  my_vp {mean_ci([[o.trig['my_vp']] for o in sel])}  opp_vp(true) "
            f"{mean_ci([[o.trig['opp_vp']] for o in sel])}"
        )
        print(
            f"  opp dev held {mean_ci([[sum(o.trig['opp_hand'].values())] for o in sel])}"
        )
        for c in DEVELOPMENT_CARDS:
            print(f"    opp {c} {mean_ci([[o.trig['opp_hand'][c]] for o in sel])}")
        print(
            f"  my dev held {mean_ci([[sum(o.trig['my_hand'].values())] for o in sel])}"
        )
        print(
            f"  knights played me {mean_ci([[o.trig['my_knights']] for o in sel])}  "
            f"opp {mean_ci([[o.trig['opp_knights']] for o in sel])}"
        )
        print(
            f"  turns to end {mean_ci([[o.final['turn'] - o.trig['turn']] for o in sel])}"
        )
        for k in ("knight", "monopoly", "yop", "roadb", "buy"):
            print(
                f"  after: person {k} {mean_ci([[o.person.get(k, 0)] for o in sel])}  "
                f"bot {mean_ci([[o.bot.get(k, 0)] for o in sel])}"
            )
        print(
            f"  bot took army back {mean_ci([[float(o.army_back is not None)] for o in sel])}"
        )
        print(
            f"  final opp vp-cards {mean_ci([[o.final['opp_vpcards']] for o in sel])}  "
            f"opp army {mean_ci([[float(o.final['opp_army'])] for o in sel])}  "
            f"opp road {mean_ci([[float(o.final['opp_road'])] for o in sel])}"
        )
        for kind in ("buy", "knight"):
            g = [[j for j in o.judged.values() if j["kind"] == kind] for o in sel]
            print(
                f"  judge {kind}: P_human {mean_ci([[j['p_human'] for j in x] for x in g])}"
            )
            print(f"           P_bot   {mean_ci([[j['p_bot'] for j in x] for x in g])}")
            print(
                f"           h - b   {mean_ci([[j['p_human'] - j['p_bot'] for j in x] for x in g])}"
            )
            print(f"           took    {mean_ci([[j['took'] for j in x] for x in g])}")
    for path, won, o in games:
        out.append(
            {
                "capture": path,
                "bot_won": won,
                "trigger": o.trig,
                "final": o.final,
                "person": o.person,
                "bot": o.bot,
                "army_back": o.army_back,
                "judged": list(o.judged.values()),
            }
        )
    os.makedirs("data/orchestrator/live-loss", exist_ok=True)
    with open("data/orchestrator/live-loss/army_dev_positions.json", "w") as f:
        json.dump(out, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
