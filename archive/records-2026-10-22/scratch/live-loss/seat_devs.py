"""Held dev cards per seat (action vs VP) from turn TURN on, in human-seat games
(round 66 vs models/human/net.npz) and plain self-play, training's search
settings (400 sims, 24 fast, full-rate 0.25). For training's E13 check.

usage: seat_devs.py GAMES_PER_KIND [TURN] [SIMS]
"""

import multiprocessing as mp
import os
import random
import sys

import numpy as np

ROOT = "/home/owen/colonist-ai"
os.chdir(ROOT)
sys.path.insert(0, f"{ROOT}/catanatron")
for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(var, "1")

NET = "runs/g1/nets/round_066.npz"
HUMAN = "models/human/net.npz"
ARGS = {"simulations": 400, "fast_simulations": 24, "full_rate": 0.25}
CHUNK = 8


def play(task):
    kind, first, count, turn_min, args = task
    from catanatron.colonist import selfplay as S
    from catanatron.colonist.net import Net
    from catanatron.game import Game
    from catanatron.state import DEVELOPMENT_CARDS
    from catanatron.state_functions import player_key

    rows = []
    seen = set()
    real = set()
    orig = Game.execute

    def execute(self, *a, **k):
        out = orig(self, *a, **k)
        s = self.state
        key = (id(self), s.num_turns)
        if (
            id(self) in real
            and s.num_turns >= turn_min
            and key not in seen
            and not s.is_initial_build_phase
        ):
            seen.add(key)
            for c in s.colors:
                p = player_key(s, c)
                hand = {
                    d: s.player_state[f"{p}_{d}_IN_HAND"] for d in DEVELOPMENT_CARDS
                }
                rows.append((self._seed_tag, c.value, hand))
        return out

    Game.execute = execute
    net = Net.load(NET)
    human = S.HumanModel(Net.load(HUMAN)) if kind == "human" else None
    seeds = range(first, first + count)
    # tag each game with its seed so rows can be grouped and seats looked up
    games = []
    for s in seeds:
        seats = S.seat_plan(s, net, [], 0.0, None, human, 1.0 if human else 0.0)
        g = S._Game(s, args, seats, net)
        g.game._seed_tag = s
        real.add(id(g.game))
        games.append(g)
    # same loop as play_games, on our tagged games
    pending = {}
    for g in games:
        step = g.advance()
        if step is not None:
            pending[g] = step
    from catanatron.colonist import encode as E

    while pending:
        by_net = {}
        for g, (n, nodes) in pending.items():
            by_net.setdefault(id(n), (n, []))[1].extend(nodes)
        for n, nodes in by_net.values():
            enc = [E.encode(node.game, node.color) for node in nodes]
            logits, values = n.forward_rows(
                [st for st, _ in enc], np.stack([d for _, d in enc])
            )
            for r, node in enumerate(nodes):
                legal = E.legal_indices(node.actions)
                node.set_evaluation(
                    logits[r, legal].astype(np.float64), float(values[r])
                )
        for g in list(pending):
            step = g.advance()
            if step is None:
                del pending[g]
            else:
                pending[g] = step
    seat = {}
    winner = {}
    for g in games:
        for c, agent in g.seats.items():
            seat[(g.seed, c.value)] = (
                "human" if isinstance(agent, S._HumanSeat) else "net"
            )
        w = g.game.winning_color()
        winner[g.seed] = None if w is None else w.value
    out = []
    for sd, c, hand in rows:
        out.append((kind, sd, seat[(sd, c)], c == winner[sd], hand))
    return out


def ci(groups, f, reps=1000):
    rng = random.Random(0)
    keys = list(groups)
    per = {k: [f(h) for h in groups[k]] for k in keys}
    mean = lambda ks: sum(sum(per[k]) for k in ks) / max(
        1, sum(len(per[k]) for k in ks)
    )
    m = mean(keys)
    boots = sorted(mean([rng.choice(keys) for _ in keys]) for _ in range(reps))
    return f"{m:.2f} [{boots[25]:.2f}, {boots[974]:.2f}]"


OUT = "data/orchestrator/live-loss/seat_devs_out.txt"


def main():
    # keep a log ourselves: the first run died after 5 h with nothing kept
    log = open("data/orchestrator/jobs/seat_devs.log", "w", buffering=1)
    sys.stdout = sys.stderr = log
    with open("data/orchestrator/jobs/seat_devs.pid", "w") as stream:
        stream.write(f"{os.getpid()}\n")
    lines = []

    def say(text):  # printed and kept for the results file
        lines.append(text)
        print(text, flush=True)

    n = int(sys.argv[1])
    turn_min = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    # third argument: plain search with that many sims instead of training's
    args = {"simulations": int(sys.argv[3])} if len(sys.argv) > 3 else ARGS
    tasks = []
    for kind, base in (("human", 7_100_000_000), ("self", 7_200_000_000)):
        for first in range(base, base + n, CHUNK):
            tasks.append((kind, first, min(CHUNK, base + n - first), turn_min, args))
    print(f"{len(tasks)} tasks", flush=True)
    rows = []
    with mp.Pool(3) as pool:
        for i, part in enumerate(pool.imap_unordered(play, tasks)):
            rows.extend(part)
            print(f"task {i + 1}/{len(tasks)} done, {len(rows)} rows", flush=True)
    from catanatron.state import DEVELOPMENT_CARDS

    action = [d for d in DEVELOPMENT_CARDS if d != "VICTORY_POINT"]
    for kind, who in (("human", "human"), ("human", "net"), ("self", "net")):
        for won in (None, True, False):
            groups = {}
            for k, sd, s, w, hand in rows:
                if k == kind and s == who and (won is None or w == won):
                    groups.setdefault(sd, []).append(hand)
            if not groups:
                continue
            npos = sum(len(v) for v in groups.values())
            tag = {None: "all", True: "won", False: "lost"}[won]
            say(
                f"{kind:5} seat={who:5} {tag:4} games {len(groups):3} positions {npos:5}"
                f"  total {ci(groups, lambda h: sum(h.values()))}"
                f"  action {ci(groups, lambda h: sum(h[d] for d in action))}"
                f"  VP {ci(groups, lambda h: h['VICTORY_POINT'])}"
            )
            if won is None:
                say(
                    "      "
                    + "  ".join(
                        f"{d[:6]} {ci(groups, lambda h, d=d: h[d])}" for d in action
                    )
                )
    with open(OUT, "w") as stream:
        stream.write(" ".join(sys.argv[1:]) + "\n" + "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
