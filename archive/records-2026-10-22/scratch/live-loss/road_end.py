"""When does a Longest Road holder at roll 60 lose it? Live games of 70+ rolls vs
2100+. Records the road holder, lengths and VPs at every roll (both players) and
at the game's end; reconciles E82's kept rate (read at the bot's last roll) with
long_games.py's (read at the final state). Aggregates only.

    .venv/bin/python data/orchestrator/scratch/live-loss/road_end.py [WORKERS] [LIMIT]
    .venv/bin/python data/orchestrator/scratch/live-loss/road_end.py table
"""

import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from e75_robber import col, jobs  # noqa: E402

OUT = "data/orchestrator/live-loss/road_end.json"


def row(game, roller, you, them):
    s = game.state
    idx = {c.value: i for c, i in s.color_to_index.items()}
    p = s.player_state
    b, t = idx[you], idx[them]
    return [
        roller,
        int(p[f"P{b}_HAS_ROAD"]),
        int(p[f"P{t}_HAS_ROAD"]),
        int(p[f"P{b}_LONGEST_ROAD_LENGTH"]),
        int(p[f"P{t}_LONGEST_ROAD_LENGTH"]),
        int(p[f"P{b}_ACTUAL_VICTORY_POINTS"]),
        int(p[f"P{t}_ACTUAL_VICTORY_POINTS"]),
    ]


class Obs:
    def __init__(self, you, them):
        self.you, self.them = you, them
        self.rows = []
        self.game = None

    def before(self, game):
        pass

    def after(self, game):
        self.game = game

    def step(self, game, decision):
        self.game = game
        s = game.state
        if s.is_initial_build_phase or decision.action_type != ActionType.ROLL:
            return
        who = "bot" if col(decision.color) == self.you else "person"
        self.rows.append(row(game, who, self.you, self.them))


def facts(job):
    path, won, rolls = job
    try:
        record = convert(load_capture(path)).record
        you = [c for c, p in record["players"].items() if p["you"]]
        if len(you) != 1 or len(record["seats"]) != 2:
            return None
        you = you[0]
        them = next(c for c in record["seats"] if c != you)
        obs = Obs(you, them)
        rep = replay(record, observer=obs)
        if rep.errors or not obs.rows or obs.game is None:
            return None
        return {
            "won": won,
            "rolls": rolls,
            "rows": obs.rows,
            "end": row(obs.game, "end", you, them),
        }
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def wil(k, n):
    if not n:
        return "-"
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return f"{100 * p:.1f}% [{100 * (c - h):.1f}, {100 * (c + h):.1f}] ({k}/{n})"


def table(res):
    gg = [g for g in res if g["rolls"] >= 70 and len(g["rows"]) >= 60]
    print(f"{len(res)} replayed, {len(gg)} of 70+ rolls")
    from collections import Counter

    wv = Counter(max(g["end"][5], g["end"][6]) for g in gg)
    print("winner's end vp:", sorted(wv.items()))
    target = wv.most_common(1)[0][0]
    for who, hi in (("bot", 1), ("person", 2)):
        for lab, ms in (("1-2", (1, 2)), ("3+", (3, 99))):
            sel = []
            for g in gg:
                r60 = g["rows"][59]  # the 60th roll
                if not r60[hi]:
                    continue
                m = (r60[3] - r60[4]) * (1 if who == "bot" else -1)
                if not ms[0] <= m <= ms[1]:
                    continue
                sel.append(g)
            n = len(sel)
            kept_end = sum(g["end"][hi] for g in sel)
            bot_last = [r for r in sel if True]
            kb = sum(
                next(r for r in reversed(g["rows"]) if r[0] == "bot")[hi]
                for g in bot_last
            )
            # where the road was lost (holder lacks it at the end)
            lost = [g for g in sel if not g["end"][hi]]
            last_turn = 0  # lost during the final turn (after the last roll)
            final_pair = 0  # held at the holder's last roll, gone by the end
            win_with = 0  # the taker won the game and got its 10th+ point from the road
            for g in lost:
                if g["rows"][-1][hi]:
                    last_turn += 1
                own_last = next(r for r in reversed(g["rows"]) if r[0] == who)
                if own_last[hi]:
                    final_pair += 1
                taker_won = (not g["won"]) if who == "bot" else g["won"]
                tv = g["end"][6] if who == "bot" else g["end"][5]
                if taker_won and tv >= target and tv - 2 < target:
                    win_with += 1
            print(f"\n{who} holds by {lab} at roll 60: n {n}")
            print(
                f"  kept at the end {wil(kept_end, n)}; held at the bot's last roll {wil(kb, n)}"
            )
            print(
                f"  lost {len(lost)}: in the game's last turn {wil(last_turn, len(lost))};"
                f" held at the holder's own last roll {wil(final_pair, len(lost))};"
                f" taker won and needed the road's 2 VP {wil(win_with, len(lost))}"
            )
            if who == "bot":
                for won in (True, False):
                    s2 = [g for g in sel if g["won"] == won]
                    print(
                        f"  bot {'won ' if won else 'lost'}: kept at end {wil(sum(g['end'][1] for g in s2), len(s2))}"
                    )


def main():
    if sys.argv[1:2] == ["table"]:
        table(json.load(open(OUT)))
        return
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    js = [j for j in jobs() if j[2] >= 70]
    if len(sys.argv) > 2:
        js = js[: int(sys.argv[2])]
    print(f"{len(js)} jobs", flush=True)
    with Pool(w, initializer=os.nice, initargs=(19,)) as pool:
        res = [r for r in pool.imap_unordered(facts, js, chunksize=8) if r]
    json.dump(res, open(OUT, "w"))
    print("done", flush=True)
    table(res)


if __name__ == "__main__":
    main()
