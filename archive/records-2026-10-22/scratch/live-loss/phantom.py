"""Lead's study (Oct 22): our games vs one strong player, against the 2300+ band.
Replays each game once and writes per-game, per-side stats to
data/orchestrator/analysis/phantom_stats.json. Internal only.

Usage: phantom.py scan   (replays target games + every ranked game with opp 2300+)
       phantom.py report"""

import json
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay

NAME = "thephantom"
TARGETS = [
    "bot3/bridge-20261019-020451.json",
    "bot3/bridge-20261019-022343.json",
    "bot3/bridge-20261021-032551.json",
    "bot4/bridge-20261016-015447.json",
    "bot4/bridge-20261016-020519.json",
    "bot5/bridge-20261015-055717.json",
    "bot5/bridge-20261018-091307.json",
    "bot9/bridge-20261021-040025.json",
]
SCAN = Path("data/orchestrator/analysis/rematch_scan.json")
OUT = Path("data/orchestrator/analysis/phantom_stats.json")
BUILD = {
    "BUILD_ROAD": "R",
    "BUILD_SETTLEMENT": "S",
    "BUILD_CITY": "C",
    "BUY_DEVELOPMENT_CARD": "D",
}
PLAY = (
    "PLAY_KNIGHT_CARD",
    "PLAY_YEAR_OF_PLENTY",
    "PLAY_MONOPOLY",
    "PLAY_ROAD_BUILDING",
)


def pips(n):
    return 0 if n is None else 6 - abs(7 - n)


def val(x):
    return getattr(x, "value", x)


def tile_pips_for(b, tile, col):
    """Production of colour col on this tile (cities count twice)."""
    out = 0
    for node in tile.nodes.values():
        if node in b.buildings and val(b.buildings[node][0]) == col:
            out += pips(tile.number) * (2 if val(b.buildings[node][1]) == "CITY" else 1)
    return out


class Obs:
    def __init__(self, seat):
        self.seat = seat  # {"bot": color, "person": color}
        self.side = {v: k for k, v in seat.items()}
        self.d = {
            w: {
                "builds": [],
                "buys": 0,
                "plays": Counter(),
                "rob": [],
                "bank": 0,
                "turns": 0,
                "first": {},
            }
            for w in seat
        }
        self.order = []
        self.opening = None
        self.first_mover = None

    def before(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        b = s.board
        if s.is_initial_build_phase:
            for node, (color, _) in b.buildings.items():
                if (node, val(color)) not in self.order:
                    self.order.append((node, val(color)))
            return
        if self.opening is None:
            self.opening = self.open_stats(b)
            self.first_mover = self.side.get(self.order[0][1]) if self.order else None
        who = self.side.get(val(decision.color))
        idx = {val(c): i for c, i in s.color_to_index.items()}
        p = s.player_state
        for w, col in self.seat.items():
            for key in ("HAS_ROAD", "HAS_ARMY"):
                if p[f"P{idx[col]}_{key}"] and key not in self.d[w]["first"]:
                    self.d[w]["first"][key] = self.d[w]["turns"]
        if who is None:
            return
        me = self.d[who]
        t = val(decision.action_type)
        if t == "ROLL":
            me["turns"] += 1
        elif t in BUILD:
            if t == "BUY_DEVELOPMENT_CARD":
                me["buys"] += 1
            me["builds"].append((me["turns"], BUILD[t]))
        elif t in PLAY:
            me["plays"][t] += 1
        elif t == "MARITIME_TRADE":
            me["bank"] += 1
        elif t == "MOVE_ROBBER":
            coord = decision.value[0]
            tile = b.map.land_tiles[coord]
            other = self.seat["bot" if who == "person" else "person"]
            blocked = tile_pips_for(b, tile, other)
            own = tile_pips_for(b, tile, self.seat[who])
            best = max(
                tile_pips_for(b, tl, other)
                for c, tl in b.map.land_tiles.items()
                if c != s.board.robber_coordinate
                and tile_pips_for(b, tl, self.seat[who]) == 0
            )
            me["rob"].append(
                {
                    "turn": me["turns"],
                    "blocked": blocked,
                    "own": own,
                    "best": best,
                    "knight": me.get("last") == "PLAY_KNIGHT_CARD",
                }
            )
        me["last"] = t

    def open_stats(self, b):
        out = {}
        for w, col in self.seat.items():
            tot = ow = wb = 0
            kinds = set()
            nums = []
            for node, (color, _) in b.buildings.items():
                if val(color) != col:
                    continue
                for tile in b.map.adjacent_tiles[node]:
                    if tile.resource is None:
                        continue
                    pp = pips(tile.number)
                    tot += pp
                    nums.append(tile.number)
                    r = val(tile.resource)
                    kinds.add(r)
                    ow += pp if r in ("ORE", "WHEAT") else 0
                    wb += pp if r in ("WOOD", "BRICK") else 0
            ports = []
            for res, node_ids in b.map.port_nodes.items():
                if any(
                    n in b.buildings and val(b.buildings[n][0]) == col for n in node_ids
                ):
                    ports.append(val(res) or "3:1")
            out[w] = {
                "pips": tot,
                "ow": ow,
                "wb": wb,
                "kinds": len(kinds),
                "ports": ports,
                "nums": sorted(nums),
            }
        return out

    def after(self, game):
        s = game.state
        idx = {val(c): i for c, i in s.color_to_index.items()}
        p = s.player_state
        self.final = {}
        for w, col in self.seat.items():
            i = idx[col]
            self.final[w] = {
                "vp": p[f"P{i}_ACTUAL_VICTORY_POINTS"],
                "settlements": 5 - p[f"P{i}_SETTLEMENTS_AVAILABLE"],
                "cities": 4 - p[f"P{i}_CITIES_AVAILABLE"],
                "roads": 15 - p[f"P{i}_ROADS_AVAILABLE"],
                "road": bool(p[f"P{i}_HAS_ROAD"]),
                "army": bool(p[f"P{i}_HAS_ARMY"]),
                "vp_cards": p[f"P{i}_VICTORY_POINT_IN_HAND"],
            }
        self.num_turns = s.num_turns


def facts(key):
    path = f"captures/{key}"
    try:
        frames = load_capture(path)
        conv = convert(frames)
        record = conv.record
        you = [c for c, pl in record["players"].items() if pl["you"]]
        if len(you) != 1 or len(record["seats"]) != 2:
            return key, None
        you = you[0]
        them = next(c for c in record["seats"] if c != you)
        name = (record["players"][them].get("name") or "").lower()
        obs = Obs({"bot": you, "person": them})
        rep = replay(record, observer=obs)
        if not rep.ok or obs.opening is None:
            return key, None
        return key, {
            "target": name == NAME,
            "game_id": record.get("source", {}).get("colonist_game"),
            "started": record.get("source", {}).get("started"),
            "you": you,
            "them": them,
            "first_mover": obs.first_mover,
            "opening": obs.opening,
            "sides": obs.d,
            "final": obs.final,
            "num_turns": obs.num_turns,
            "winner": rep.winner,
            "resigned": record.get("resigned"),
        }
    except Exception as error:
        print(f"{key}: {error!r}", file=sys.stderr)
        return key, None


def do_scan():
    cache = json.loads(SCAN.read_text())
    keys = list(TARGETS)
    for k, s in cache.items():
        if s and (s.get("opp_mmr") or 0) >= 2300 and k not in keys:
            keys.append(k)
    print(f"{len(keys)} games", flush=True)
    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    todo = [k for k in keys if k not in out]
    with Pool(4) as pool:
        for n, (k, f) in enumerate(pool.imap_unordered(facts, todo, chunksize=4)):
            out[k] = f
            if n % 100 == 99:
                OUT.write_text(json.dumps(out))
                print(f"{n + 1} done", flush=True)
    OUT.write_text(json.dumps(out))
    print("SCAN DONE", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "scan":
        do_scan()
    elif sys.argv[1] == "one":
        print(json.dumps(facts(TARGETS[0])[1])[:3000])
