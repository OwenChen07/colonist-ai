"""Does deeper search build a city more often when one is affordable?

Per position (our PLAY_TURN, BUILD_CITY playable, plus some other build or buy):
base search twice (two seeds, the noise floor) and a deep search once.
Early = nobody at 6+ visible points; late = someone is. Aggregate output only.

  city_depth.py NET OUT.json [per_class] [base] [deep] [workers] [last]
"""

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_v] = "1"

import json
import sys
from multiprocessing import Pool

from catanatron.colonist import road_benchmark as R
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.net import Net
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionPrompt, ActionType
from catanatron.models.player import Color
from catanatron.state_functions import get_visible_victory_points

NET, OUT = sys.argv[1], sys.argv[2]
PER_CLASS = int(sys.argv[3]) if len(sys.argv) > 3 else 60
BASE = int(sys.argv[4]) if len(sys.argv) > 4 else 4800
DEEP = int(sys.argv[5]) if len(sys.argv) > 5 else 12000
WORKERS = int(sys.argv[6]) if len(sys.argv) > 6 else 3
LAST = int(sys.argv[7]) if len(sys.argv) > 7 else 1500

OTHER = {
    ActionType.BUILD_ROAD,
    ActionType.BUILD_SETTLEMENT,
    ActionType.BUY_DEVELOPMENT_CARD,
}


class Obs:
    def __init__(self, color):
        self.color = color
        self.got = {"early": None, "late": None}

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        if decision.color != self.color:
            return
        s = game.state
        if len(s.colors) != 2 or s.current_prompt != ActionPrompt.PLAY_TURN:
            return
        types = {a.action_type for a in game.playable_actions}
        if ActionType.BUILD_CITY not in types or not (types & OTHER):
            return
        late = max(get_visible_victory_points(s, c) for c in s.colors) >= 6
        cls = "late" if late else "early"
        if self.got[cls] is None:
            self.got[cls] = (game.copy(), decision.action_type.value)


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
    cls, i, game, live = job
    seed = 2903 + 3 * i
    a = R.search_once(game, _net, BASE, seed)
    b = R.search_once(game, _net, BASE, seed + 1)
    d = R.search_once(game, _net, DEEP, seed + 2)
    return cls, live, a, b, d


def main():
    jobs, counts = [], {"early": 0, "late": 0}
    for path in R.indexed_captures("data/human/index.jsonl", "captures", LAST):
        try:
            got = positions(path)
        except (OSError, ValueError, KeyError, IndexError, AttributeError):
            continue
        for cls, item in got.items():
            if item is not None and counts[cls] < PER_CLASS:
                jobs.append((cls, len(jobs), item[0], item[1]))
                counts[cls] += 1
        if min(counts.values()) >= PER_CLASS:
            break
    print("positions", counts, flush=True)
    rows = {"early": [], "late": []}
    with Pool(WORKERS) as pool:
        for n, (cls, live, a, b, d) in enumerate(pool.imap_unordered(work, jobs), 1):
            rows[cls].append((live, a, b, d))
            if n % 10 == 0:
                print("done", n, flush=True)
    out = {"base": BASE, "deep": DEEP}
    city = "BUILD_CITY"
    for cls, rs in rows.items():
        n = len(rs)
        out[cls] = {
            "positions": n,
            "live_city": sum(live == city for live, _, _, _ in rs),
            "base1_city": sum(a["type"] == city for _, a, _, _ in rs),
            "base2_city": sum(b["type"] == city for _, _, b, _ in rs),
            "deep_city": sum(d["type"] == city for _, _, _, d in rs),
            "base_vs_base_changed": sum(a["key"] != b["key"] for _, a, b, _ in rs),
            "base_vs_deep_changed": sum(a["key"] != d["key"] for _, a, _, d in rs),
            "base2_vs_deep_changed": sum(b["key"] != d["key"] for _, _, b, d in rs),
            "deep_types": {},
            "mean_base_s": sum(a["seconds"] for _, a, _, _ in rs) / max(n, 1),
            "mean_deep_s": sum(d["seconds"] for _, _, _, d in rs) / max(n, 1),
        }
        for _, _, _, d in rs:
            t = out[cls]["deep_types"]
            t[d["type"]] = t.get(d["type"], 0) + 1
    print(json.dumps(out, indent=1))
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
