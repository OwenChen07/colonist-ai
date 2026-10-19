"""Games without search, for policy-gradient training (E54, offline).

    python -m catanatron.colonist.pg LEARNER.npz OUT_DIR --games 2000 \
        --pool models/r73.npz models/r66.npz --human models/human/net.npz

Every move of a net seat is sampled from its policy over the legal moves (at
--temperature), with no search, so a game costs about a millisecond a move
instead of a search's half second. Games are played together, one network
call per net per step, as in selfplay.py.

Seats (fixed by the seed): a --self-rate share of games put the learner in
both seats and record both; the rest put one seat to a random member of the
pool (older nets, and the human-style seat of E13 when --human is given) and
record only the learner's moves.

Each recorded row has, besides selfplay.py's columns (`action` is the move
played; there is no `pi`): the move's log-probability under the policy that
played it (`logp`), the value head's output (`vpred`), and the advantage
(`adv`) and lambda-return (`ret`) from generalised advantage estimation along
the seat's own moves: reward z at the end of the game, no discount, the
value head as the baseline (Schulman et al. 2016). train.py's --pg reads them.
"""

import argparse
import os
import random
import time

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.datagen import shard_path
from catanatron.colonist.graph import AUX_TARGETS
from catanatron.colonist.mcts import TURNS_LIMIT
from catanatron.colonist.selfplay import HumanModel, aux_targets, fresh_game


def gae(values, z, lam):
    """(advantages, lambda-returns) for one seat's moves in order: values are
    the value head's outputs at its moves, z the final result (+1, -1, 0).
    The reward comes only at the end and nothing is discounted."""
    values = np.asarray(values, np.float64)
    nxt = np.append(values[1:], z)
    delta = nxt - values
    adv = np.empty_like(values)
    run = 0.0
    for t in range(len(values) - 1, -1, -1):
        run = delta[t] + lam * run
        adv[t] = run
    return adv, adv + values


class _PGGame:
    """One game in progress. `seats` maps each color to a Net or a
    HumanModel; moves of the colors in `record` are recorded."""

    def __init__(self, seed, seats, record, temperature):
        self.seed = seed
        self.game = fresh_game(seed)
        self.rng = random.Random(seed ^ 0x9E54)
        self.temperature = temperature
        self.seats = {
            c: (a.seat(seed) if isinstance(a, HumanModel) else a)
            for c, a in seats.items()
        }
        self.record = set(record)
        self.human_opponent = any(isinstance(a, HumanModel) for a in seats.values())
        self.rows = []  # (dynamic, legal mask, action, logp, vpred, color, turn)
        self.moves = 0

    def done(self):
        g = self.game
        return g.winning_color() is not None or g.state.num_turns >= TURNS_LIMIT

    def advance(self):
        """Plays forced moves and the human seat's moves. Returns the net to
        move next, or None when the game is over."""
        game = self.game
        while True:
            while not self.done() and len(game.playable_actions) == 1:
                game.execute(game.playable_actions[0])
            if self.done():
                return None
            agent = self.seats[game.state.current_color()]
            if hasattr(agent, "forward_rows"):
                return agent
            game.execute(agent.decide(game, game.playable_actions))

    def play(self, logits, value, dynamic):
        """Samples and plays the move to play from the net's output."""
        game = self.game
        actions = game.playable_actions
        legal = E.legal_indices(actions)
        x = logits.take(legal).astype(np.float64) / self.temperature
        x -= x.max()
        logp = x - np.log(np.exp(x).sum())
        cdf = np.cumsum(np.exp(logp))
        pick = min(
            int(np.searchsorted(cdf, self.rng.random() * cdf[-1], side="right")),
            len(actions) - 1,
        )
        color = game.state.current_color()
        if color in self.record:
            mask = np.zeros(E.NUM_ACTIONS, bool)
            mask[legal] = True
            self.rows.append(
                (
                    dynamic,
                    mask,
                    legal[pick],
                    float(logp[pick]),
                    float(value),
                    color,
                    game.state.num_turns,
                )
            )
        game.execute(actions[pick])
        self.moves += 1


def seat_plan(seed, learner, pool, self_rate):
    """(seats, recorded colors) for game `seed`: the learner in both seats at
    `self_rate`, else against a random pool member in a random seat."""
    from catanatron.models.player import Color

    first, second = Color.RED, Color.BLUE
    rng = random.Random(seed ^ 0x0E54)
    if not pool or rng.random() < self_rate:
        return {first: learner, second: learner}, (first, second)
    other = pool[rng.randrange(len(pool))]
    if rng.random() < 0.5:
        return {first: learner, second: other}, (first,)
    return {first: other, second: learner}, (second,)


def play_games(learner, seeds, pool=(), self_rate=1.0, temperature=1.0):
    """Plays the games together, one network call per net per step. Returns
    the finished _PGGames and the number of network calls."""
    games = []
    for s in seeds:
        seats, record = seat_plan(s, learner, list(pool), self_rate)
        games.append(_PGGame(s, seats, record, temperature))
    pending = {}
    for g in games:
        net = g.advance()
        if net is not None:
            pending[g] = net
    calls = 0
    while pending:
        by_net = {}
        for g, net in pending.items():
            by_net.setdefault(id(net), (net, []))[1].append(g)
        for net, group in by_net.values():
            encoded = [E.encode(g.game, g.game.state.current_color()) for g in group]
            dynamic = np.stack([d for _, d in encoded])
            logits, values = net.forward_rows([st for st, _ in encoded], dynamic)
            calls += 1
            for row, g in enumerate(group):
                g.play(logits[row], values[row], dynamic[row])
        for g in list(pending):
            net = g.advance()
            if net is None:
                del pending[g]
            else:
                pending[g] = net
    return games, calls


def write_shard(path, games, lam=0.95):
    """Writes the games' records to `path`, in selfplay.py's layout (no `pi`)
    plus logp, vpred, adv and ret."""
    statics, seeds, gidx = [], [], []
    cols = {k: [] for k in ("d", "m", "a", "lp", "vp", "z", "t", "aux", "adv", "ret")}
    for i, g in enumerate(games):
        statics.append(E.encode_static(g.game.state.board.map))
        seeds.append(g.seed)
        winner = g.game.winning_color()
        for color in g.game.state.colors:
            rows = [r for r in g.rows if r[5] == color]
            if not rows:
                continue
            z = 0 if winner is None else (1 if color == winner else -1)
            adv, ret = gae([r[4] for r in rows], z, lam)
            aux = aux_targets(g.game.state, color)
            for r, a, rt in zip(rows, adv, ret):
                d, m, action, logp, vpred, _, turn = r
                for k, x in zip(
                    ("d", "m", "a", "lp", "vp", "z", "t", "aux", "adv", "ret"),
                    (d, m, action, logp, vpred, z, turn, aux, a, rt),
                ):
                    cols[k].append(x)
                gidx.append(i)
    tmp = path + ".tmp.npz"
    np.savez_compressed(
        tmp,
        static=np.stack(statics).astype(np.float32),
        seed=np.asarray(seeds, np.int64),
        dynamic=np.stack(cols["d"]).astype(np.float16),
        game=np.asarray(gidx, np.int32),
        legal=np.packbits(np.stack(cols["m"]), axis=1),
        action=np.asarray(cols["a"], np.int16),
        z=np.asarray(cols["z"], np.int8),
        turn=np.asarray(cols["t"], np.int16),
        aux=np.asarray(cols["aux"], np.float16).reshape(-1, AUX_TARGETS),
        logp=np.asarray(cols["lp"], np.float32),
        vpred=np.asarray(cols["vp"], np.float32),
        adv=np.asarray(cols["adv"], np.float32),
        ret=np.asarray(cols["ret"], np.float32),
        human_opponent=np.asarray([g.human_opponent for g in games], bool),
    )
    os.replace(tmp, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("learner", help="the learner's net.npz")
    parser.add_argument("out", help="directory for the shard")
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--first", type=int, default=0, help="first game seed")
    parser.add_argument("--pool", nargs="*", default=[], help="opponent net.npz files")
    parser.add_argument("--human", default=None, help="a human-style seat's model")
    parser.add_argument("--self-rate", type=float, default=0.5)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--lam", type=float, default=0.95, help="GAE lambda")
    args = parser.parse_args(argv)

    from catanatron.colonist.net import Net

    learner = Net.load(args.learner)
    pool = [Net.load(p) for p in args.pool]
    if args.human:
        pool.append(HumanModel(Net.load(args.human)))
    start = time.time()
    seeds = range(args.first, args.first + args.games)
    games, calls = play_games(learner, seeds, pool, args.self_rate, args.temperature)
    os.makedirs(args.out, exist_ok=True)
    path = shard_path(args.out, args.first, args.games)
    write_shard(path, games, args.lam)
    rows = sum(len(g.rows) for g in games)
    took = time.time() - start
    print(
        f"{args.games} games, {rows} rows, {calls} net calls, "
        f"{took:.1f} s ({took / args.games:.3f} s a game) -> {path}"
    )


if __name__ == "__main__":
    main()
