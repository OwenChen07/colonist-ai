"""Export verified, player-perspective Colonist captures as training shards.

Only use captures recorded by the human whose decisions are to be learned.
An opponent's unplayed development cards are hidden in a one-sided capture,
so replaying that opponent's moves does not recreate their information state.
For a bot-side capture against a human, use --value-only for outcome labels
and --opponent-openings for the human's setup moves, when no development cards
exist yet. The opponent_known and opponent_exact modes take the human
opponent's later moves too: while they hold no card, and (opponent_exact)
while their hand is the only one that fits the log (DevHands). The output
contains numeric inputs and labels, not names or chat.

    python -m catanatron.colonist.human_data 'captures/human-*.json' \
        --out data/human --human-perspective
"""

import argparse
import glob
import hashlib
import itertools
import json
import os
import random
import sys
from collections import Counter

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.record import BUY, END_TURN, PLAYED_CARD
from catanatron.colonist.replay import replay
from catanatron.models.enums import DEVELOPMENT_CARDS, VICTORY_POINT
from catanatron.state_functions import get_dev_cards_in_hand, player_num_dev_cards

UNKNOWN = "?"  # a card never identified: never played, and not a revealed VP


def _fits(plays, buys):
    """Whether each play (its turn) can have its own purchase on an earlier
    turn. "Earlier than" nests, so checking sorted prefixes is enough."""
    buys = sorted(buys)
    for k, play in enumerate(sorted(plays)):
        if sum(1 for b in buys if b < play) < k + 1:
            return False
    return True


class DevHands:
    """A seat's hidden development cards before each log entry, worked out
    from its purchases (cards unseen), its plays and the end-of-game reveal of
    victory point cards (`hidden_vp_cards`).

    at(i) gives the hand as a Counter of cards and whether it is the only one
    that fits: ("none", empty) when no card is held; ("exact", hand) when every
    way of matching plays to earlier-turn purchases gives the same hand and it
    has no never-identified card; ("ambiguous", None) or ("unknown", None)
    otherwise. Same-type cards need no matching, and a card bought this turn
    can't be played, which is what narrows the matchings down.
    """

    def __init__(self, record, color):
        self.buys, self.plays = [], []  # (log index, turn[, card])
        known_buy = False
        turn = 0
        for i, (action, result) in enumerate(record["log"]):
            if action[1] == END_TURN:
                turn += 1
            if action[0] != color:
                continue
            if action[1] == BUY:
                self.buys.append((i, turn))
                known_buy |= result is not None
            elif action[1] in PLAYED_CARD:
                self.plays.append((i, turn, PLAYED_CARD[action[1]]))
        revealed = record.get("hidden_vp_cards") or {}
        self.vp = revealed.get(color, 0)
        self.unknown = len(self.buys) - len(self.plays) - self.vp
        if color not in revealed:  # no reveal: any unplayed card is unknown
            self.unknown, self.vp = self.unknown + self.vp, 0
        # The person's own purchases are hidden; a seat whose cards were seen
        # is not what this is for.
        self.ok = (
            not known_buy
            and self.unknown >= 0
            and bool(self.hands(len(record["log"]) + 1) or not self.buys)
        )

    def hands(self, i):
        """Every hand that fits before log entry i."""
        before = [b for b in self.buys if b[0] < i]
        after = [b for b in self.buys if b[0] >= i]
        played = [p for p in self.plays if p[0] < i]
        later = [p for p in self.plays if p[0] >= i]
        held = len(before) - len(played)
        kinds = sorted({p[2] for p in later})
        by_kind = {k: [p for p in later if p[2] == k] for k in kinds}
        out = set()
        for counts in itertools.product(*(range(len(by_kind[k]) + 1) for k in kinds)):
            rest = held - sum(counts)
            for vp in range(min(self.vp, max(rest, -1)) + 1):
                unknown = rest - vp
                if unknown < 0 or unknown > self.unknown:
                    continue
                # A held card of a kind is the one played first, later on.
                now = [p for k, c in zip(kinds, counts) for p in by_kind[k][:c]]
                then = [p for k, c in zip(kinds, counts) for p in by_kind[k][c:]]
                if len(then) + self.vp - vp + self.unknown - unknown != len(after):
                    continue
                if not _fits([p[1] for p in played + now], [b[1] for b in before]):
                    continue
                if not _fits([p[1] for p in then], [b[1] for b in after]):
                    continue
                hand = Counter(dict(zip(kinds, counts)))
                hand[VICTORY_POINT] += vp
                hand[UNKNOWN] += unknown
                out.add(tuple(sorted((k, n) for k, n in hand.items() if n)))
        return out

    def at(self, i):
        held = sum(1 for b in self.buys if b[0] < i) - sum(
            1 for p in self.plays if p[0] < i
        )
        if held == 0:
            return "none", Counter()
        hands = self.hands(i) if self.ok else set()
        if len(hands) != 1:
            unknown = not hands or any(dict(h).get(UNKNOWN) for h in hands)
            return ("unknown" if unknown else "ambiguous"), None
        hand = Counter(dict(next(iter(hands))))
        return ("unknown", None) if hand[UNKNOWN] else ("exact", hand)


def held_cards(state, color):
    """The development cards the engine has in color's hand."""
    return Counter(
        {
            card: n
            for card in DEVELOPMENT_CARDS
            if (n := get_dev_cards_in_hand(state, color, card))
        }
    )


class _Positions:
    def __init__(self, color, setup_only=False, known_only=False, hands=None):
        self.color = color
        self.setup_only = setup_only
        # Only decisions where the player holds no unplayed development card:
        # then everything they knew is in the capture (their card count is
        # public, and in 1v1 every resource that moves is seen by both).
        self.known_only = known_only
        # Or those plus the ones where their hand is known exactly (DevHands)
        # and the replay gave them those very cards.
        self.hands = hands
        self.counts = Counter()
        self.index = 0
        self.rows = []
        self.static = None
        self.winner = None

    def before(self, game):
        self.static = E.encode_static(game.state.board.map)

    def step(self, game, decision):
        i, self.index = self.index, self.index + 1
        if game.state.current_color().value != self.color:
            return
        if self.setup_only and not game.state.is_initial_build_phase:
            return
        if self.known_only and player_num_dev_cards(
            game.state, game.state.current_color()
        ):
            return
        actions = game.playable_actions
        if len(actions) <= 1:
            return
        if self.hands is not None:
            kind, hand = self.hands.at(i)
            if (
                kind == "exact"
                and held_cards(game.state, game.state.current_color()) != hand
            ):
                kind = "mismatch"
            elif kind == "none" and player_num_dev_cards(
                game.state, game.state.current_color()
            ):
                kind = "mismatch"
            self.counts[kind] += 1
            if kind not in ("none", "exact"):
                return
        _, dynamic = E.encode(game, game.state.current_color())
        legal = np.zeros(E.NUM_ACTIONS, bool)
        legal[E.legal_indices(actions)] = True
        action = E.action_index(decision)
        if not legal[action]:
            raise ValueError("recorded action is outside the encoded legal moves")
        self.rows.append(
            (
                dynamic,
                legal,
                action,
                game.state.num_turns,
                game.state.is_initial_build_phase,
            )
        )

    def after(self, game):
        winner = game.winning_color()
        self.winner = winner.value if winner else None


def _seed(record):
    game_id = (record.get("source") or {}).get("colonist_game")
    identity = (
        str(game_id).encode()
        if game_id is not None
        else json.dumps([record["map"], record["log"]], sort_keys=True).encode()
    )
    return int.from_bytes(hashlib.sha256(identity).digest()[:8], "big") & (
        (1 << 63) - 1
    )


def capture_net(path):
    """The hash of the net that played, which the bridge saves with each
    capture; "" for a capture from before that or recorded by a person."""
    with open(path) as stream:
        bot = json.load(stream).get("bot") or {}
    return bot.get("sha256", "")


MODES = ("human", "value", "opponent_openings", "opponent_known", "opponent_exact")


def export_capture(path, out_dir, keep=40, mode="human", stats=None):
    """Export one clean, finished game; return (path, sampled rows, total rows).

    Raises ValueError for games that cannot safely provide training labels.
    Existing shards are left untouched, making repeated imports idempotent.
    For opponent_exact, `stats` (a dict) gets the opponent's decisions by
    what was known of their hand: none, exact, ambiguous, unknown, mismatch.
    """
    conversion = convert(load_capture(path))
    record = conversion.record
    net = capture_net(path)
    if conversion.unsupported or record.get("incomplete"):
        raise ValueError("capture could not be fully converted")
    you = [color for color, player in record["players"].items() if player["you"]]
    if len(you) != 1:
        raise ValueError("capture has no single recording-player perspective")
    if mode not in MODES:
        raise ValueError("unknown export mode")
    opponent = mode.startswith("opponent_")
    if opponent and len(record["seats"]) != 2:
        raise ValueError("opponent exports require a two-player game")
    color = next(c for c in record["seats"] if c != you[0]) if opponent else you[0]
    observer = _Positions(
        color,
        setup_only=mode == "opponent_openings",
        known_only=mode == "opponent_known",
        hands=DevHands(record, color) if mode == "opponent_exact" else None,
    )
    report = replay(record, observer=observer)
    if not report.ok:
        raise ValueError("capture did not replay with matching cards and points")
    if stats is not None:
        stats.update(observer.counts)
    winner = observer.winner or record.get("winner")
    if winner is None and conversion.ended == "resignation":
        resigned = record.get("resigned")
        if resigned in record["seats"] and len(record["seats"]) == 2:
            winner = next(c for c in record["seats"] if c != resigned)
    if winner is None:
        raise ValueError("capture has no completed winner")
    if not observer.rows:
        raise ValueError("capture has no non-forced decisions")
    seed = _seed(record)
    os.makedirs(out_dir, exist_ok=True)
    # Two participants may each export the same game from their own view.
    # Give them separate files but the same seed, so validation keeps both
    # perspectives together.
    out = os.path.join(out_dir, f"shard_{seed:019d}_{color}_{mode}.npz")
    if os.path.exists(out):
        return out, 0, len(observer.rows)
    setup = [i for i, row in enumerate(observer.rows) if row[4]]
    later = [i for i, row in enumerate(observer.rows) if not row[4]]
    rng = random.Random(seed)
    count = max(0, keep - len(setup))
    chosen = sorted(setup + (rng.sample(later, count) if len(later) > count else later))
    rows = [observer.rows[i] for i in chosen]
    tmp = out + ".tmp.npz"
    np.savez_compressed(
        tmp,
        static=np.asarray([observer.static], dtype=np.float32),
        seed=np.asarray([seed], dtype=np.int64),
        dynamic=np.stack([r[0] for r in rows]).astype(np.float16),
        game=np.zeros(len(rows), dtype=np.int32),
        legal=np.packbits(np.stack([r[1] for r in rows]), axis=1),
        action=np.asarray(
            [-1 if mode == "value" else r[2] for r in rows], dtype=np.int16
        ),
        z=np.full(len(rows), 1 if winner == color else -1, dtype=np.int8),
        turn=np.asarray([r[3] for r in rows], dtype=np.int16),
        net=np.asarray([net]),
    )
    os.replace(tmp, out)
    return out, len(rows), len(observer.rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("captures", nargs="+", help="capture paths or globs")
    parser.add_argument("--out", required=True, help="output shard directory")
    parser.add_argument("--keep", type=int, default=40)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--human-perspective",
        action="store_true",
        help="confirm the recording account was the human whose moves are labels",
    )
    modes.add_argument(
        "--value-only",
        action="store_true",
        help="record outcomes but do not imitate a bot recording account's moves",
    )
    modes.add_argument(
        "--opponent-openings",
        action="store_true",
        help="record only the human opponent's setup moves from a bot-side capture",
    )
    args = parser.parse_args(argv)
    if args.keep < 1:
        parser.error("--keep must be positive")
    paths = sorted({p for pattern in args.captures for p in glob.glob(pattern)})
    if not paths:
        parser.error("no captures matched")
    good = 0
    for path in paths:
        try:
            mode = (
                "human"
                if args.human_perspective
                else "value" if args.value_only else "opponent_openings"
            )
            out, used, total = export_capture(path, args.out, args.keep, mode)
        except (OSError, ValueError, KeyError, IndexError) as error:
            print(
                f"SKIP {os.path.basename(path)}: {type(error).__name__}",
                file=sys.stderr,
            )
            continue
        print(
            f"{'EXISTS' if used == 0 else 'EXPORTED'} {os.path.basename(path)}: {used}/{total} decisions -> {out}"
        )
        good += 1
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
