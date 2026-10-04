"""Record training positions from baseline-bot games (PLAN.md, Phase 2).

    python -m catanatron.colonist.datagen data/ab2 --games 40000
    python -m catanatron.colonist.datagen data/ab2 --games 40000   # resumes

Games are played in shards of --shard-size games, one shard per task, and each
shard is written to its own file when it finishes. A rerun skips shards that
are already on disk, so a long run can be stopped and restarted (or moved to
another machine) without losing more than the shards in progress. Game i is a
pure function of its seed, so the data is the same however it is split.

From each game, up to --keep positions are kept, drawn evenly from the
decisions with more than one legal move. Each position stores:

  dynamic   the dynamic inputs, as the player to move sees them (float16)
  game      which game (row of `static`) the board inputs are in
  legal     the legal moves, as a bitmask over the 313 moves
  action    the move the bot chose (-1 if it was a random exploration move)
  z         the game result for the player to move: 1 win, -1 loss, 0 none
  turn      the turn number

and each game stores its board inputs once (`static`, float32) and its seed.

--epsilon makes each player pick a random legal move at that rate, so the data
covers positions a little off the bot's own path; those moves are not used as
policy targets.
"""

import argparse
import multiprocessing
import os
import random
import signal
import sys
import time

import numpy as np

from catanatron.colonist import encode as E


def _bots(spec):
    import catanatron.players  # noqa: F401
    from catanatron.models.player import Color
    from catanatron.registry import REGISTRY

    return [REGISTRY.build(spec, c) for c in (Color.RED, Color.BLUE)]


def play_one(spec, seed, keep, epsilon, max_turns=1000):
    """One game; returns (static, rows) where rows are the kept positions."""
    from catanatron.colonist.rules import new_game

    random.seed(seed)
    players = _bots(spec)
    game = new_game(players, seed=seed)
    explore = random.Random(seed ^ 0x5EED)
    positions = []
    while game.winning_color() is None and game.state.num_turns < max_turns:
        actions = game.playable_actions
        if len(actions) == 1:
            game.execute(actions[0])
            continue
        color = game.state.current_color()
        player = game.state.current_player()
        if epsilon and explore.random() < epsilon:
            action, target = explore.choice(actions), -1
        else:
            action = player.decide(game, actions)
            target = E.action_index(action)
        _, dynamic = E.encode(game, color)
        mask = np.zeros(E.NUM_ACTIONS, bool)
        mask[E.legal_indices(actions)] = True
        positions.append((dynamic, mask, target, color, game.state.num_turns))
        game.execute(action)
    winner = game.winning_color()
    if len(positions) > keep:
        picks = sorted(explore.sample(range(len(positions)), keep))
        positions = [positions[i] for i in picks]
    rows = [
        (d, m, t, 0 if winner is None else (1 if c == winner else -1), turn)
        for d, m, t, c, turn in positions
    ]
    return E.encode_static(game.state.board.map), rows


def run_shard(task):
    """Plays one shard and writes it. Runs in a worker."""
    out_dir, spec, first, count, keep, epsilon = task
    os.makedirs(out_dir, exist_ok=True)
    path = shard_path(out_dir, first, count)
    statics, seeds, dyn, masks, actions, zs, turns, gidx = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    start = time.time()
    for g, seed in enumerate(range(first, first + count)):
        static, rows = play_one(spec, seed, keep, epsilon)
        statics.append(static)
        seeds.append(seed)
        for d, m, t, z, turn in rows:
            dyn.append(d)
            masks.append(m)
            actions.append(t)
            zs.append(z)
            turns.append(turn)
            gidx.append(g)
    tmp = path + ".tmp.npz"
    np.savez_compressed(
        tmp,
        static=np.stack(statics).astype(np.float32),
        seed=np.asarray(seeds, np.int64),
        dynamic=np.stack(dyn).astype(np.float16),
        game=np.asarray(gidx, np.int32),
        legal=np.packbits(np.stack(masks), axis=1),
        action=np.asarray(actions, np.int16),
        z=np.asarray(zs, np.int8),
        turn=np.asarray(turns, np.int16),
    )
    os.replace(tmp, path)
    return path, count, len(dyn), time.time() - start


def shard_path(out_dir, first, count):
    return os.path.join(out_dir, f"shard_{first:07d}_{count}.npz")


def load(paths):
    """Concatenate shards into one dict of arrays, with `game` renumbered to
    index the combined `static`."""
    parts = [dict(np.load(p)) for p in sorted(paths)]
    out, offset = {}, 0
    for part in parts:
        part["game"] = part["game"] + offset
        offset += len(part["static"])
    for key in parts[0]:
        out[key] = np.concatenate([p[key] for p in parts])
    out["legal"] = np.unpackbits(out["legal"], axis=1, count=E.NUM_ACTIONS).astype(bool)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out_dir")
    parser.add_argument("--games", type=int, default=40000)
    parser.add_argument("--bot", default="AB:2", help="registry spec, both seats")
    parser.add_argument("--seed", type=int, default=1_000_000, help="first game seed")
    parser.add_argument("--keep", type=int, default=40, help="positions per game")
    parser.add_argument("--epsilon", type=float, default=0.02)
    parser.add_argument("--shard-size", type=int, default=50)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    # One BLAS thread per worker process: the net's small matrix products
    # gain nothing from more, and threads across workers fight for cores.
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(var, "1")

    os.makedirs(args.out_dir, exist_ok=True)
    tasks = []
    for first in range(args.seed, args.seed + args.games, args.shard_size):
        count = min(args.shard_size, args.seed + args.games - first)
        if not os.path.exists(shard_path(args.out_dir, first, count)):
            tasks.append(
                (args.out_dir, args.bot, first, count, args.keep, args.epsilon)
            )
    done_before = (args.games + args.shard_size - 1) // args.shard_size - len(tasks)
    print(f"{len(tasks)} shards to play ({done_before} already done)", flush=True)
    if not tasks:
        return 0
    start, games, positions = time.time(), 0, 0
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(args.workers) as pool:
        for path, count, rows, secs in pool.imap_unordered(run_shard, tasks):
            games += count
            positions += rows
            rate = games / (time.time() - start)
            left = (len(tasks) * args.shard_size - games) / rate if rate else 0
            print(
                f"{os.path.basename(path)}: {count} games, {rows} positions, "
                f"{secs:.0f} s | {games} games, {rate:.2f}/s, ~{left / 60:.0f} min left",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
