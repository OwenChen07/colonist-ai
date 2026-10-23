"""Does deeper search change contested-road decisions more than other late ones?

Per position: base search twice (two seeds, the same-budget noise floor) and a
deep search once. Contested = road_benchmark.contested; control = a late
PLAY_TURN (max visible VP >= 8) that is not contested. Aggregate output only.

  road_depth.py NET OUT.json [per_class] [base] [deep] [workers] [last]
"""

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_v] = "1"  # the training box is busy; one core per worker

import json
import sys
from multiprocessing import Pool

from catanatron.colonist import encode as E
from catanatron.colonist import road_benchmark as R
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.net import Net
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionPrompt
from catanatron.models.player import Color
from catanatron.state_functions import get_visible_victory_points

NET, OUT = sys.argv[1], sys.argv[2]
PER_CLASS = int(sys.argv[3]) if len(sys.argv) > 3 else 60
BASE = int(sys.argv[4]) if len(sys.argv) > 4 else 4800
DEEP = int(sys.argv[5]) if len(sys.argv) > 5 else 12000
WORKERS = int(sys.argv[6]) if len(sys.argv) > 6 else 2
LAST = int(sys.argv[7]) if len(sys.argv) > 7 else 1500


def late_control(state):
    if len(state.colors) != 2 or state.current_prompt != ActionPrompt.PLAY_TURN:
        return False
    return max(
        get_visible_victory_points(state, c) for c in state.colors
    ) >= 8 and not R.contested(state)


class Obs:
    def __init__(self, color):
        self.color = color
        self.got = {"road": None, "control": None}

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        if decision.color != self.color or len(game.playable_actions) < 3:
            return
        s = game.state
        cls = "road" if R.contested(s) else "control" if late_control(s) else None
        # one position per class per game, the first one seen
        if cls and self.got[cls] is None:
            self.got[cls] = game.copy()


def positions(path):
    record = convert(load_capture(path)).record
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return {}
    obs = Obs(Color[you[0]])
    return obs.got if replay(record, observer=obs).ok else {}


_net = None


def work(job):
    global _net
    if _net is None:
        _net = Net.load(NET)
    cls, i, game = job
    seed = 1701 + 3 * i
    a = R.search_once(game, _net, BASE, seed)
    b = R.search_once(game, _net, BASE, seed + 1)
    d = R.search_once(game, _net, DEEP, seed + 2)
    return cls, a, b, d


def main():
    jobs, counts = [], {"road": 0, "control": 0}
    for path in R.indexed_captures("data/human/index.jsonl", "captures", LAST):
        try:
            got = positions(path)
        except (OSError, ValueError, KeyError, IndexError):
            continue
        for cls, game in got.items():
            if game is not None and counts[cls] < PER_CLASS:
                jobs.append((cls, len(jobs), game))
                counts[cls] += 1
        if min(counts.values()) >= PER_CLASS:
            break
    print("positions", counts, flush=True)
    rows = {"road": [], "control": []}
    with Pool(WORKERS) as pool:
        for n, (cls, a, b, d) in enumerate(pool.imap_unordered(work, jobs), 1):
            rows[cls].append((a, b, d))
            if n % 10 == 0:
                print("done", n, flush=True)
    out = {"base": BASE, "deep": DEEP}
    for cls, rs in rows.items():
        n = len(rs)
        out[cls] = {
            "positions": n,
            "base_vs_base_changed": sum(a["key"] != b["key"] for a, b, _ in rs),
            "base_vs_deep_changed": sum(a["key"] != d["key"] for a, _, d in rs),
            "base2_vs_deep_changed": sum(b["key"] != d["key"] for _, b, d in rs),
            "deep_road_moves": sum("ROAD" in d["type"] for _, _, d in rs),
            "base_road_moves": sum("ROAD" in a["type"] for a, _, _ in rs),
            "mean_base_s": sum(a["seconds"] for a, _, _ in rs) / max(n, 1),
            "mean_deep_s": sum(d["seconds"] for _, _, d in rs) / max(n, 1),
            "mean_abs_value_shift": sum(abs(d["value"] - a["value"]) for a, _, d in rs)
            / max(n, 1),
        }
    print(json.dumps(out, indent=1))
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
