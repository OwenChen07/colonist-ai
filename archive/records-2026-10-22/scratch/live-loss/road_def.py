"""Road-defence playouts (long_games.py follow-up). Bot decisions at roll 50+ of
2100+ ranked games where the bot holds Longest Road by 1-2, a legal road would
lengthen its longest road, and the bot ended its turn instead. One a game, SIZE
from lost games and SIZE from won games.

Arm 'road' plays the road with the largest length gain (ties: first found);
arm 'bot' ends the turn; both sides then play on with NetPlayer (SIMS; 0 =
policy argmax), GAMES seeded games.

    build [WORKERS]                       -> live-loss/road_def.jsonl
    run NET SIMS GAMES [LIMIT] [WORKERS] [OPP]  -> road_def_forced_<net>_<sims>[_vs_<dir>].jsonl
      (OPP: a net for the other seat, policy argmax, e.g. the people model)
    sum FILE
"""

import json
import os
import random
import sys
import time
import zlib
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist.replay import replay  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from catanatron.state_functions import get_longest_road_color, player_key  # noqa: E402
from e75_robber import jobs, col  # noqa: E402
from forced_settle import playout  # noqa: E402
from road_set import summary  # noqa: E402

OUT = "data/orchestrator/live-loss/road_def.jsonl"
SIZE = 100
MIN_ROLLS = 50
R, E = ActionType.BUILD_ROAD, ActionType.END_TURN


def rl(state, color):
    return state.player_state.get(f"{player_key(state, color)}_LONGEST_ROAD_LENGTH", 0)


def margin(game, color):
    s = game.state
    if get_longest_road_color(s) != color:
        return None
    other = next(c for c in s.colors if c != color)
    return rl(s, color) - rl(s, other)


def best_road(game):
    """(road, length gain) or None"""
    color = game.state.current_color()
    base = rl(game.state, color)
    best = None
    for a in game.playable_actions:
        if a.action_type != R:
            continue
        g = game.copy()
        g.execute(a, validate_action=False)
        gain = rl(g.state, color) - base
        if gain > 0 and (best is None or gain > best[1]):
            best = (a, gain)
    return best


class Obs:
    def __init__(self, you, target=None):
        self.you, self.target = you, target
        self.k = -1
        self.rolls = 0
        self.rows = []
        self.game = None
        self.move = None

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, d):
        s = game.state
        if s.is_initial_build_phase:
            return
        if d.action_type == ActionType.ROLL:
            self.rolls += 1
        if col(d.color) != self.you or d.action_type != E or self.rolls < MIN_ROLLS:
            return
        m = margin(game, d.color)
        if m is None or not 1 <= m <= 2:
            return
        got = best_road(game)
        if got is None:
            return
        self.k += 1
        if self.target is not None:
            if self.k == self.target:
                self.game = game.copy()
                self.move = d
            return
        self.rows.append(
            {
                "k": self.k,
                "turn": s.num_turns,
                "roll": self.rolls,
                "margin": m,
                "gain": got[1],
            }
        )


def init():
    os.nice(19)


def find(job):
    from robber_set import players

    path, won, rolls = job
    try:
        record, you, them = players(path)
        if record is None:
            return []
        obs = Obs(you)
        if replay(record, observer=obs).errors:
            return []
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return []
    if not obs.rows:
        return [{"capture": path, "won": won, "none": True}]
    r = random.Random(path).choice(obs.rows)
    return [dict(r, capture=path, won=won, rolls=rolls, n=len(obs.rows))]


def build(workers):
    todo = jobs()
    print(f"{len(todo)} games", flush=True)
    with Pool(workers, initializer=init) as pool:
        rows = [r for part in pool.map(find, todo, chunksize=8) for r in part]
    picked = []
    for won in (False, True):
        g = [r for r in rows if r["won"] == won]
        c = [r for r in g if not r.get("none")]
        print(
            f"won={won}: {len(g)} games replayed; {len(c)} with a skipped defending road;"
            f" such turn ends a game mean {np.mean([r['n'] for r in c]):.1f}"
        )
        random.Random(77).shuffle(c)
        picked += c[:SIZE]
    picked.sort(key=lambda r: (r["capture"], r["k"]))
    with open(OUT, "w") as f:
        for r in picked:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(picked)} positions to {OUT}")
    for won in (False, True):
        p = [r for r in picked if r["won"] == won]
        print(
            f"  won={won}: {len(p)}; margin 1 {np.mean([r['margin'] == 1 for r in p]):.0%};"
            f" gain {np.mean([r['gain'] for r in p]):.2f}; roll median {int(np.median([r['roll'] for r in p]))}"
        )


def playout_vs(game, first, seed, net, sims, opp):
    """playout with the bot's seat on NET and the other seat on OPP (policy argmax)"""
    from catanatron.colonist.mcts import NetPlayer

    g = game.copy()
    g.random = g.state.random = random.Random(seed)
    random.seed(seed)
    g.state.random.shuffle(g.state.development_listdeck)
    me = g.state.current_color()
    g.state.players = [
        NetPlayer(
            p.color,
            NetPlayer.Params(
                path=net if p.color == me else opp,
                simulations=sims if p.color == me else 0,
                seed=seed,
            ),
        )
        for p in g.state.players
    ]
    g.execute(first, validate_action=False)
    ticks = 0
    while g.winning_color() is None and ticks < 3000:
        g.play_tick()
        ticks += 1
    w = g.winning_color()
    return None if w is None else w == me, g.state.num_turns


def one(args):
    from robber_set import players

    r, net, sims, games, opp = args
    try:
        record, you, them = players(r["capture"])
        obs = Obs(you, target=r["k"])
        replay(record, observer=obs)
    except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None
    game = obs.game
    if game is None or obs.move is None:
        return None
    best = best_road(game)[0]
    real = obs.move
    t0 = time.time()
    base = zlib.crc32(f"{r['capture']}:{r['k']}".encode())
    res = {"road": [], "bot": []}
    for i in range(games):
        for arm, first in (("road", best), ("bot", real)):
            if opp:
                res[arm].append(playout_vs(game, first, base + i, net, sims, opp))
            else:
                res[arm].append(playout(game, first, base + i, net, sims))
    return {
        "capture": r["capture"],
        "k": r["k"],
        "won": r["won"],
        "real": f"m{r['margin']}",
        "turn0": game.state.num_turns,
        "res": res,
        "secs": round(time.time() - t0, 1),
    }


def run(net, sims, games, limit, workers, opp=None):
    rows = [json.loads(line) for line in open(OUT)]
    random.Random(5).shuffle(rows)
    rows = rows[:limit]
    vs = f"_vs_{os.path.basename(os.path.dirname(opp))}" if opp else ""
    out = OUT.replace(".jsonl", f"_forced_{os.path.basename(net)}_{sims}{vs}.jsonl")
    todo = [(r, net, sims, games, opp) for r in rows]
    n = 0
    with Pool(workers, initializer=init) as pool, open(out, "w") as f:
        for x in pool.imap_unordered(one, todo):
            n += 1
            if x is not None:
                f.write(json.dumps(x) + "\n")
                f.flush()
            print(f"done {n}/{len(todo)}", flush=True)
    print(f"wrote {out}")
    summary(out)


def main():
    cmd = sys.argv[1]
    if cmd == "sum":
        summary(sys.argv[2])
    elif cmd == "build":
        build(int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    else:
        net, sims, games = sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
        limit = int(sys.argv[5]) if len(sys.argv) > 5 else 200
        workers = int(sys.argv[6]) if len(sys.argv) > 6 else 3
        opp = sys.argv[7] if len(sys.argv) > 7 else None
        run(net, sims, games, limit, workers, opp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
