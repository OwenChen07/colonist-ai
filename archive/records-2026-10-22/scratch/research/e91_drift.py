"""E91: where did E54 step 50 drift, and does its value read OpenHex games well?

(1) Top-move agreement and mean prior distance (TV) of step 50 with round 73
and round 208 (round 208 vs round 73 as the yardstick) on: self-play
positions (round 208's data), people's positions (holdout policy_won and
opponent_exact), and OpenHex positions (every saved game replayed, our
decisions with 2+ legal moves), the last split by phase.
(2) On OpenHex positions: each net's value error to the result by phase, and
calibration by value bin; in the games step 50 played (E86), its peak value
in each loss.

    .venv/bin/python data/orchestrator/scratch/research/e91_drift.py
"""

import glob
import json
import os
import pickle
import statistics as st
import sys

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.net import Net
from catanatron.openhex.board import Board
from catanatron.openhex.follow import Follower, color

ROOT = "/home/owen/colonist-ai"
NETS = {
    "s50": f"{ROOT}/data/orchestrator/e54/run1/iter-50/net.npz",
    "r73": f"{ROOT}/runs/g1/nets/round_073.npz",
    "r208": f"{ROOT}/runs/g1/nets/round_208.npz",
    "r152": f"{ROOT}/runs/g1/nets/round_152.npz",
}
PAIRS = (("s50", "r73"), ("s50", "r208"), ("r208", "r73"))
E86_START, R152_START = 277, 172
nets = {k: Net.load(p) for k, p in NETS.items()}


def shard_outputs(net, d):
    static, dyn, game = d["static"], d["dynamic"].astype(np.float32), d["game"]
    logits, _ = net.forward_rows([static[g].astype(np.float32) for g in game], dyn)
    legal = np.unpackbits(d["legal"], axis=1, count=E.NUM_ACTIONS).astype(bool)
    x = np.where(legal, logits, -np.inf)
    x = x - x.max(axis=1, keepdims=True)
    p = np.exp(x)
    return p / p.sum(axis=1, keepdims=True)


def shards(label, paths, limit=None):
    res = {pair: [0, 0, []] for pair in PAIRS}
    n = 0
    for path in paths:
        with np.load(path) as d:
            if "legal" not in d:
                continue
            ps = {k: shard_outputs(nets[k], d) for k in ("s50", "r73", "r208")}
        many = (ps["r73"] > 0).sum(axis=1) >= 2
        for a, b in PAIRS:
            pa, pb = ps[a][many], ps[b][many]
            r = res[(a, b)]
            r[0] += int((pa.argmax(1) == pb.argmax(1)).sum())
            r[1] += len(pa)
            r[2].append(0.5 * np.abs(pa - pb).sum(1))
        n += 1
        if limit and n >= limit:
            break
    for (a, b), (same, tot, tv) in res.items():
        tv = np.concatenate(tv) if tv else np.zeros(1)
        print(
            f"{label:28s} {a:>4} vs {b:<4} top move same {100 * same / max(tot, 1):5.1f}% "
            f"of {tot:6d}  TV {tv.mean():.3f}",
            flush=True,
        )


def openhex():
    done = []
    for path in sorted(
        glob.glob(f"{ROOT}/data/openhex/games/*.json"), key=os.path.getmtime
    ):
        rec = json.load(open(path))
        if (rec.get("game") or {}).get("status") == "active":
            continue
        done.append(rec)
    rows = []  # (game index, who played, turn, seat, won, {net: (top, value, prior)})
    for gi, g in enumerate(done):
        if not g.get("tiles"):
            continue
        w = (g.get("game") or {}).get("winner")
        w = w.get("color") if isinstance(w, dict) else w
        won = 1.0 if w == g["me"] else -1.0
        seat = next(
            (
                p.get("seat")
                for p in g["game"].get("players", [])
                if p.get("color") == g["me"]
            ),
            None,
        )
        who = "s50" if gi >= E86_START else "r152" if gi >= R152_START else "r73"
        fol = Follower(
            Board(g["tiles"], g["ports"]), g["turn_order"], g["me"], g["robber"]
        )
        events = sorted(
            {e["seq"]: e for e in g["events"]}.values(), key=lambda e: e["seq"]
        )
        me = color(g["me"])
        orig = Follower._apply
        local = []

        def apply(game, action, result, _orig=orig):
            s = game.state
            if s.current_color() == me and len(game.playable_actions) >= 2:
                legal = E.legal_indices(game.playable_actions)
                out = {}
                for k, net in nets.items():
                    pr, v = net.evaluate(game, me, legal)
                    out[k] = (int(np.argmax(pr)), v, np.asarray(pr))
                local.append((gi, who, s.num_turns, seat, won, out))
            return _orig(game, action, result)

        Follower._apply = staticmethod(apply)
        try:
            fol.feed(events)
            fol.settle()
            fol.replay()
            rows.extend(local)
        except Exception as e:  # noqa: BLE001
            print(g["id"][:8], "replay failed:", repr(e)[:100])
        finally:
            Follower._apply = staticmethod(orig)
    return rows


PHASES = (
    ("all", lambda t: True),
    ("turn<20", lambda t: t < 20),
    ("turn 20-49", lambda t: 20 <= t < 50),
    ("turn 50+", lambda t: t >= 50),
)


def main():
    if "--openhex-only" not in sys.argv:
        sp = sorted(glob.glob(f"{ROOT}/runs/g1/round_208/data/shard_*.npz"))[:30]
        shards("self-play (round 208 data)", sp)
        shards(
            "people won (holdout)",
            sorted(glob.glob(f"{ROOT}/data/human/holdout/policy_won/shard_*.npz")),
        )
        shards(
            "people all (opponent_exact)",
            sorted(glob.glob(f"{ROOT}/data/human/holdout/opponent_exact/shard_*.npz")),
        )

    cache = f"{ROOT}/data/orchestrator/e91/openhex_rows.pkl"
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            rows = pickle.load(f)
    else:
        rows = openhex()
        with open(cache, "wb") as f:
            pickle.dump(rows, f)
    games = len({r[0] for r in rows})
    print(f"\nOpenHex: {games} games, {len(rows)} of our decisions", flush=True)
    for label, sel in PHASES:
        for a, b in PAIRS:
            rs = [r for r in rows if sel(r[2])]
            same = sum(r[5][a][0] == r[5][b][0] for r in rs)
            tv = [0.5 * float(np.abs(r[5][a][2] - r[5][b][2]).sum()) for r in rs]
            print(
                f"OpenHex {label:11s} {a:>4} vs {b:<4} top move same {100 * same / len(rs):5.1f}% "
                f"of {len(rs):5d}  TV {st.mean(tv):.3f}"
            )
    print(
        "\nOpenHex value error to the result (mean (v - z)^2), all positions, by phase:"
    )
    for label, sel in PHASES:
        rs = [r for r in rows if sel(r[2])]
        line = "  ".join(
            f"{k} {st.mean((r[5][k][1] - r[4]) ** 2 for r in rs):.3f}" for k in nets
        )
        diff = np.array(
            [(r[5]["s50"][1] - r[4]) ** 2 - (r[5]["r208"][1] - r[4]) ** 2 for r in rs]
        )
        # cluster by game for the interval
        by = {}
        for r, d in zip(rs, diff):
            by.setdefault(r[0], []).append(d)
        gm = np.array([np.mean(v) for v in by.values()])
        se = gm.std(ddof=1) / np.sqrt(len(gm)) if len(gm) > 1 else 0
        print(
            f"  {label:11s} n {len(rs):5d}  {line}   s50-r208 {diff.mean():+.3f} (±{1.96 * se:.3f} by game)"
        )
    print("\nOpenHex calibration (all positions): value bin -> win share, per net")
    bins = (-1.01, -0.6, -0.2, 0.2, 0.6, 1.01)
    for k in ("s50", "r208", "r73"):
        parts = []
        for lo, hi in zip(bins, bins[1:]):
            zs = [r[4] for r in rows if lo <= r[5][k][1] < hi]
            if zs:
                parts.append(
                    f"[{lo:+.1f},{hi:+.1f}) {len(zs):5d} win {100 * np.mean([z > 0 for z in zs]):3.0f}%"
                    f" (says {50 * (1 + np.mean([r[5][k][1] for r in rows if lo <= r[5][k][1] < hi])):3.0f}%)"
                )
        print(f"  {k}: " + " | ".join(parts))
    print("\nBy who played and seat, late (turn 50+) mean value vs result:")
    for who in ("r73", "r152", "s50"):
        for seat in (0, 1):
            rs = [r for r in rows if r[1] == who and r[3] == seat and r[2] >= 50]
            if not rs:
                continue
            n = len({r[0] for r in rs})
            wins = np.mean([r[4] > 0 for r in {r[0]: r for r in rs}.values()])
            print(
                f"  played by {who:4s} seat {seat}: {n:3d} games, won {100 * wins:3.0f}%; "
                f"late value says (own net) {50 * (1 + st.mean(r[5][who][1] for r in rs)):3.0f}%, "
                f"s50 {50 * (1 + st.mean(r[5]['s50'][1] for r in rs)):3.0f}%, "
                f"r208 {50 * (1 + st.mean(r[5]['r208'][1] for r in rs)):3.0f}%"
            )
    print("\nPeak win chance (own net's value) in lost games, by who played:")
    for who in ("r73", "r152", "s50"):
        peaks = []
        for gi in sorted({r[0] for r in rows if r[1] == who and r[4] < 0}):
            vs = [r[5][who][1] for r in rows if r[0] == gi and r[2] >= 8]
            if vs:
                peaks.append(50 * (1 + max(vs)))
        if peaks:
            peaks = np.array(peaks)
            print(
                f"  {who:4s} {len(peaks):3d} losses: peak median {np.median(peaks):.0f}%, "
                f"60%+ in {100 * np.mean(peaks >= 60):.0f}%, 75%+ in {100 * np.mean(peaks >= 75):.0f}%"
            )
    print(
        "\nTurns per game (median), by who played: "
        + ", ".join(
            f"{w} {np.median([max(r[2] for r in rows if r[0] == gi) for gi in {r[0] for r in rows if r[1] == w}]):.0f}"
            for w in ("r73", "r152", "s50")
        )
    )
    print("done", flush=True)


if __name__ == "__main__":
    main()
