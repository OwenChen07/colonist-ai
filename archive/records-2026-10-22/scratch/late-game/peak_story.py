"""Late-game reading: the bot's stated win chance (bridge.log, the live search's
number) over each ranked game, by opponent band, and for 2300+ games a replay that
counts what each side does from the bot's peak to the end.

Curves: bridge.log 'move: ... (win chance N%' lines between 'game started' and
'game saved to <capture>'. Bot turn k = number of the bot's 'roll' moves so far;
the setup moves are bot turn 0 and are left out of the peak.
Outcome: games.jsonl winner == bot_color. Opponent rating: analysis caches
(opponent_ratings.json, opponent_hash.json), else read from the capture.
Writes data/orchestrator/scratch/late-game/peak_story.json. Aggregates only.
Usage: peak_story.py [workers]"""

import json
import math
import re
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from lvlib import ACCOUNTS, counted, scan as scan_opp  # noqa: E402

from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.player import Color  # noqa: E402

OUT = Path("data/orchestrator/scratch/late-game/peak_story.json")
PAT = re.compile(r"move: (.*) \(win chance (\d+)%")
BANDS = ((0, 2100), (2100, 2300), (2300, 9999))


def curves_for(acc):
    out = {}
    moves = []
    try:
        f = open(f"data/{acc}/bridge.log", errors="replace")
    except FileNotFoundError:
        return out
    for line in f:
        if "game started" in line:
            moves = []
        m = PAT.search(line)
        if m:
            moves.append((m.group(1).strip(), int(m.group(2))))
        if "game saved to" in line and moves:
            out[line.rstrip().rsplit("/", 1)[-1]] = moves
    return out


def per_turn(moves):
    """stated % at the bot's roll of each bot turn k>=1 (or first move of the turn)."""
    k = 0
    pts = []  # (k, p) for every move from turn 1 on
    for name, p in moves:
        if name == "roll":
            k += 1
        if k >= 1:
            pts.append((k, p))
    return pts


class Obs:
    def __init__(self, me):
        self.me = me
        self.k = 0
        self.ev = []  # (k, who, kind)
        self.snap = {}  # k -> dict
        self.rolls = (
            []
        )  # per roll: bot turn k, roller, expected and actual cards each side

    def before(self, game):
        pass

    def _vp(self, game, color):
        s = game.state
        i = s.color_to_index[color]
        ps = s.player_state
        return dict(
            vp=ps[f"P{i}_ACTUAL_VICTORY_POINTS"],
            pub=ps[f"P{i}_VICTORY_POINTS"],
            cities=4 - ps[f"P{i}_CITIES_AVAILABLE"],
            setts=5 - ps[f"P{i}_SETTLEMENTS_AVAILABLE"],
            road=ps[f"P{i}_HAS_ROAD"],
            army=ps[f"P{i}_HAS_ARMY"],
            knights=ps[f"P{i}_PLAYED_KNIGHT"],
            vpcards=ps[f"P{i}_VICTORY_POINT_IN_HAND"],
            devhand=sum(
                ps[f"P{i}_{d}_IN_HAND"]
                for d in (
                    "KNIGHT",
                    "MONOPOLY",
                    "YEAR_OF_PLENTY",
                    "ROAD_BUILDING",
                    "VICTORY_POINT",
                )
            ),
            res=sum(
                ps[f"P{i}_{r}_IN_HAND"]
                for r in ("WOOD", "BRICK", "SHEEP", "WHEAT", "ORE")
            ),
            roadlen=ps[f"P{i}_LONGEST_ROAD_LENGTH"],
        )

    def _hands(self, game):
        s = game.state
        out = {}
        for c in s.colors:
            i = s.color_to_index[c]
            w = "bot" if c == self.me else "opp"
            out[w] = sum(
                s.player_state[f"P{i}_{r}_IN_HAND"]
                for r in ("WOOD", "BRICK", "SHEEP", "WHEAT", "ORE")
            )
        return out

    def _expected(self, game, robber=True):
        """cards per roll each side's buildings yield on average (7 yields nothing)."""
        b = game.state.board
        out = {"bot": 0.0, "opp": 0.0}
        for coord, tile in b.map.land_tiles.items():
            if tile.number is None or (robber and b.robber_coordinate == coord):
                continue
            p = (6 - abs(7 - tile.number)) / 36
            for node in tile.nodes.values():
                bl = b.buildings.get(node)
                if bl:
                    out["bot" if bl[0] == self.me else "opp"] += p * (
                        2 if bl[1] == "CITY" else 1
                    )
        return out

    def step(self, game, d):
        if getattr(self, "pending", None) is not None:
            now = self._hands(game)
            for w in ("bot", "opp"):
                gain = now[w] - self.pending[w]
                if gain > 0:
                    self.ev.extend([(self.k, w, "income")] * gain)
                self.roll_rec[f"got_{w}"] = max(0, gain)
            self.pending = None
        t = d.action_type.value
        who = "bot" if d.color == self.me else "opp"
        if t == "ROLL":
            self.pending = self._hands(game)
            e, e0 = self._expected(game), self._expected(game, robber=False)
            self.roll_rec = dict(
                k=self.k + (who == "bot"),
                roller=who,
                exp_bot=e["bot"],
                exp_opp=e["opp"],
                free_bot=e0["bot"],
                free_opp=e0["opp"],
            )
            self.rolls.append(self.roll_rec)
        if t == "ROLL" and who == "bot":
            self.k += 1
            opp = [c for c in game.state.colors if c != self.me][0]
            self.snap[self.k] = dict(
                turn=game.state.num_turns,
                bot=self._vp(game, self.me),
                opp=self._vp(game, opp),
            )
        kind = {
            "BUY_DEVELOPMENT_CARD": "dev_buy",
            "PLAY_KNIGHT_CARD": "knight",
            "PLAY_MONOPOLY": "monopoly",
            "PLAY_YEAR_OF_PLENTY": "yop",
            "PLAY_ROAD_BUILDING": "road_building",
            "BUILD_CITY": "city",
            "BUILD_SETTLEMENT": "settlement",
            "BUILD_ROAD": "road",
            "MARITIME_TRADE": "bank_trade",
            "DISCARD": "discard",
        }.get(t)
        if t == "MOVE_ROBBER":
            v = d.value
            victim = v[1] if isinstance(v, (tuple, list)) and len(v) > 1 else None
            kind = "robber_on_other" if victim is not None else "robber_nobody"
        if kind:
            self.ev.append((self.k, who, kind))

    def after(self, game):
        opp = [c for c in game.state.colors if c != self.me][0]
        self.final = dict(
            turn=game.state.num_turns,
            bot=self._vp(game, self.me),
            opp=self._vp(game, opp),
        )


def replay_one(job):
    path, me = job
    try:
        conv = convert(load_capture(path))
        if conv.unsupported:
            return None
        obs = Obs(Color[me])
        rep = replay(conv.record, observer=obs)
        if not rep.ok:
            return None
        return dict(ev=obs.ev, snap=obs.snap, final=obs.final, rolls=obs.rolls)
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)[:200]}


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    rc = json.loads(
        Path("data/orchestrator/analysis/opponent_ratings.json").read_text()
    )
    oc = json.loads(Path("data/orchestrator/analysis/opponent_hash.json").read_text())
    games = []
    for acc in ACCOUNTS:
        cv = curves_for(acc)
        p = Path(f"data/{acc}/games.jsonl")
        if not p.exists():
            continue
        for line in open(p):
            if not line.strip():
                continue
            g = json.loads(line)
            if (
                g.get("queue") != "ranked"
                or g.get("own_bot")
                or g.get("winner") is None
                or not counted(acc, g)
            ):
                continue
            mv = cv.get(g["capture"])
            if not mv:
                continue
            mmr = (oc.get(g["capture"]) or {}).get("opp_mmr") or (
                rc.get(g["capture"]) or {}
            ).get("opp_mmr")
            if mmr is None:
                cap = Path("captures") / acc / g["capture"]
                if cap.exists():
                    try:
                        mmr = scan_opp(cap).get("opp_mmr")
                    except Exception:  # noqa: BLE001
                        mmr = None
            pts = per_turn(mv)
            if not pts:
                continue
            peak_k, peak = max(pts, key=lambda x: (x[1], -x[0]))
            peak_k = min(k for k, p in pts if p == peak)
            first = {}
            for T in (60, 70, 80, 90):
                ks = [k for k, p in pts if p >= T]
                first[T] = ks[0] if ks else None
            at_turn = {}
            for kk in (5, 10, 15, 20, 25, 30):
                v = [p for k, p in pts if k == kk]
                at_turn[kk] = v[0] if v else None
            games.append(
                dict(
                    acc=acc,
                    cap=g["capture"],
                    color=g["bot_color"],
                    won=g["winner"] == g["bot_color"],
                    ended=g.get("ended"),
                    label=g.get("label") or "",
                    at=g["at"],
                    mmr=mmr,
                    peak=peak,
                    peak_k=peak_k,
                    last_k=pts[-1][0],
                    first=first,
                    at_turn=at_turn,
                    open_p=mv[3][1] if len(mv) > 3 else None,
                )
            )
    print(f"{len(games)} ranked games with curves", file=sys.stderr)
    hi = [g for g in games if g["mmr"] and g["mmr"] >= 2300]
    jobs = [(f"captures/{g['acc']}/{g['cap']}", g["color"]) for g in hi]
    with Pool(workers) as pool:
        res = pool.map(replay_one, jobs, chunksize=2)
    for g, r in zip(hi, res):
        g["replay"] = r if r and "error" not in r else None
    OUT.write_text(json.dumps(games))
    print(
        f"2300+: {len(hi)}, replayed {sum(1 for g in hi if g['replay'])}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
