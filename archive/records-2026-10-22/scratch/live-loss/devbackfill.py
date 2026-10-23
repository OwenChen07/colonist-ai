"""How many of the person's decisions could get their dev-card hand back.

For each train capture in data/human/index.jsonl: replay, and at each of the
person's decisions (more than one legal move) classify their hidden hand:
  a: no card held
  b: held, hand known exactly (one consistent multiset)
  c: held, all identified, but several consistent multisets
  d: held, a never-identified card may be in hand
Plays need a purchase on an earlier turn; VP cards (count from the end-game
reveal) and never-identified cards can sit on any purchase.

    .venv/bin/python data/orchestrator/scratch/live-loss/devbackfill.py [LIMIT]
"""

import itertools
import json
import os
import sys
from collections import Counter
from multiprocessing import Pool

sys.path.insert(0, "catanatron")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.state_functions import player_num_dev_cards  # noqa: E402

ROOT = "/home/owen/colonist-ai"
PLAYS = {
    "PLAY_KNIGHT_CARD": "K",
    "PLAY_MONOPOLY": "M",
    "PLAY_YEAR_OF_PLENTY": "Y",
    "PLAY_ROAD_BUILDING": "R",
}


class Obs:
    def __init__(self, color):
        self.color = color
        self.turns = []  # num_turns at each log entry
        self.decisions = []  # (log index, num_turns, cards held)

    def before(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        self.turns.append(s.num_turns)
        if s.current_color().value != self.color or s.is_initial_build_phase:
            return
        if len(game.playable_actions) <= 1:
            return
        held = player_num_dev_cards(s, s.current_color())
        self.decisions.append((len(self.turns) - 1, s.num_turns, held))

    def after(self, game):
        pass


def nested_ok(plays, buys):
    """plays: turns of plays; buys: turns of purchases. Each play needs its
    own purchase on an earlier turn (prefix sets, so Hall on sorted plays)."""
    buys = sorted(buys)
    for k, p in enumerate(sorted(plays)):
        if sum(1 for b in buys if b < p) < k + 1:
            return False
    return True


def hands(t_idx, buys, plays, vp, unk):
    """All feasible hand multisets at log index t_idx.
    buys: [(idx, turn)], plays: [(idx, turn, type)]."""
    before = [b for b in buys if b[0] < t_idx]
    after = [b for b in buys if b[0] >= t_idx]
    p_before = [p for p in plays if p[0] < t_idx]
    p_after = sorted([p for p in plays if p[0] >= t_idx])
    h = len(before) - len(p_before)
    types = sorted({p[2] for p in p_after})
    by_type = {ty: [p for p in p_after if p[2] == ty] for ty in types}
    out = set()
    ranges = [range(len(by_type[ty]) + 1) for ty in types]
    for counts in itertools.product(*ranges):
        s = sum(counts)
        if s > h:
            continue
        rest = h - s
        for v in range(min(vp, rest) + 1):
            u = rest - v
            if u > unk:
                continue
            if vp - v > len(after) or unk - u > len(after):
                continue
            # S = earliest plays of each type go to before-t purchases.
            S, R = [], []
            for ty, c in zip(types, counts):
                S += by_type[ty][:c]
                R += by_type[ty][c:]
            if len(R) + (vp - v) + (unk - u) != len(after):
                continue
            if not nested_ok([p[1] for p in p_before + S], [b[1] for b in before]):
                continue
            if not nested_ok([p[1] for p in R], [b[1] for b in after]):
                continue
            hand = Counter(dict(zip(types, counts)))
            hand["V"] = v
            hand["U"] = u
            out.add(tuple(sorted((k, n) for k, n in hand.items() if n)))
    return out


def phase(turn):
    return "<30" if turn < 30 else "30-75" if turn < 75 else "75+"


def one(row):
    path = os.path.join(ROOT, "captures", row["account"], row["capture"])
    try:
        conversion = convert(load_capture(path))
        record = conversion.record
        you = [c for c, p in record["players"].items() if p["you"]][0]
        them = [c for c in record["seats"] if c != you][0]
        obs = Obs(them)
        if not replay(record, observer=obs).ok:
            return {"err": "replay"}
        buys, plays = [], []
        for i, (payload, _) in enumerate(record["log"]):
            if payload[0] != them:
                continue
            if payload[1] == "BUY_DEVELOPMENT_CARD":
                buys.append((i, obs.turns[i]))
            elif payload[1] in PLAYS:
                plays.append((i, obs.turns[i], PLAYS[payload[1]]))
        hv = record.get("hidden_vp_cards") or {}
        vp_known = them in hv
        vp = hv.get(them, 0)
        unk = len(buys) - len(plays) - vp
        if unk < 0:
            return {"err": "negative unknown"}
        if not vp_known:
            unk, vp = unk + vp, 0
        if buys and not hands(len(record["log"]) + 1, buys, plays, vp, unk):
            return {"err": "no consistent matching"}
        cats = []
        for idx, turn, held in obs.decisions:
            if held == 0:
                cats.append(("a", phase(turn), 0))
                continue
            hs = hands(idx, buys, plays, vp, unk)
            if not hs:
                cats.append(("x", phase(turn), 0))
            elif any(dict(h).get("U") for h in hs):
                cats.append(("d", phase(turn), 0))
            elif len(hs) == 1:
                cats.append(("b", phase(turn), 0))
            else:
                doubt = {
                    k
                    for k in {k for h in hs for k, _ in h}
                    if len({dict(h).get(k, 0) for h in hs}) > 1
                }
                cats.append(("c", phase(turn), len(doubt)))
        knights = sum(1 for p in plays if p[2] == "K")
        return {
            "cats": cats,
            "knights": knights,
            "vp_known": vp_known,
            "buys": len(buys),
            "unk": unk,
        }
    except Exception as e:  # one bad capture must not stop the rest
        return {"err": type(e).__name__}


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    rows = []
    with open(os.path.join(ROOT, "data/human/index.jsonl")) as f:
        for line in f:
            r = json.loads(line)
            if r.get("status") == "ok" and r.get("split") == "train":
                rows.append(r)
    rows = rows[:limit]
    with Pool(6) as pool:
        results = pool.map(one, rows, chunksize=4)
    errs = Counter(r["err"] for r in results if "err" in r)
    good = [r for r in results if "err" not in r]
    total = Counter()
    byphase = Counter()
    doubt = Counter()
    for r in good:
        for cat, ph, nd in r["cats"]:
            total[cat] += 1
            byphase[(cat, ph)] += 1
            if cat == "c":
                doubt[nd] += 1
    n = sum(total.values())
    print(f"games {len(rows)}, replayed {len(good)}, errors {dict(errs)}")
    print(f"vp reveal present in {sum(r['vp_known'] for r in good)} games")
    print(
        f"games with a never-identified card: {sum(1 for r in good if r['unk'])}"
        f" of {sum(1 for r in good if r['buys'])} with any purchase"
    )
    print(f"decisions {n}")
    for c in "abcdx":
        print(f"  {c}: {total[c]} ({100 * total[c] / max(n, 1):.1f}%)")
    print("by phase (count, share of that phase's decisions):")
    for ph in ("<30", "30-75", "75+"):
        m = sum(byphase[(c, ph)] for c in "abcdx")
        cells = "  ".join(
            f"{c} {byphase[(c, ph)]} ({100 * byphase[(c, ph)] / max(m, 1):.1f}%)"
            for c in "abcdx"
        )
        print(f"  {ph:6} n={m}  {cells}")
    print(f"c: types in doubt {dict(sorted(doubt.items()))}")
    kr = [r for r in good if r["knights"] >= 3]
    kt = Counter(c for r in kr for c, _, _ in r["cats"])
    kn = sum(kt.values())
    all_b = sum(1 for r in kr if all(c in "ab" for c, _, _ in r["cats"]))

    def b_share(r):
        held = [c for c, _, _ in r["cats"] if c != "a"]
        return sum(c == "b" for c in held) / len(held) if held else 1.0

    half = sum(1 for r in kr if b_share(r) >= 0.5)
    print(
        f"knight-race games (3+ knights): {len(kr)}; all held decisions in b: {all_b};"
        f" half or more in b: {half}"
    )
    oth = [r for r in good if r["knights"] < 3]
    print(
        f"other games: {len(oth)}; all held in b: "
        f"{sum(1 for r in oth if b_share(r) == 1.0)}; half or more: "
        f"{sum(1 for r in oth if b_share(r) >= 0.5)}"
    )
    print(
        "  their decisions: "
        + "  ".join(f"{c} {kt[c]} ({100 * kt[c] / max(kn, 1):.1f}%)" for c in "abcdx")
    )


if __name__ == "__main__":
    main()
