"""E63 prototype: plan-summary features per player, and their cost.

    e63_plan.py GAMES SEED

Per player: longest road now and with 1, 2, 3 more roads (a trail through own
roads plus up to k empty land edges, starting where the player already is,
stopped by enemy buildings and never using enemy roads); empty edges needed to
join two of the player's networks (capped at 4); empty edges to the nearest
free legal settlement spot (capped at 4). Positions from random-player games
(turns 30+), timed per call. Also checks L0 against catanatron's own length.
"""

import random
import sys
import time
from collections import deque

import numpy as np

from catanatron.game import Game
from catanatron.models.board import STATIC_GRAPH
from catanatron.models.player import Color, RandomPlayer

NB = {n: tuple(STATIC_GRAPH.neighbors(n)) for n in STATIC_GRAPH.nodes}
CAP = 4


def plan_features(board, color):
    roads, buildings = board.roads, board.buildings
    land = board.map.land_nodes

    def enemy(n):
        b = buildings.get(n)
        return b is not None and b[0] != color

    def usable(a, b):  # (cost or None)
        r = roads.get((a, b))
        if r == color:
            return 0
        if r is None and a in land and b in land:
            return 1
        return None

    mine = {n for n in land if (buildings.get(n, (None,))[0] == color)}
    for (a, b), r in roads.items():
        if r == color:
            mine.add(a)
            mine.add(b)
    best = [0, 0, 0, 0]

    def walk(node, used, length, cost):
        for k in range(cost, 4):
            if length > best[k]:
                best[k] = length
        if length and enemy(node):
            return
        for nb in NB[node]:
            c = usable(node, nb)
            if c is None or cost + c > 3:
                continue
            e = (node, nb) if node < nb else (nb, node)
            if e in used:
                continue
            used.add(e)
            walk(nb, used, length + 1, cost + c)
            used.discard(e)

    for s in mine:
        if not enemy(s):
            walk(s, set(), 0, 0)

    # Networks: components of own roads (and lone buildings).
    comp, cid = {}, 0
    for s in mine:
        if s in comp:
            continue
        stack = [s]
        comp[s] = cid
        while stack:
            n = stack.pop()
            if enemy(n):
                continue
            for nb in NB[n]:
                if roads.get((n, nb)) == color and nb not in comp:
                    comp[nb] = cid
                    stack.append(nb)
        cid += 1

    def bfs(sources, goal):
        dist = {s: 0 for s in sources}
        q = deque(sources)
        while q:
            n = q.popleft()
            d = dist[n]
            if d >= CAP:
                continue
            if d and enemy(n):
                continue
            for nb in NB[n]:
                c = usable(n, nb)
                if c is None or nb in dist:
                    continue
                if c == 0:
                    dist[nb] = d
                    q.appendleft(nb)
                else:
                    dist[nb] = d + 1
                    q.append(nb)
                if goal(nb, dist[nb]):
                    return dist[nb]
        return CAP

    join = CAP
    if cid > 1:
        for c in range(cid):
            src = [n for n, k in comp.items() if k == c]
            join = min(join, bfs(src, lambda n, d, c=c: comp.get(n, c) != c))

    def free_spot(n, d):
        return (
            n not in buildings and not any(m in buildings for m in NB[n]) and n in land
        )

    spot = bfs(list(mine), free_spot) if mine else CAP
    return best, join, spot


def main(games, seed):
    rng = random.Random(int(seed))
    times, rows, bad = [], [], 0
    for g in range(int(games)):
        game = Game(
            [RandomPlayer(Color.RED), RandomPlayer(Color.BLUE)],
            seed=rng.randrange(10**9),
        )
        while game.winning_color() is None and game.state.num_turns < 200:
            game.play_tick()
            if game.state.num_turns >= 30 and rng.random() < 0.05:
                for color in game.state.colors:
                    t = time.perf_counter()
                    best, join, spot = plan_features(game.state.board, color)
                    times.append(time.perf_counter() - t)
                    rows.append([*best, join, spot])
                    if best[0] != game.state.board.road_lengths.get(color, 0):
                        bad += 1
    t = np.asarray(times) * 1e3
    a = np.asarray(rows)
    print(
        f"calls {len(t)}  ms mean {t.mean():.3f}  median {np.median(t):.3f}  p99 {np.percentile(t, 99):.3f}  max {t.max():.3f}"
    )
    print(f"L0 mismatches vs catanatron: {bad}")
    print("mean L0 L1 L2 L3 join spot:", np.round(a.mean(0), 2))
    print(
        "share join<=3:",
        round(float((a[:, 4] <= 3).mean()), 3),
        " gain L3-L0 mean:",
        round(float((a[:, 3] - a[:, 0]).mean()), 2),
    )


if __name__ == "__main__":
    main(*sys.argv[1:])
