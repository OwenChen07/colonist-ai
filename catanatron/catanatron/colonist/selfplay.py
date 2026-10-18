"""Self-play with the network and search (PLAN.md, Phase 3; benchmarked in Phase 2).

    python -m catanatron.colonist.selfplay models/ab2/net.npz data/sp1 --games 5000
    python -m catanatron.colonist.selfplay models/ab2/net.npz data/sp1 --games 5000  # resumes

Each worker process plays a shard of games all at once: every game's search is
a generator that pauses whenever it needs the network, and the pending
positions of all the games go through the network in one call. One position
at a time, the network costs several times more per position (PLAN.md, Time
and compute), so this is what makes self-play affordable.

A share of games (--opponent-rate) put a random opponent in one seat: an
older net or a reference bot, so the net doesn't only learn to beat itself.
Only the main net's moves are recorded from those games.

Human opponent (E13, --human-opponent, a model from opponent_model.py): a
--human-opponent-rate share of games put it in one seat, where it samples
each move from its policy without search, so the net learns to beat
people's style of play. Again only the main net's moves are recorded.

Every searched position is recorded, with the search's improved policy over
the moves (`pi`) and its value (`v`) as training targets, and the game result
(`z`) once the game ends. Shards are written and resumed as in datagen.py, in
the same format plus `pi` (float16, over the 313 moves), `v` and `full`.

Human starts (--human-starts, a starts.py file): a --human-start-rate share
of games start from a position of a real game against a person instead of
an empty board, and are played out as usual.

Each recorded position also gets auxiliary targets from the end of its game
(`aux`, graph.AUX_TARGETS): for the player to move, who holds longest road
and largest army (+1 they do, -1 the opponent, 0 nobody) and both players'
final points over 15. Training learns them alongside (train.py --aux-weight).

Playout cap randomization (KataGo, --full-rate below 1): only a random
--full-rate share of moves get the full search, and only those are policy
targets (`full`); the rest get a quick search of --fast-simulations without
exploration noise, which plays the game on cheaply, so a round has more games
and more value targets for the same compute. Initial placements always get the
full search.
"""

import argparse
import gc
import json
import math
import multiprocessing
import os
import queue
import random
import signal
import sys
import threading
import time

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.datagen import shard_path
from catanatron.colonist.graph import AUX_TARGETS
from catanatron.colonist.mcts import TURNS_LIMIT, Search
from catanatron.models.enums import ActionType


def load_opponent(spec):
    """An opponent: a net file (searched like the main net) or a registry
    bot spec such as AB:2 (asked for its move directly)."""
    if spec.endswith(".npz"):
        from catanatron.colonist.net import Net

        return Net.load(spec)
    return spec


PLAY_DEV_CARD = frozenset(
    (
        ActionType.PLAY_KNIGHT_CARD,
        ActionType.PLAY_YEAR_OF_PLENTY,
        ActionType.PLAY_MONOPOLY,
        ActionType.PLAY_ROAD_BUILDING,
    )
)


class HumanModel:
    """A seat that plays like people (E13): each move is sampled from the
    human opponent model's policy over the legal moves, with no search.
    Shared by the games of a shard; each game gets its own seat().

    A model file with `meta_dev_play_bias` adds that much to the logits of
    playing an action card (negative: the seat holds its cards longer, as
    people hold knights late; the plain model plays them like the net)."""

    def __init__(self, net):
        self.net = net
        meta = getattr(net, "meta", {})
        self.dev_play_bias = float(meta.get("meta_dev_play_bias", 0.0))

    def seat(self, seed):
        return _HumanSeat(self.net, random.Random(seed ^ 0x4A11), self.dev_play_bias)


class _HumanSeat:
    """A HumanModel's seat in one game. Has decide() like a bot, so _Game
    plays it without a search and records none of its moves."""

    def __init__(self, net, rng, dev_play_bias=0.0):
        self.net = net
        self.rng = rng
        self.dev_play_bias = dev_play_bias

    def decide(self, game, actions):
        static, dynamic = E.encode(game, game.state.current_color())
        logits, _ = self.net.forward_rows([static], np.asarray(dynamic)[None])
        x = logits[0, E.legal_indices(actions)].astype(np.float64)
        if self.dev_play_bias:
            x += self.dev_play_bias * np.fromiter(
                (a.action_type in PLAY_DEV_CARD for a in actions), float, len(actions)
            )
        p = np.exp(x - x.max())
        cdf = np.cumsum(p / p.sum())
        pick = int(np.searchsorted(cdf, self.rng.random(), side="right"))
        return actions[min(pick, len(actions) - 1)]


class _Game:
    """One game in progress and its records. `seats` maps each color to what
    plays it: the main Net, another Net, or a bot spec string."""

    def __init__(self, seed, search_args, seats, main, game=None):
        self.seed = seed
        self.game = game if game is not None else fresh_game(seed)
        self.human_start = game is not None
        self.rng = random.Random(seed)
        self.search_args = search_args
        self.main = main
        self.seats = {}
        for color, agent in seats.items():
            if isinstance(agent, HumanModel):
                agent = agent.seat(seed)
            elif isinstance(agent, str):
                import catanatron.players  # noqa: F401
                from catanatron.registry import REGISTRY

                agent = REGISTRY.build(agent, color)
            self.seats[color] = agent
        self.human_opponent = any(
            isinstance(a, _HumanSeat) for a in self.seats.values()
        )
        self.rows = []  # (dynamic, legal mask, pi, v, color, turn)
        self.search = None
        self.search_net = None
        self.steps = None
        self.full = True
        self.searches = 0

    def done(self):
        g = self.game
        return g.winning_color() is not None or g.state.num_turns >= TURNS_LIMIT

    def advance(self):
        """Plays forced moves and bot moves, then starts or resumes a search.
        Returns (net, positions to evaluate), or None when the game is over."""
        while True:
            if self.steps is None:
                while not self.done() and len(self.game.playable_actions) == 1:
                    self.game.execute(self.game.playable_actions[0])
                if self.done():
                    return None
                agent = self.seats[self.game.state.current_color()]
                if not hasattr(agent, "forward"):  # a bot
                    self.game.execute(
                        agent.decide(self.game, self.game.playable_actions)
                    )
                    continue
                random.seed(self.rng.random())  # for bots that use the global stream
                self.search_net = agent
                self.full, search_args = self.next_search()
                self.search = Search(agent, rng=self.rng, **search_args)
                self.steps = self.search.steps(self.game)
                return agent, next(self.steps)
            try:
                return self.search_net, self.steps.send(None)
            except StopIteration as finished:
                action, root = finished.value
                if self.search_net is self.main:  # train only on the main net's moves
                    self.record(root)
                key = E.action_key(action)
                move = next(
                    a for a in self.game.playable_actions if E.action_key(a) == key
                )
                self.game.execute(move)
                self.steps = None
                self.searches += 1

    def next_search(self):
        """(full?, Search arguments) for the next search: playout cap
        randomization, when the search arguments set a full_rate below 1."""
        args = dict(self.search_args)
        full_rate = args.pop("full_rate", 1.0)
        fast = args.pop("fast_simulations", None)
        setup_simulations = args.pop("setup_simulations", None)
        if self.game.state.is_initial_build_phase:
            if setup_simulations is not None:
                args["simulations"] = setup_simulations
            return True, args
        if full_rate >= 1:
            return True, args
        if self.rng.random() < full_rate:
            return True, args
        args["simulations"] = fast
        args["noise"] = False
        return False, args

    def record(self, root):
        pi, v = self.search.root_targets(root)
        legal = E.legal_indices(root.actions)
        _, dynamic = E.encode(self.game, root.color)
        mask = np.zeros(E.NUM_ACTIONS, bool)
        mask[legal] = True
        full_pi = np.zeros(E.NUM_ACTIONS, np.float32)
        full_pi[legal] = pi
        self.rows.append(
            (
                dynamic,
                mask,
                full_pi,
                v,
                root.color,
                self.game.state.num_turns,
                self.full,
            )
        )


def fresh_game(seed):
    """A new game on a random board, fixed by the seed."""
    from catanatron.colonist.rules import new_game
    from catanatron.models.player import Color, Player

    players = [Player(Color.RED), Player(Color.BLUE)]  # moves come from here
    game = new_game(players, seed=seed)
    game.random.seed(seed * 2 + 1)
    return game


def seat_plan(
    seed, net, opponents, opponent_rate, colors=None, human=None, human_rate=0.0
):
    """Who plays each color in game `seed`: the main net in both seats, or,
    at `opponent_rate`, one seat to a random opponent, or, at `human_rate`
    (drawn first), one seat to the HumanModel `human`. Fixed by the seed."""
    from catanatron.models.player import Color

    first, second = colors or (Color.RED, Color.BLUE)
    if human is not None and human_rate > 0:
        rng = random.Random(seed ^ 0x4A12)
        if rng.random() < human_rate:
            if rng.random() < 0.5:
                return {first: net, second: human}
            return {first: human, second: net}
    rng = random.Random(seed ^ 0x0FF0)
    if opponents and rng.random() < opponent_rate:
        other = opponents[rng.randrange(len(opponents))]
        if rng.random() < 0.5:
            return {first: net, second: other}
        return {first: other, second: net}
    return {first: net, second: net}


def human_start(seed, starts, rate):
    """A game from a human start for game `seed`, at `rate`, or None. Fixed
    by the seed."""
    if not starts or rate <= 0:
        return None
    rng = random.Random(seed ^ 0x57A27)
    if rng.random() >= rate:
        return None
    from catanatron.colonist.starts import start_game

    return start_game(starts[rng.randrange(len(starts))], seed)


def aux_targets(state, color):
    """graph.AUX_TARGETS for `color` at the end of a game."""
    from catanatron.state_functions import (
        get_largest_army,
        get_longest_road_color,
        player_key,
    )

    def owner(holder):
        return 0.0 if holder is None else (1.0 if holder == color else -1.0)

    other = next(c for c in state.colors if c != color)
    points = [
        state.player_state[f"{player_key(state, c)}_ACTUAL_VICTORY_POINTS"] / 15
        for c in (color, other)
    ]
    return [
        owner(get_longest_road_color(state)),
        owner(get_largest_army(state)[0]),
        *points,
    ]


def play_games(
    net,
    seeds,
    search_args,
    opponents=(),
    opponent_rate=0.0,
    starts=None,
    start_rate=0.0,
    human=None,
    human_rate=0.0,
):
    """Plays the games together, one network call per net per step. Returns
    the finished _Games and the number of network calls."""
    # The search trees hold millions of objects, and the cyclic collector's
    # full passes over them took a fifth of self-play's time. They have
    # almost no cycles (reference counting frees them), so collect only now
    # and then. The games are the same either way.
    enabled = gc.isenabled()
    gc.disable()
    try:
        return _play_games(
            net,
            seeds,
            search_args,
            opponents,
            opponent_rate,
            starts,
            start_rate,
            human,
            human_rate,
        )
    finally:
        if enabled:
            gc.enable()


#: Network calls between full garbage collections while games are played.
GC_EVERY = 1000


def _play_games(
    net,
    seeds,
    search_args,
    opponents,
    opponent_rate,
    starts,
    start_rate,
    human,
    human_rate,
):
    games = new_games(
        net,
        seeds,
        search_args,
        opponents,
        opponent_rate,
        starts,
        start_rate,
        human,
        human_rate,
    )
    runner = _Runner()
    runner.add(games)
    while runner.pending:
        runner.step()
    return games, runner.calls


def new_games(
    net,
    seeds,
    search_args,
    opponents,
    opponent_rate,
    starts,
    start_rate,
    human,
    human_rate,
):
    """A _Game for each seed, with its start and seats."""
    games = []
    for s in seeds:
        game = human_start(s, starts, start_rate)
        colors = None if game is None else game.state.colors
        seats = seat_plan(
            s, net, list(opponents), opponent_rate, colors, human, human_rate
        )
        games.append(_Game(s, search_args, seats, net, game))
    return games


class _Runner:
    """Games played together, one network call per net per step. Games can
    join while others play."""

    def __init__(self):
        self.pending = {}  # game -> (net, positions to evaluate)
        self.calls = 0

    def add(self, games):
        """Starts the games; returns those already over."""
        over = []
        for g in games:
            step = g.advance()
            if step is None:
                over.append(g)
            else:
                self.pending[g] = step
        return over

    def step(self):
        """Evaluates every pending position and advances each game; returns
        the games that ended."""
        by_net = {}
        for g, (n, nodes) in self.pending.items():
            by_net.setdefault(id(n), (n, []))[1].extend(nodes)
        for n, nodes in by_net.values():
            encoded = [E.encode(node.game, node.color) for node in nodes]
            logits, values = n.forward_rows(
                [st for st, _ in encoded], np.stack([d for _, d in encoded])
            )
            self.calls += 1
            if self.calls % GC_EVERY == 0:
                gc.collect()
            # Widened once per call (exact) rather than once per row.
            logits = logits.astype(np.float64)
            values = values.tolist()
            for row, node in enumerate(nodes):
                node.set_evaluation(
                    logits[row].take(E.legal_indices(node.actions)), values[row]
                )
        over = []
        for g in list(self.pending):
            step = g.advance()
            if step is None:
                del self.pending[g]
                over.append(g)
            else:
                self.pending[g] = step
        return over


def _load_task(task):
    """(out_dir, first, count, play): a task tuple's shard and a function
    that makes its games for a range of seeds, with the nets loaded once."""
    out_dir, net_path, first, count, search_args, *rest = task
    rest = list(rest) + [(), 0.0, None, 0.0, None, 0.0][len(rest) :]
    opponent_specs, opponent_rate, starts_path, start_rate, human_path, human_rate = (
        rest
    )
    from catanatron.colonist.net import Net
    from catanatron.colonist.starts import load as load_starts

    net = Net.load(net_path)
    opponents = [load_opponent(o) for o in opponent_specs]
    starts = load_starts(starts_path) if starts_path and start_rate > 0 else None
    human = HumanModel(Net.load(human_path)) if human_path and human_rate > 0 else None
    args = (net, search_args, opponents, opponent_rate, starts, start_rate, human)
    return out_dir, first, count, args + (human_rate,)


def run_shard(task):
    out_dir, first, count, args = _load_task(task)
    os.makedirs(out_dir, exist_ok=True)
    path = shard_path(out_dir, first, count)
    net, search_args, *rest = args
    start = time.time()
    games, calls = play_games(net, range(first, first + count), search_args, *rest)
    write_shard(path, games)
    searches = sum(g.searches for g in games)
    return (
        path,
        count,
        sum(len(g.rows) for g in games),
        searches,
        calls,
        (time.time() - start),
    )


def write_shard(path, games):
    """Writes the finished games' records to the shard file at `path`."""
    statics, seeds, cols = [], [], {k: [] for k in "dmpvztfa"}
    gidx = []
    for i, g in enumerate(games):
        statics.append(E.encode_static(g.game.state.board.map))
        seeds.append(g.seed)
        winner = g.game.winning_color()
        aux = {c: aux_targets(g.game.state, c) for c in g.game.state.colors}
        for d, m, p, v, color, turn, full in g.rows:
            for k, x in zip("dmpvtfa", (d, m, p, v, turn, full, aux[color])):
                cols[k].append(x)
            cols["z"].append(0 if winner is None else (1 if color == winner else -1))
            gidx.append(i)
    tmp = path + ".tmp.npz"
    np.savez_compressed(
        tmp,
        static=np.stack(statics).astype(np.float32),
        seed=np.asarray(seeds, np.int64),
        dynamic=np.stack(cols["d"]).astype(np.float16),
        game=np.asarray(gidx, np.int32),
        legal=np.packbits(np.stack(cols["m"]), axis=1),
        action=np.full(len(gidx), -1, np.int16),
        pi=np.stack(cols["p"]).astype(np.float16),
        v=np.asarray(cols["v"], np.float16),
        z=np.asarray(cols["z"], np.int8),
        turn=np.asarray(cols["t"], np.int16),
        full=np.asarray(cols["f"], bool),
        aux=np.asarray(cols["a"], np.float16).reshape(-1, AUX_TARGETS),
        human_start=np.asarray([g.human_start for g in games], bool),
        human_opponent=np.asarray([g.human_opponent for g in games], bool),
    )
    os.replace(tmp, path)


#: Games a --parts-stream process keeps in play: it starts its next part
#: whenever fewer are left. A shard's games end one by one, so the network
#: batches shrink to a game or two at its end. 64 seeds in parts of 8, one
#: process (Oct 18): 14.91 s a game one part at a time, 14.39 with 16 games
#: in play, 13.82 with 32, with the same moves.
STREAM_GAMES = 32


def stream_worker(task, tasks, results, width=STREAM_GAMES):
    """A --parts-stream process: plays the (first, count) parts from `tasks`
    (None ends it), several at once, keeping `width` games in play, and
    puts each part's run_shard result on `results` when its file is written."""
    out_dir, _, _, args = _load_task(task)
    os.makedirs(out_dir, exist_ok=True)
    runner = _Runner()
    parts = {}  # (first, count) -> [games, games still playing, start time]
    owner = {}  # game -> its part
    ended = False
    gc.disable()  # as in play_games

    def finish(over):
        for g in over:
            part = owner.pop(g)
            parts[part][1] -= 1
            if parts[part][1] == 0:
                games, _, start = parts.pop(part)
                path = shard_path(out_dir, *part)
                write_shard(path, games)
                searches = sum(g.searches for g in games)
                rows = sum(len(g.rows) for g in games)
                secs = time.time() - start
                results.put((path, part[1], rows, searches, runner.calls, secs))

    while True:
        while not ended and len(runner.pending) < width:
            try:
                part = tasks.get(block=not runner.pending)
            except queue.Empty:
                break
            if part is None:
                ended = True
                break
            first, count = part
            games = new_games(args[0], range(first, first + count), *args[1:])
            parts[part] = [games, len(games), time.time()]
            owner.update((g, part) for g in games)
            finish(runner.add(games))
        if not runner.pending:
            if ended:
                return
            continue
        finish(runner.step())


# Fewer shards than processes (a host's last shard, or one taken over from a
# straggler) are played in parts of at least this many games, one process
# each, and merged into the shard: round 70 waited 29 minutes on one 64-game
# shard on one of the coordinator's processes while the others sat idle.
MIN_PART = 4


def parts_of(first, count, pieces):
    """(first, count) of `pieces` near-equal consecutive parts of a shard."""
    pieces = max(1, min(pieces, count // MIN_PART))
    cuts = [first + count * i // pieces for i in range(pieces + 1)]
    return [(a, b - a) for a, b in zip(cuts, cuts[1:])]


def merge_parts(path, parts):
    """Writes the shard at `path` from its part files, in seed order, as
    run_shard would have written it, and removes the parts."""
    datas = []
    for part in parts:
        with np.load(part) as data:
            datas.append({k: data[k] for k in data.files})
    merged = {}
    for key in datas[0]:
        pieces = []
        offset = 0
        for data in datas:
            pieces.append(data[key] + offset if key == "game" else data[key])
            offset += len(data["seed"])
        merged[key] = np.concatenate(pieces)
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, **merged)
    os.replace(tmp, path)
    for part in parts:
        os.remove(part)


def run(
    net_path,
    out_dir,
    games=5000,
    seed=2_000_000,
    simulations=50,
    shard_size=64,
    workers=None,
    opponents=(),
    opponent_rate=0.0,
    log=print,
    setup_noise_scale=1.0,
    full_rate=1.0,
    fast_simulations=None,
    human_starts=None,
    human_start_rate=0.0,
    setup_simulations=None,
    human_opponent=None,
    human_opponent_rate=0.0,
    part_stream=None,
    done=None,
    stream_games=STREAM_GAMES,
):
    """Plays the shards of out_dir that aren't on disk yet. With `part_stream`, an
    iterable of (first, count), plays those parts instead as they come, into
    out_dir/parts, calling done(file name) as each is written."""
    if not 0 <= human_start_rate <= 1:
        raise ValueError("human_start_rate must be in [0, 1]")
    if not 0 <= human_opponent_rate <= 1:
        raise ValueError("human_opponent_rate must be in [0, 1]")
    if human_opponent_rate > 0 and not (
        human_opponent and os.path.isfile(human_opponent)
    ):
        raise ValueError(f"human opponent model {human_opponent!r} not found")
    if human_start_rate > 0 and not (human_starts and os.path.isfile(human_starts)):
        raise ValueError(f"human starts file {human_starts!r} not found")
    if not math.isfinite(setup_noise_scale) or setup_noise_scale <= 0:
        raise ValueError("setup_noise_scale must be positive and finite")
    if not 0 < full_rate <= 1:
        raise ValueError("full_rate must be in (0, 1]")
    if full_rate < 1 and not (fast_simulations and fast_simulations > 0):
        raise ValueError("playout cap randomization needs fast_simulations")
    if setup_simulations is not None and setup_simulations <= 0:
        raise ValueError("setup_simulations must be positive")
    os.makedirs(out_dir, exist_ok=True)
    config_path = os.path.join(out_dir, "setup-exploration.json")
    if os.path.exists(config_path):
        with open(config_path) as stream:
            recorded_scale = json.load(stream)["setup_noise_scale"]
        if recorded_scale != setup_noise_scale:
            raise ValueError(
                f"{out_dir} used setup_noise_scale={recorded_scale}; "
                f"cannot resume with {setup_noise_scale}"
            )
    else:
        existing_shards = any(
            name.startswith("shard_") and name.endswith(".npz")
            for name in os.listdir(out_dir)
        )
        if existing_shards and setup_noise_scale != 1.0:
            raise ValueError(
                f"{out_dir} has shards without setup metadata; "
                "they used the default setup_noise_scale=1"
            )
        with open(config_path, "w") as stream:
            json.dump({"setup_noise_scale": setup_noise_scale}, stream)
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(var, "1")
    search_args = {
        "simulations": simulations,
        "setup_noise_scale": setup_noise_scale,
    }
    if setup_simulations is not None:
        search_args["setup_simulations"] = setup_simulations
    if full_rate < 1:
        search_args["full_rate"] = full_rate
        search_args["fast_simulations"] = fast_simulations

    def task(directory, first, count):
        return (
            directory,
            net_path,
            first,
            count,
            search_args,
            tuple(opponents),
            opponent_rate,
            human_starts,
            human_start_rate,
            human_opponent,
            human_opponent_rate,
        )

    if part_stream is not None:
        # distributed.run's queue: (first, count) parts arrive while others
        # play, and each is reported by its file name in out_dir/parts.
        # Each process plays several parts at once (stream_worker).
        parts_dir = os.path.join(out_dir, "parts")
        start, played = time.time(), 0
        ctx = multiprocessing.get_context("spawn")
        tasks, results = ctx.Queue(), ctx.Queue()
        processes = [
            ctx.Process(
                target=stream_worker,
                args=(task(parts_dir, 0, 0), tasks, results, stream_games),
                daemon=True,
            )
            for _ in range(workers or os.cpu_count() or 1)
        ]
        for p in processes:
            p.start()

        def feed():
            for part in part_stream:
                tasks.put(part)
            for _ in processes:
                tasks.put(None)

        threading.Thread(target=feed, daemon=True).start()
        while True:
            try:
                path, count, rows, searches, calls, secs = results.get(timeout=5)
            except queue.Empty:
                if any(p.exitcode not in (None, 0) for p in processes):
                    raise RuntimeError("a self-play process failed")
                if all(p.exitcode == 0 for p in processes) and results.empty():
                    return
                continue
            played += count
            log(
                f"{os.path.basename(path)}: {count} games in {secs:.0f} s "
                f"(up to {stream_games} at once in this worker) | {played} "
                f"games, {played / (time.time() - start):.2f}/s"
            )
            done(os.path.basename(path))
    tasks = []
    for first in range(seed, seed + games, shard_size):
        count = min(shard_size, seed + games - first)
        if not os.path.exists(shard_path(out_dir, first, count)):
            tasks.append(task(out_dir, first, count))
    processes = workers or os.cpu_count() or 1
    parts = {}  # shard path -> its part paths, when played in parts
    # The shards past the last full wave are cut into parts for every
    # process: round 76's 4-process VMs each had 5 shards, and played the
    # fifth on one process for 8 of their 16 minutes, three processes idle.
    rest = len(tasks) % processes
    if rest:
        parts_dir = os.path.join(out_dir, "parts")
        pieces = -(-processes // rest)
        shards, tasks = tasks, tasks[: len(tasks) - rest]
        for task in shards[len(tasks) :]:
            paths = []
            for first, count in parts_of(task[2], task[3], pieces):
                paths.append(shard_path(parts_dir, first, count))
                if not os.path.exists(paths[-1]):
                    tasks.append((parts_dir, task[1], first, count, *task[4:]))
            parts[shard_path(out_dir, task[2], task[3])] = paths
        if rest == len(shards):
            log(f"{len(shards)} shards to play, in {len(tasks)} parts")
        else:
            whole = len(shards) - rest
            log(
                f"{len(shards)} shards to play, the last {rest} in "
                f"{len(tasks) - whole} parts"
            )
    else:
        log(f"{len(tasks)} shards to play")

    def merge_done():
        for path, paths in list(parts.items()):
            if all(os.path.exists(p) for p in paths):
                merge_parts(path, paths)
                del parts[path]
                log(f"{os.path.basename(path)}: merged from {len(paths)} parts")

    if human_opponent_rate > 0:
        log(
            f"human opponent: {human_opponent_rate:.0%} of games, one seat "
            f"sampling from {human_opponent}"
        )
    if full_rate < 1:
        log(
            f"playout cap randomization: {full_rate:.0%} of moves get {simulations} "
            f"simulations, the rest {fast_simulations}"
        )
    if human_start_rate > 0:
        log(f"human starts: {human_start_rate:.0%} of games, from {human_starts}")
    merge_done()  # parts all played before a restart
    if not tasks:
        return
    start, played = time.time(), 0
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(workers) as pool:
        for path, count, rows, searches, calls, secs in pool.imap_unordered(
            run_shard, tasks
        ):
            played += count
            rate = played / (time.time() - start)
            log(
                f"{os.path.basename(path)}: {count} games in {secs:.0f} s "
                f"({secs / count:.1f} s per game in this worker; {searches / count:.0f} "
                f"searches per game, {searches * simulations / max(calls, 1):.0f} "
                f"simulations per network call) | {played} games, {rate:.2f}/s"
            )
            merge_done()


def _end_now(*_):
    """SIGTERM for a --parts-stream worker: its finished parts are on disk, and
    the part being written is a tmp file, so nothing is lost."""
    for child in multiprocessing.active_children():
        child.kill()
    os._exit(143)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("net")
    parser.add_argument("out_dir")
    parser.add_argument("--games", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=2_000_000, help="first game seed")
    parser.add_argument("--simulations", type=int, default=50)
    parser.add_argument(
        "--setup-simulations",
        type=int,
        default=None,
        help="search simulations for initial placements (default: --simulations)",
    )
    parser.add_argument(
        "--setup-noise-scale",
        type=float,
        default=1.0,
        help="scale Gumbel exploration for initial settlements only (default 1)",
    )
    parser.add_argument(
        "--shard-size",
        type=int,
        default=64,
        help="games per shard, all played at once in one worker",
    )
    parser.add_argument(
        "--full-rate",
        type=float,
        default=1.0,
        help="share of moves with the full search (playout cap randomization)",
    )
    parser.add_argument(
        "--fast-simulations",
        type=int,
        default=None,
        help="simulations for the other moves, with --full-rate below 1",
    )
    parser.add_argument(
        "--human-starts",
        default=None,
        help="a starts.py file: start some games from real games' positions",
    )
    parser.add_argument(
        "--human-start-rate",
        type=float,
        default=0.0,
        help="share of games that start from --human-starts",
    )
    parser.add_argument(
        "--human-opponent",
        default=None,
        help="a human opponent model (opponent_model.py): one seat of some "
        "games samples its moves from it, without search",
    )
    parser.add_argument(
        "--human-opponent-rate",
        type=float,
        default=0.0,
        help="share of games with a --human-opponent seat (default 0: off)",
    )
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument(
        "--opponents",
        default="",
        help="comma-separated nets (.npz) or bot specs (e.g. AB:2) for some games",
    )
    parser.add_argument(
        "--opponent-rate",
        type=float,
        default=0.2,
        help="share of games where one seat is a random opponent",
    )
    parser.add_argument(
        "--parts-stream",
        action="store_true",
        help="play 'first count' lines from stdin as they arrive, into "
        "out_dir/parts, printing each part's file name when written (how "
        "distributed.run uses a worker); progress goes to stderr",
    )
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    opponents = [o for o in args.opponents.split(",") if o]
    log, stream, done = (lambda m: print(m, flush=True)), None, None
    if args.parts_stream:
        # A stopped queue worker hung in its pool's terminate, on a lock a
        # killed worker held (round 85): kill the workers and leave instead.
        signal.signal(signal.SIGTERM, _end_now)
        log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
        stream = (tuple(map(int, line.split())) for line in sys.stdin if line.strip())
        done = lambda name: print(name, flush=True)  # noqa: E731
    run(
        args.net,
        args.out_dir,
        args.games,
        args.seed,
        args.simulations,
        args.shard_size,
        args.workers,
        opponents,
        args.opponent_rate,
        log=log,
        setup_noise_scale=args.setup_noise_scale,
        full_rate=args.full_rate,
        fast_simulations=args.fast_simulations,
        human_starts=args.human_starts,
        human_start_rate=args.human_start_rate,
        setup_simulations=args.setup_simulations,
        human_opponent=args.human_opponent,
        human_opponent_rate=args.human_opponent_rate,
        part_stream=stream,
        done=done,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
