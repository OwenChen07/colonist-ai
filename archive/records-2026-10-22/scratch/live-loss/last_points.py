"""For live-play (Oct 19, 4:43 PM): in ranked r73 losses where the bot's mean
stated win chance in a mid-game phase (bot rolls 15-29 or 30-44) was 20-60%,
how did the opponent get its last points? Replays each capture and splits the
opponent's points by source (settlements, cities, longest road, largest army,
VP cards) at the "hope point" (the bot's last searched move at >=20%) and at the
end; also the opponent's final turn alone. Wins in the same band for contrast.
Usage: last_points.py [N per account]. VP-card timing follows the replay's
oldest-hidden-draw rule (a lower bound on how early they were held)."""

import json
import math
import os
import re
import sys
from collections import Counter
from multiprocessing import Pool

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType

ROOT = os.path.expanduser("~/colonist-ai")
ACCOUNTS = ["bot3", "bot4", "bot5", "bot7"]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
MOVE = re.compile(r"  move: (.*) \(win chance (\d+)%, (\d+) ms(?:, (\d+) sims)?\)")
SAVED = re.compile(r"game saved to (\S+)")
SRC = ("settle", "city", "road", "army", "vpcard")


def games_from_log(path):
    out, cur = {}, []
    with open(path, errors="replace") as f:
        for line in f:
            if "following: game started" in line:
                cur = []
            m = MOVE.match(line.rstrip("\n"))
            if m:
                cur.append((m.group(1), int(m.group(2)) / 100, int(m.group(4) or 0)))
            s = SAVED.search(line)
            if s and cur:
                out.setdefault(s.group(1).rsplit("/", 1)[-1], cur)
    return out


def parts(state, color):
    i = state.color_to_index[color]
    p = state.player_state
    return {
        "settle": 5 - p[f"P{i}_SETTLEMENTS_AVAILABLE"],
        "city": 2 * (4 - p[f"P{i}_CITIES_AVAILABLE"]),
        "road": 2 * bool(p[f"P{i}_HAS_ROAD"]),
        "army": 2 * bool(p[f"P{i}_HAS_ARMY"]),
        "vpcard": p[f"P{i}_VICTORY_POINT_IN_HAND"],
    }


class Obs:
    def __init__(self, bot, opp):
        self.bot, self.opp = bot, opp
        self.at_bot_roll = []  # opponent parts at each bot roll (before it)
        self.opp_turn_start = None
        self.final = None

    def before(self, game):
        pass

    def step(self, game, d):
        if d.action_type != ActionType.ROLL:
            return
        if d.color.value == self.bot:
            self.at_bot_roll.append(parts(game.state, self.opp_c(game)))
        elif d.color.value == self.opp:
            self.opp_turn_start = parts(game.state, self.opp_c(game))

    def opp_c(self, game):
        return next(c for c in game.state.colors if c.value == self.opp)

    def after(self, game):
        self.final = parts(game.state, self.opp_c(game))


def one(job):
    path, moves, won = job
    try:
        rec = convert(load_capture(path)).record
        you = [c for c, p in rec["players"].items() if p["you"]]
        if len(you) != 1 or len(rec["seats"]) != 2:
            return None
        opp = next(c for c in rec["seats"] if c != you[0])
        obs = Obs(you[0], opp)
        if not replay(rec, observer=obs).ok:
            return None
    except Exception:  # noqa: BLE001
        return None
    rolls, hope = 0, None
    for name, p, sims in moves:
        if name == "roll":
            rolls += 1
        if sims > 0 and p >= 0.2 and rolls >= 10:
            hope = rolls
    if hope is None or hope < 1 or hope > len(obs.at_bot_roll):
        hope_parts = None
    else:
        hope_parts = obs.at_bot_roll[hope - 1]
    return {
        "won": won,
        "final": obs.final,
        "hope": hope_parts,
        "hope_roll": hope,
        "rolls": len(obs.at_bot_roll),
        "last_turn": obs.opp_turn_start,
        "resigned": bool(rec.get("resigned")),
    }


def band(moves):
    rolls, ph = 0, {1: [], 2: []}
    for name, p, sims in moves:
        if name == "roll":
            rolls += 1
        if sims > 0:
            k = 1 if 15 <= rolls < 30 else 2 if 30 <= rolls < 45 else 0
            if k:
                ph[k].append(p)
    return any(ps and 0.2 <= sum(ps) / len(ps) < 0.6 for ps in ph.values())


def ci(k, n, z=1.96):
    if not n:
        return "n=0"
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{p:.1%} [{c - h:.1%}, {c + h:.1%}] ({k}/{n})"


def report(rs, label):
    print(f"\n== {label}: {len(rs)} games")
    if not rs:
        return
    fin = [r for r in rs if r["final"]]
    tot = Counter()
    for r in fin:
        tot.update(r["final"])
    print(
        "  opponent's final points by source (mean per game): "
        + ", ".join(f"{s} {tot[s] / len(fin):.2f}" for s in SRC)
        + f"; total {sum(tot.values()) / len(fin):.2f}"
    )
    for s in ("road", "army"):
        print(
            f"  opponent ended with {s}: {ci(sum(bool(r['final'][s]) for r in fin), len(fin))}"
        )
    print(
        f"  opponent ended holding >=1 VP card: {ci(sum(r['final']['vpcard'] > 0 for r in fin), len(fin))}"
    )
    h = [r for r in fin if r["hope"]]
    if h:
        gain = Counter()
        for r in h:
            for s in SRC:
                gain[s] += r["final"][s] - r["hope"][s]
        g = sum(gain.values())
        print(
            f"  from the hope point (bot's last >=20% move; {len(h)} games, mean "
            f"{sum(r['rolls'] - r['hope_roll'] for r in h) / len(h):.1f} bot rolls before the end): "
            f"opponent gained {g / len(h):.2f} points"
        )
        for s in SRC:
            print(
                f"    {s}: {gain[s] / len(h):+.2f} per game ({gain[s] / g:.0%} of the gain)"
                if g
                else ""
            )
        print(
            f"    VP cards already in the opponent's hand at the hope point: "
            f"{sum(r['hope']['vpcard'] for r in h) / len(h):.2f} per game; >=1 in "
            f"{ci(sum(r['hope']['vpcard'] > 0 for r in h), len(h))}"
        )
        for s in ("road", "army"):
            swing = sum(r["final"][s] > r["hope"][s] for r in h)
            print(f"    opponent took {s} after the hope point: {ci(swing, len(h))}")
    lt = [r for r in fin if r["last_turn"] and not r["resigned"]]
    if lt:
        gain, kinds = Counter(), Counter()
        for r in lt:
            d = {s: r["final"][s] - r["last_turn"][s] for s in SRC}
            gain.update(d)
            kinds[tuple(s for s in SRC if d[s] > 0) or ("none",)] += 1
        print(
            f"  opponent's final turn ({len(lt)} games played to 15): gain per game "
            + ", ".join(f"{s} {gain[s] / len(lt):+.2f}" for s in SRC)
        )
        print(
            "    sources that rose on the final turn: "
            + "; ".join(
                f"{'+'.join(k)} {ci(n, len(lt))}" for k, n in kinds.most_common(8)
            )
        )


def main():
    jobs = []
    for acct in ACCOUNTS:
        logs = games_from_log(f"{ROOT}/data/{acct}/bridge.log")
        recs = []
        for line in open(f"{ROOT}/data/{acct}/games.jsonl"):
            try:
                g = json.loads(line)
            except ValueError:
                continue
            if g.get("queue") == "ranked" and g.get("winner") and g.get("round") == 73:
                recs.append(g)
        for g in recs[-N:]:
            moves = logs.get(g["capture"])
            if not moves or not band(moves):
                continue
            jobs.append(
                (
                    f"{ROOT}/captures/{acct}/{g['capture']}",
                    moves,
                    g["winner"] == g["bot_color"],
                )
            )
    print(f"{len(jobs)} games in the 20-60% mid-game band")
    with Pool(6) as pool:
        rs = [r for r in pool.map(one, jobs, chunksize=4) if r]
    print(f"{len(rs)} replayed")
    report([r for r in rs if not r["won"]], "losses")
    report([r for r in rs if r["won"]], "wins (same band, for contrast)")


if __name__ == "__main__":
    main()
