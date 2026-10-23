"""Judge the bot's idle wood+brick turns before the first longest-road claim
with the human policy model (models/human/net.npz) next to the bot's own net
(runs/g1/best.npz): the probability mass each puts on BUILD_ROAD at the first
decision of the turn where a road is legal. Separates 'the bot under-values
roads' (people would road here, the bot doesn't) from 'bad position' (nobody
would). Aggregates only, no names. Candidate positions -> live-loss/road_candidates.json.
"""

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

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600
WINDOW = 10
HUMAN = Net.load("models/human/net.npz")
BOT = Net.load("runs/g1/best.npz")
BUILDS = {
    ActionType.BUILD_ROAD: "road",
    ActionType.BUILD_SETTLEMENT: "settle",
    ActionType.BUILD_CITY: "city",
    ActionType.BUY_DEVELOPMENT_CARD: "dev",
}


def road_mass(net, game, color, actions):
    legal = E.legal_indices(actions)
    static, dynamic = E.encode(game, color)
    logits, _ = net.forward(static, dynamic[None, :])
    x = logits[0, legal].astype(np.float64)
    p = np.exp(x - x.max())
    p /= p.sum()
    return float(
        sum(pi for pi, a in zip(p, actions) if a.action_type == ActionType.BUILD_ROAD)
    )


class Obs:
    def __init__(self, you, them):
        self.you, self.them = you, them
        self.first = None
        self.turns = {}
        self.i = -1

    def before(self, game):
        pass

    def step(self, game, decision):
        self.i += 1
        s = game.state
        if s.is_initial_build_phase:
            return
        idx = {c.value: i for c, i in s.color_to_index.items()}
        col = {c.value: c for c in s.color_to_index}
        p = s.player_state
        yi, ti = idx[self.you], idx[self.them]
        t = s.num_turns
        rec = self.turns.setdefault(
            t, {"judged": False, "built": set(), "settle_ok": False}
        )
        dc = getattr(decision.color, "value", decision.color)
        own_turn = getattr(s.colors[s.current_turn_index], "value", None) == self.you
        if dc == self.you and own_turn:
            acts = game.playable_actions
            if p[f"P{yi}_WOOD_IN_HAND"] >= 1 and p[f"P{yi}_BRICK_IN_HAND"] >= 1:
                if (
                    p[f"P{yi}_SHEEP_IN_HAND"] >= 1
                    and p[f"P{yi}_WHEAT_IN_HAND"] >= 1
                    and p[f"P{yi}_SETTLEMENTS_AVAILABLE"] > 0
                    and s.board.buildable_node_ids(col[self.you])
                ):
                    rec["settle_ok"] = True
                if not rec["judged"] and any(
                    a.action_type == ActionType.BUILD_ROAD for a in acts
                ):
                    rec["judged"] = True
                    rec["entry"] = self.i
                    rec["n_legal"] = len(acts)
                    rec["human"] = road_mass(HUMAN, game, col[self.you], acts)
                    rec["bot"] = road_mass(BOT, game, col[self.you], acts)
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
    rep = replay(record, observer=obs)
    if not rep.ok:
        return None
    return obs


def mean_ci(groups, reps=2000, seed=0):
    """groups: per-game lists of values; mean over values, game-clustered bootstrap."""
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
    return f"{sum(flat) / len(flat):.3f} [{vals[int(0.025 * reps)]:.3f}, {vals[int(0.975 * reps) - 1]:.3f}] (n {len(flat)}, games {len(groups)})"


def main():
    with open("data/human/index.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = {r["capture"]: r for r in rows if r.get("status") == "ok"}
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
        games.append((path, r, o))
    print(f"games {len(games)}, skipped {skipped}")
    cands = []
    for label in ("person", "bot"):
        sel = [(path, o) for path, _, o in games if o.first and o.first[0] == label]
        print(f"\n{label} first: games {len(sel)}")
        cells = {}
        for path, o in sel:
            t0 = o.first[1]
            per = {}
            for t, rec in o.turns.items():
                if not (t0 - WINDOW <= t < t0 and rec["judged"]):
                    continue
                kind = "settle_ok" if rec["settle_ok"] else "no_settle"
                act = (
                    "road"
                    if "road" in rec["built"]
                    else ("other" if rec["built"] else "nothing")
                )
                for key in (f"{kind}/{act}", f"{kind}/all", "all/all"):
                    per.setdefault(key, []).append(rec)
                if label == "person" and kind == "no_settle" and act == "nothing":
                    cands.append(
                        {
                            "capture": path,
                            "entry": rec["entry"],
                            "turn": t,
                            "claim_turn": t0,
                            "p_human": round(rec["human"], 4),
                            "p_bot": round(rec["bot"], 4),
                        }
                    )
            for k, v in per.items():
                cells.setdefault(k, []).append(v)
        for k in sorted(cells):
            g = cells[k]
            print(f"  {k}:")
            print(f"    P_human(road) {mean_ci([[r['human'] for r in x] for x in g])}")
            print(f"    P_bot(road)   {mean_ci([[r['bot'] for r in x] for x in g])}")
            print(
                f"    human - bot   {mean_ci([[r['human'] - r['bot'] for r in x] for x in g])}"
            )
            print(
                f"    human>=0.5    {mean_ci([[float(r['human'] >= 0.5) for r in x] for x in g])}"
            )
    cands.sort(key=lambda c: c["p_bot"] - c["p_human"])
    os.makedirs("data/orchestrator/live-loss", exist_ok=True)
    with open("data/orchestrator/live-loss/road_candidates.json", "w") as f:
        json.dump(cands, f, indent=1)
    strong = [c for c in cands if c["p_human"] >= 0.5]
    print(
        f"\ncandidates (person-first, no settle, idle): {len(cands)}, human>=0.5: {len(strong)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
