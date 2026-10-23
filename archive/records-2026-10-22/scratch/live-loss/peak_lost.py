"""Leads lost after a high win chance: for the last N ranked games per account,
the raw net's (runs/g1/best.npz) highest stated win chance p=(v+1)/2 at a bot
decision from turn 20 on, and the outcome (games.jsonl winner==bot_color).
Compares bot5's overnight slide (Oct 19 9 PM - Oct 20 6:48 AM Toronto) with
bot5 at other times and with the other accounts. Aggregates only.
Usage: peak_lost.py [last N per account]"""

import json
import math
import sys
from datetime import datetime, timezone
from multiprocessing import Pool

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.net import Net
from catanatron.colonist.replay import replay
from catanatron.models.player import Color

NET = "runs/g1/best.npz"
ACCOUNTS = ("bot3", "bot4", "bot5", "bot7")
W0 = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc).timestamp()
W1 = datetime(2026, 10, 20, 10, 48, tzinfo=timezone.utc).timestamp()
OUT = "data/orchestrator/scratch/live-loss/peak_lost.json"
_net = None


def wilson(k, n, z=1.96):
    if n == 0:
        return "n=0"
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{100 * p:.1f}% [{100 * (c - h):.1f}, {100 * (c + h):.1f}] ({k}/{n})"


class Obs:
    def __init__(self, me):
        self.me = me
        self.peak = 0.0
        self.peak_turn = None
        self.first85 = None
        self.sum_p, self.n_p = 0.0, 0  # mean stated p from turn 20 (calibration)

    def before(self, game):
        pass

    def step(self, game, decision):
        t = game.state.num_turns
        if decision.color != self.me or t < 20:
            return
        _, v = _net.evaluate(game, self.me)
        p = (v + 1) / 2
        self.sum_p += p
        self.n_p += 1
        if p > self.peak:
            self.peak, self.peak_turn = p, t
        if p >= 0.85 and self.first85 is None:
            self.first85 = t

    def after(self, game):
        self.end_turn = game.state.num_turns


def scan(job):
    global _net
    path, row = job
    if _net is None:
        _net = Net.load(NET)
    try:
        conv = convert(load_capture(path))
        if conv.unsupported:
            return None
        rec = conv.record
        you = [c for c, p in rec["players"].items() if p["you"]]
        if len(you) != 1 or len(rec["seats"]) != 2:
            return None
        obs = Obs(Color[you[0]])
        rep = replay(rec, observer=obs)
        if not rep.ok:
            return None
        return dict(
            row,
            peak=obs.peak,
            peak_turn=obs.peak_turn,
            first85=obs.first85,
            mean_p=obs.sum_p / obs.n_p if obs.n_p else None,
            end_turn=obs.end_turn,
        )
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)[:200]}


def report(label, rs):
    hi = [r for r in rs if r["peak"] >= 0.85]
    wins = sum(r["won"] for r in rs)
    print(f"{label}: games {len(rs)}, won {wilson(wins, len(rs))}")
    print(
        f"  reached 85%+: {wilson(len(hi), len(rs))}; of those won {wilson(sum(r['won'] for r in hi), len(hi))}"
    )
    lost = [r for r in rs if not r["won"]]
    print(
        f"  losses that had reached 85%+: {wilson(sum(r['peak'] >= 0.85 for r in lost), len(lost))}"
    )
    for lo, up in ((0.85, 0.9), (0.9, 0.95), (0.95, 1.01)):
        b = [r for r in rs if lo <= r["peak"] < up]
        print(
            f"   peak {lo:.2f}-{min(up, 1):.2f}: won {wilson(sum(r['won'] for r in b), len(b))}"
        )


def main():
    last = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    jobs = []
    for acc in ACCOUNTS:
        rows = [json.loads(x) for x in open(f"data/{acc}/games.jsonl")]
        rows = [r for r in rows if r.get("queue") == "ranked"]
        if acc == "bot5":
            keep = [r for r in rows if W0 <= r["at"] <= W1]
            rest = [r for r in rows if r["at"] < W0][-last:]
            rows = rest + keep
        else:
            rows = rows[-last:]
        for r in rows:
            meta = {
                "acc": acc,
                "at": r["at"],
                "won": r.get("winner") == r.get("bot_color"),
                "window": acc == "bot5" and W0 <= r["at"] <= W1,
            }
            jobs.append((f"captures/{acc}/{r['capture']}", meta))
    with Pool(6) as pool:
        res = [r for r in pool.imap_unordered(scan, jobs, chunksize=4) if r]
    errs = [r for r in res if "error" in r]
    res = [r for r in res if "error" not in r]
    json.dump(res, open(OUT, "w"))
    print(f"jobs {len(jobs)} replayed {len(res)} errors {len(errs)}")
    report("bot5 overnight window", [r for r in res if r["window"]])
    report(
        "bot5 before the window",
        [r for r in res if r["acc"] == "bot5" and not r["window"]],
    )
    report("bot3/4/7", [r for r in res if r["acc"] != "bot5"])
    report("all but the window", [r for r in res if not r["window"]])


if __name__ == "__main__":
    main()
