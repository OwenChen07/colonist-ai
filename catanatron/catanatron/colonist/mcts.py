"""Search guided by the network (PLAN.md, Phase 2 and Design choices).

A Monte Carlo tree search in the style of Gumbel MuZero (Danihelka et al.,
2022), with chance nodes for Catan's dice:

  - Root: Gumbel top-k sampling picks up to `considered` candidate moves from
    the network's policy, then sequential halving spends the simulations on
    them, halving the field each round, and plays the best survivor. This
    improves on the network's own choice even with a few dozen simulations.
  - Below the root: each node visits the move that most raises the visit
    distribution toward the improved policy (softmax of policy logits plus a
    scaled Q), deterministically, as the paper does.
  - Chance: a roll, a development card draw and a robber's steal lead to a
    chance node that branches on every outcome with its true odds (dice odds
    from the balanced-dice state). Outcomes are visited in proportion to their
    odds, each one built only when first visited.
  - Forced moves (one legal move) are played through without asking the net.

Hidden cards: the searching player can't see the opponent's development cards
or the order of the deck. Before each search, the opponent's hidden cards are
re-dealt at random from the cards the searcher hasn't seen (the deck plus the
opponent's hand), so the search can't use them. Within a search the deal is
fixed (see PLAN.md: the fallback is to re-deal at every chance node).

Opponent model: the search can give the opponent's nodes a prior that mixes
in a second net trained on people's moves (opponent_model.py), so it expects
human-like replies instead of the bot's own; the searcher's nodes and every
value still come from the main net. See mix_logits.

Values are stored from the root player's point of view and flipped for the
other player's nodes; a node's player is looked up, never assumed to
alternate, since a Catan turn is many moves by one player.
"""

import math
import random
import time
from dataclasses import dataclass

import numpy as np

from catanatron.colonist import encode as E
from catanatron.models.dice import full_deck
from catanatron.models.enums import (
    DEVELOPMENT_CARDS,
    RESOURCES,
    VICTORY_POINT,
    ActionPrompt,
    ActionRecord,
    ActionType,
)
from catanatron.models.player import Player
from catanatron.state_functions import player_key

TURNS_LIMIT = 1000


# ===== hidden information
def redeal_hidden_cards(game, viewer, rng, tries=100):
    """In place: deal the opponent's development cards again from everything
    `viewer` can't see (their hand plus the deck), and shuffle the deck.

    A deal must fit what the viewer knows: the game isn't over, so the
    opponent's visible points plus victory point cards are short of winning.
    Deals that break this are drawn again (the real hand always fits)."""
    state = game.state
    ps = state.player_state
    for color in state.colors:
        if color == viewer:
            continue
        key = player_key(state, color) + "_"
        hand = [c for c in DEVELOPMENT_CARDS for _ in range(ps[key + c + "_IN_HAND"])]
        visible = ps[key + "ACTUAL_VICTORY_POINTS"] - hand.count(VICTORY_POINT)
        pool = state.development_listdeck + hand
        new_hand, deck = hand, state.development_listdeck
        for _ in range(tries):
            rng.shuffle(pool)
            if visible + pool[: len(hand)].count(VICTORY_POINT) < game.vps_to_win:
                new_hand, deck = pool[: len(hand)], pool[len(hand) :]
                break
        state.development_listdeck = list(deck)
        for c in DEVELOPMENT_CARDS:
            n = new_hand.count(c)
            ps[key + c + "_IN_HAND"] = n
            if c != VICTORY_POINT:
                # Cards held since the start of the turn are playable; a new
                # deal can't know which were bought this turn, so it assumes
                # none were (exact at the start of their turn).
                ps[key + c + "_OWNED_AT_START"] = n > 0
        ps[key + "ACTUAL_VICTORY_POINTS"] = visible + new_hand.count(VICTORY_POINT)
    # Refresh legal moves: they can depend on the cards in hand.
    from catanatron.models.actions import generate_playable_actions

    game.playable_actions = generate_playable_actions(state)


# ===== chance
def outcomes(game, action):
    """The chance outcomes of `action`, as [(probability, ActionRecord)], or
    None if it is deterministic."""
    t = action.action_type
    state = game.state
    if t == ActionType.ROLL:
        dice = state.dice
        if dice is None:
            odds = [min(s - 1, 13 - s) / 36 for s in range(2, 13)]
            pairs = [p[0] for p in full_deck()]
        else:
            odds = dice.odds(action.color)
            pairs = [dice.example_pair(s) for s in range(2, 13)]
        return [
            (p, ActionRecord(action.__class__(action.color, t, pair), pair))
            for p, pair in zip(odds, pairs)
            if p > 0
        ]
    if t == ActionType.BUY_DEVELOPMENT_CARD:
        deck = state.development_listdeck
        return [
            (deck.count(card) / len(deck), ActionRecord(action, card))
            for card in DEVELOPMENT_CARDS
            if card in deck
        ]
    if t == ActionType.MOVE_ROBBER and action.value[1] is not None:
        key = player_key(state, action.value[1]) + "_"
        hand = [state.player_state[key + r + "_IN_HAND"] for r in RESOURCES]
        total = sum(hand)
        if total == 0:
            return None
        return [
            (n / total, ActionRecord(action, r)) for r, n in zip(RESOURCES, hand) if n
        ]
    return None


# ===== opponent model
def mix_logits(main, human, mix):
    """log((1 - mix) softmax(main) + mix softmax(human)), over the same legal
    moves: the prior for an opponent's node. A mixture of probabilities (not
    of logits) keeps every move the main net likes possible, however sure the
    human model is, and the reverse."""
    log_p = main - np.logaddexp.reduce(main)
    log_q = human - np.logaddexp.reduce(human)
    if mix >= 1:
        return log_q
    return np.logaddexp(math.log1p(-mix) + log_p, math.log(mix) + log_q)


# ===== immediate wins
def immediate_win(root):
    """Index of a root move that wins the game at once, or None. Only moves
    without a chance outcome count (a development card bought could be a
    point, but isn't known to be), and only when the player is within 2
    points of winning (no single move gains more: a city or settlement 1,
    longest road or largest army 2)."""
    game, color = root.game, root.color
    state = game.state
    points = state.player_state[player_key(state, color) + "_ACTUAL_VICTORY_POINTS"]
    if points < game.vps_to_win - 2:
        return None
    for i, action in enumerate(root.actions):
        if outcomes(game, action) is not None:
            continue
        after = game.copy()
        after.execute(action, validate_action=False)
        if after.winning_color() == color:
            return i
    return None


# ===== the tree
class Decision:
    __slots__ = (
        "game",
        "color",
        "actions",
        "legal",
        "logits",
        "prior",
        "value",
        "N",
        "W",
        "children",
        "immediate_win",
    )

    def __init__(self, game, color, actions):
        self.game = game
        self.color = color
        self.actions = actions
        self.logits = None  # over `actions`; None until the net has seen it
        n = len(actions)
        self.N = np.zeros(n)
        self.W = np.zeros(n)  # summed values for `color`
        self.children = [None] * n
        self.immediate_win = None  # index of a move that wins at once (root)

    def set_evaluation(self, logits, value):
        self.logits = logits
        p = np.exp(logits - logits.max())
        self.prior = p / p.sum()
        self.value = value  # net's value, for `color`


class Chance:
    __slots__ = ("game", "action", "probs", "records", "N", "children")

    def __init__(self, game, action, outs):
        self.game = game  # before the action; children copy it
        self.action = action
        self.probs = np.asarray([p for p, _ in outs])
        self.records = [r for _, r in outs]
        self.N = np.zeros(len(outs))
        self.children = [None] * len(outs)


class Terminal:
    __slots__ = ("winner",)

    def __init__(self, winner):
        self.winner = winner


class Search:
    """One search from a position, for the player to move there."""

    def __init__(
        self,
        net,
        simulations=50,
        considered=16,
        c_visit=50.0,
        c_scale=1.0,
        rng=None,
        noise=True,
        setup_noise_scale=1.0,
        opponent_model=None,
        opponent_mix=0.0,
        root_action_filter=None,
        take_immediate_wins=True,
    ):
        self.net = net
        self.simulations = simulations
        self.considered = considered
        self.c_visit = c_visit
        self.c_scale = c_scale
        self.rng = rng or random.Random()
        # Gumbel noise makes the root's choice a sample: right for self-play,
        # which should explore. Without it the search plays its best move.
        self.noise = noise
        if not math.isfinite(setup_noise_scale) or setup_noise_scale <= 0:
            raise ValueError("setup_noise_scale must be positive and finite")
        self.setup_noise_scale = setup_noise_scale
        if not 0.0 <= opponent_mix <= 1.0:
            raise ValueError("opponent_mix must be between 0 and 1")
        if opponent_mix > 0 and opponent_model is None:
            raise ValueError("opponent_mix needs an opponent_model")
        # With mix 0 the model is never run: the search is exactly as without.
        self.opponent_model = opponent_model if opponent_mix > 0 else None
        self.opponent_mix = opponent_mix
        # Experimental decision rules may narrow only the root's legal moves.
        # Descendants always use the simulator's complete legal action set.
        self.root_action_filter = root_action_filter
        self.take_immediate_wins = take_immediate_wins
        self.root_color = None  # the searcher, set when a search starts
        self.evaluations = 0
        self.opponent_evaluations = 0

    # ----- building nodes
    def node_after(self, game):
        """The node for `game` (which this search now owns), playing through
        forced deterministic moves. A Decision comes back unevaluated."""
        while True:
            winner = game.winning_color()
            if winner is not None or game.state.num_turns >= TURNS_LIMIT:
                return Terminal(winner)
            actions = game.playable_actions
            if len(actions) > 1:
                break
            outs = outcomes(game, actions[0])
            if outs is not None:
                return Chance(game, actions[0], outs)
            game.execute(actions[0], validate_action=False)
        return Decision(game, game.state.current_color(), list(actions))

    def evaluate(self, nodes):
        """Evaluates new Decisions with one network call. They are all
        positions of the same game, so they share the board inputs."""
        self.evaluations += len(nodes)
        encoded = [E.encode(n.game, n.color) for n in nodes]
        dynamic = np.stack([d for _, d in encoded])
        logits, values = self.net.forward(encoded[0][0], dynamic)
        human = {}
        if self.opponent_model is not None:
            # The opponent's nodes only, in one more call: the root and the
            # searcher's own nodes keep the main net's prior.
            rows = [r for r, n in enumerate(nodes) if n.color != self.root_color]
            if rows:
                self.opponent_evaluations += len(rows)
                h, _ = self.opponent_model.forward(encoded[0][0], dynamic[rows])
                human = dict(zip(rows, h))
        for row, node in enumerate(nodes):
            legal = E.legal_indices(node.actions)
            prior = logits[row, legal].astype(np.float64)
            if row in human:
                prior = mix_logits(
                    prior, human[row][legal].astype(np.float64), self.opponent_mix
                )
            node.set_evaluation(prior, float(values[row]))

    def child_of(self, node, i):
        """Builds the node reached by move i of a Decision."""
        action = node.actions[i]
        outs = outcomes(node.game, action)
        if outs is not None:
            if len(outs) == 1:
                g = node.game.copy()
                g.execute(action, validate_action=False, action_record=outs[0][1])
                return self.node_after(g)
            return Chance(node.game, action, outs)
        g = node.game.copy()
        g.execute(action, validate_action=False)
        return self.node_after(g)

    def outcome_child(self, node, j):
        g = node.game.copy()
        record = node.records[j]
        g.execute(record.action, validate_action=False, action_record=record)
        return self.node_after(g)

    # ----- values
    def value_for(self, leaf, color):
        """A new leaf's value for `color`."""
        if isinstance(leaf, Terminal):
            if leaf.winner is None:
                return 0.0
            return 1.0 if leaf.winner == color else -1.0
        if isinstance(leaf, Decision):
            return leaf.value if leaf.color == color else -leaf.value
        raise TypeError(leaf)

    def sigma(self, node, q):
        """Scale Q (for node.color, in [-1, 1]) as the paper's sigma, on [0, 1]."""
        return (self.c_visit + node.N.max()) * self.c_scale * (q + 1) / 2

    def completed_q(self, node):
        visited = node.N > 0
        q = np.zeros(len(node.N))
        q[visited] = node.W[visited] / node.N[visited]
        total = node.N.sum()
        if total == 0:
            v_mix = node.value
        else:
            pv = node.prior[visited]
            v_mix = (node.value + total / pv.sum() * (pv * q[visited]).sum()) / (
                1 + total
            )
        q[~visited] = v_mix
        return q

    def improved_policy(self, node):
        x = node.logits + self.sigma(node, self.completed_q(node))
        p = np.exp(x - x.max())
        return p / p.sum()

    # ----- simulation
    def descend(self, root, first):
        """Walks from the root through root move `first` to a new leaf.
        Returns (path, leaf); a Decision leaf still needs evaluating."""
        path = [(root, first)]
        node = root
        i = first
        while True:
            child = node.children[i]
            if child is None:
                if isinstance(node, Decision):
                    child = node.children[i] = self.child_of(node, i)
                else:
                    child = node.children[i] = self.outcome_child(node, i)
                leaf = child
                break
            node = child
            if isinstance(node, Terminal):
                leaf = node
                break
            if isinstance(node, Decision):
                pi = self.improved_policy(node)
                i = int(np.argmax(pi - node.N / (1 + node.N.sum())))
            else:
                i = int(np.argmax(node.probs - node.N / (1 + node.N.sum())))
            path.append((node, i))
        while isinstance(leaf, Chance):
            # A chance node as a leaf: value it through its likeliest outcome.
            j = int(np.argmax(leaf.probs))
            child = leaf.children[j] = self.outcome_child(leaf, j)
            path.append((leaf, j))
            leaf = child
        return path, leaf

    def backup(self, path, leaf, root_color):
        value = self.value_for(leaf, root_color)
        for n, k in path:
            n.N[k] += 1
            if isinstance(n, Decision):
                n.W[k] += value if n.color == root_color else -value

    def run(self, game, time_limit=None):
        """Search `game` (not modified) for its player to move. Returns
        (chosen action, root node). With `time_limit` (seconds), the search
        stops early when the time is up and picks the best candidate so far
        (at least one round of visits always runs); `simulations` stays the
        cap. self.used is the number of simulations it ran."""
        self.stop_at = None if time_limit is None else time.monotonic() + time_limit
        steps = self.steps(game)
        try:
            pending = next(steps)
            while True:
                self.evaluate(pending)
                pending = steps.send(None)
        except StopIteration as done:
            return done.value

    def root_gumbel(self, n, prompt):
        """Scale setup settlement sampling without changing later moves."""
        if not self.noise:
            return np.zeros(n)
        values = np.array(
            [-math.log(-math.log(self.rng.random() or 1e-300)) for _ in range(n)]
        )
        if prompt == ActionPrompt.BUILD_INITIAL_SETTLEMENT:
            values *= self.setup_noise_scale
        return values

    def steps(self, game):
        """The search as a generator, so many searches can share network
        calls: it yields lists of new Decisions to evaluate (with
        set_evaluation) before it is resumed, and returns (action, root)."""
        g = game.copy()
        viewer = self.root_color = g.state.current_color()
        redeal_hidden_cards(g, viewer, self.rng)
        filtered = None
        if self.root_action_filter is not None:
            filtered = list(self.root_action_filter(g, list(g.playable_actions)))
            if not filtered or any(a not in g.playable_actions for a in filtered):
                raise ValueError("root action filter must retain legal moves")
            g.playable_actions = filtered
        # A filter may leave one move. Keep it as a root decision rather than
        # playing through to an unrelated decision later in the game.
        root = (
            Decision(g, viewer, filtered)
            if filtered is not None and len(filtered) == 1
            else self.node_after(g)
        )
        if not isinstance(root, Decision):
            raise ValueError("search needs a position with a choice to make")
        yield [root]
        if self.take_immediate_wins:
            win = immediate_win(root)
            if win is not None:
                # The root samples at most `considered` moves by the net's
                # prior: a win the net rates low was never looked at (seen
                # in 5-6 of 35 flagged human-game positions). A win is a win.
                root.immediate_win = win
                self.used = 0
                return root.actions[win], root
        n = len(root.actions)
        gumbel = self.root_gumbel(n, root.game.state.current_prompt)
        m = min(self.considered, n)
        candidates = list(np.argsort(-(gumbel + root.logits))[:m])
        # Sequential halving: equal visits per round to each candidate, then
        # keep the better half (at least 2 until the last round, which spends
        # whatever budget is left). Within a round the candidates' paths
        # share nothing below the root, and root statistics are only read
        # between rounds, so a round's new leaves are evaluated together.
        sims = self.simulations
        phases = max(1, math.ceil(math.log2(m))) if m > 1 else 0
        used = 0
        stop_at = getattr(self, "stop_at", None)
        out_of_time = False
        for phase in range(phases):
            per = max(1, (sims - used) // ((phases - phase) * len(candidates)))
            for _ in range(per):
                walks = [self.descend(root, a) for a in candidates]
                pending = [leaf for _, leaf in walks if isinstance(leaf, Decision)]
                if pending:
                    yield pending
                for path, leaf in walks:
                    self.backup(path, leaf, root.color)
                used += len(candidates)
                if stop_at is not None and time.monotonic() >= stop_at:
                    out_of_time = True
                    break
            if out_of_time:
                break
            if phase < phases - 1:
                q = self.completed_q(root)
                score = gumbel + root.logits + self.sigma(root, q)
                candidates.sort(key=lambda a: -score[a])
                candidates = candidates[: max(2, len(candidates) // 2)]
        self.used = used
        q = self.completed_q(root)
        score = gumbel + root.logits + self.sigma(root, q)
        best = max(candidates, key=lambda a: score[a])
        return root.actions[best], root

    def root_targets(self, root):
        """Training targets from a finished search: the improved policy over
        the root's moves, and the search's value for the root player."""
        if root.immediate_win is not None:  # the move that wins, for sure
            pi = np.zeros(len(root.actions))
            pi[root.immediate_win] = 1.0
            return pi, 1.0
        q = self.completed_q(root)
        pi = self.improved_policy(root)
        return pi, float((pi * q).sum())


class NetPlayer(Player):
    """The network with search: Z:<net.npz>:<simulations>. With 0 simulations
    it plays the policy's top move."""

    LABEL = "Net+MCTS"

    @dataclass(frozen=True)
    class Params:
        path: str = "models/net.npz"
        simulations: int = 50
        seed: int = 0

    _nets = {}  # path -> Net, shared by the players of a process

    def __init__(self, color, params=None, net=None):
        super().__init__(color, params)
        from catanatron.colonist.net import Net

        if net is None:
            net = self._nets.get(self.params.path)
            if net is None:
                net = self._nets[self.params.path] = Net.load(self.params.path)
        self.net = net
        self.simulations = self.params.simulations
        self.rng = random.Random(self.params.seed)

    def decide(self, game, playable_actions):
        if len(playable_actions) == 1:
            return playable_actions[0]
        if self.simulations == 0:
            color = game.state.current_color()
            legal = E.legal_indices(playable_actions)
            static, dynamic = E.encode(game, color)
            logits, _ = self.net.forward(static, dynamic[None, :])
            return playable_actions[int(np.argmax(logits[0, legal]))]
        search = Search(self.net, self.simulations, rng=self.rng)
        action, _ = search.run(game)
        # The search ran on a copy; hand back the move as the game listed it.
        key = E.action_key(action)
        return next(a for a in playable_actions if E.action_key(a) == key)
