"""Opening production in live games: after setup, each side's expected cards per
roll (pips), its ore+wheat and wood+brick pips, resource variety and ports, and
the bot's win rate by the pip difference and by who placed first. Aggregates
only, no names. Usage: opening.py [LAST] [WORKERS]"""

import json
import os
import random
import sys
from collections import defaultdict
from multiprocessing import Pool

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.loss_report import wilson
from catanatron.colonist.replay import replay

LAST = int(sys.argv[1]) if len(sys.argv) > 1 else 600
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 4


def pips(n):
    return 0 if n is None else 6 - abs(7 - n)


class Obs:
    def __init__(self, you, them):
        self.you, self.them = you, them
        self.order = []
        self.res = None

    def before(self, game):
        pass

    def step(self, game, decision):
        if self.res is not None:
            if self.res["lr_first"] is None:
                s = game.state
                idx = {c.value: i for c, i in s.color_to_index.items()}
                p = s.player_state
                if p[f"P{idx[self.you]}_HAS_ROAD"]:
                    self.res["lr_first"] = "bot"
                elif p[f"P{idx[self.them]}_HAS_ROAD"]:
                    self.res["lr_first"] = "person"
            return
        s = game.state
        b = s.board
        if s.is_initial_build_phase:
            for node, (color, _) in b.buildings.items():
                c = getattr(color, "value", color)
                if (node, c) not in self.order:
                    self.order.append((node, c))
            return
        out = {}
        for who, col in (("bot", self.you), ("person", self.them)):
            tot = ow = wb = 0
            kinds = set()
            for node, (color, _) in b.buildings.items():
                if getattr(color, "value", color) != col:
                    continue
                for tile in b.map.adjacent_tiles[node]:
                    if tile.resource is None:
                        continue
                    p = pips(tile.number)
                    tot += p
                    kinds.add(tile.resource)
                    r = getattr(tile.resource, "value", tile.resource)
                    if r in ("ORE", "WHEAT"):
                        ow += p
                    if r in ("WOOD", "BRICK"):
                        wb += p
            ports = 0
            for node_ids in b.map.port_nodes.values():
                ports += any(
                    n in b.buildings
                    and getattr(b.buildings[n][0], "value", b.buildings[n][0]) == col
                    for n in node_ids
                )
            out[who] = {
                "pips": tot,
                "ow": ow,
                "wb": wb,
                "kinds": len(kinds),
                "ports": ports,
            }
        first = self.order[0][1] if self.order else None
        out["bot_first"] = first == self.you
        out["lr_first"] = None
        self.res = out

    def after(self, game):
        pass


def facts(args):
    path, bot_won = args
    try:
        record = convert(load_capture(path)).record
        you = [c for c, p in record["players"].items() if p["you"]]
        if len(you) != 1 or len(record["seats"]) != 2:
            return None
        you = you[0]
        them = next(c for c in record["seats"] if c != you)
        obs = Obs(you, them)
        replay(record, observer=obs)
        if obs.res is None:
            return None
        obs.res["won"] = bot_won
        return obs.res
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None


def boot(vals, n=2000):
    rng = random.Random(1)
    m = sum(vals) / len(vals)
    bs = sorted(sum(rng.choices(vals, k=len(vals))) / len(vals) for _ in range(n))
    return f"{m:+.2f} [{bs[int(0.025 * n)]:+.2f}, {bs[int(0.975 * n)]:+.2f}]"


def main():
    with open("data/human/index.jsonl") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    rows = {r["capture"]: r for r in rows if r.get("status") == "ok"}
    caps = sorted(rows)[-LAST:]
    jobs = [
        (os.path.join("captures", rows[c]["account"], c), rows[c]["bot_won"])
        for c in caps
    ]
    with Pool(WORKERS) as pool:
        res = [r for r in pool.map(facts, jobs, chunksize=8) if r]
    print(f"games {len(res)}, skipped {len(jobs) - len(res)}")
    w = lambda sel: wilson(sum(r["won"] for r in sel), len(sel))
    print(f"all: bot wins {w(res)}")
    for k in ("pips", "ow", "wb", "kinds", "ports"):
        d = [r["bot"][k] - r["person"][k] for r in res]
        mb = sum(r["bot"][k] for r in res) / len(res)
        mp = sum(r["person"][k] for r in res) / len(res)
        print(f"{k}: bot {mb:.2f} person {mp:.2f} diff {boot(d)}")
    for bf in (True, False):
        sel = [r for r in res if r["bot_first"] == bf]
        d = [r["bot"]["pips"] - r["person"]["pips"] for r in sel]
        print(f"bot placed first={bf}: n {len(sel)} wins {w(sel)} pip diff {boot(d)}")
    print("by pip difference (bot - person):")
    buckets = defaultdict(list)
    for r in res:
        d = r["bot"]["pips"] - r["person"]["pips"]
        key = (
            "<=-4"
            if d <= -4
            else (
                "-3..-1" if d < 0 else "0..2" if d <= 2 else "3..5" if d <= 5 else ">=6"
            )
        )
        buckets[key].append(r)
    for key in ("<=-4", "-3..-1", "0..2", "3..5", ">=6"):
        sel = buckets[key]
        if sel:
            print(f"  {key}: n {len(sel)} wins {w(sel)}")
    print("by ore+wheat pip difference:")
    buckets = defaultdict(list)
    for r in res:
        d = r["bot"]["ow"] - r["person"]["ow"]
        key = "<=-3" if d <= -3 else "-2..2" if d <= 2 else ">=3"
        buckets[key].append(r)
    for key in ("<=-3", "-2..2", ">=3"):
        sel = buckets[key]
        if sel:
            print(f"  {key}: n {len(sel)} wins {w(sel)}")
    print("by wood+brick pip difference: n, bot wins, person took longest road first")
    buckets = defaultdict(list)
    for r in res:
        d = r["bot"]["wb"] - r["person"]["wb"]
        key = "<=-3" if d <= -3 else "-2..2" if d <= 2 else ">=3"
        buckets[key].append(r)
    for key in ("<=-3", "-2..2", ">=3"):
        sel = buckets[key]
        if sel:
            pf = sum(r["lr_first"] == "person" for r in sel)
            print(
                f"  {key}: n {len(sel)} wins {w(sel)} person LR first {wilson(pf, len(sel))}"
            )
    print("wood+brick diff <= -3 vs > -3, within ore+wheat buckets (wins):")
    for ok in ("<=-3", "-2..2", ">=3"):
        for wk in ("wb<=-3", "wb>-3"):
            sel = []
            for r in res:
                do = r["bot"]["ow"] - r["person"]["ow"]
                dw = r["bot"]["wb"] - r["person"]["wb"]
                kk = "<=-3" if do <= -3 else "-2..2" if do <= 2 else ">=3"
                if kk == ok and (dw <= -3) == (wk == "wb<=-3"):
                    sel.append(r)
            if sel:
                pf = sum(r["lr_first"] == "person" for r in sel)
                print(
                    f"  ow {ok} {wk}: n {len(sel)} wins {w(sel)} person LR first {wilson(pf, len(sel))}"
                )
    with open("data/orchestrator/live-loss/opening_rows.json", "w") as f:
        json.dump(res, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
