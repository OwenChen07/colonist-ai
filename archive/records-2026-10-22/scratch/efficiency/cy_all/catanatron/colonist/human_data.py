"""Export verified, player-perspective Colonist captures as training shards.

Only use captures recorded by the human whose decisions are to be learned.
An opponent's unplayed development cards are hidden in a one-sided capture,
so replaying that opponent's moves does not recreate their information state.
For a bot-side capture against a human, use --value-only for outcome labels
and --opponent-openings for the human's setup moves, when no development cards
exist yet. The output contains numeric inputs and labels, not names or chat.

    python -m catanatron.colonist.human_data 'captures/human-*.json' \
        --out data/human --human-perspective
"""

import argparse
import glob
import hashlib
import json
import os
import random
import sys

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.state_functions import player_num_dev_cards


class _Positions:
    def __init__(self, color, setup_only=False, known_only=False):
        self.color = color
        self.setup_only = setup_only
        # Only decisions where the player holds no unplayed development card:
        # then everything they knew is in the capture (their card count is
        # public, and in 1v1 every resource that moves is seen by both).
        self.known_only = known_only
        self.rows = []
        self.static = None
        self.winner = None

    def before(self, game):
        self.static = E.encode_static(game.state.board.map)

    def step(self, game, decision):
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


def export_capture(path, out_dir, keep=40, mode="human"):
    """Export one clean, finished game; return (path, sampled rows, total rows).

    Raises ValueError for games that cannot safely provide training labels.
    Existing shards are left untouched, making repeated imports idempotent.
    """
    conversion = convert(load_capture(path))
    record = conversion.record
    net = capture_net(path)
    if conversion.unsupported or record.get("incomplete"):
        raise ValueError("capture could not be fully converted")
    you = [color for color, player in record["players"].items() if player["you"]]
    if len(you) != 1:
        raise ValueError("capture has no single recording-player perspective")
    if mode not in ("human", "value", "opponent_openings", "opponent_known"):
        raise ValueError("unknown export mode")
    opponent = mode in ("opponent_openings", "opponent_known")
    if opponent and len(record["seats"]) != 2:
        raise ValueError("opponent exports require a two-player game")
    color = next(c for c in record["seats"] if c != you[0]) if opponent else you[0]
    observer = _Positions(
        color,
        setup_only=mode == "opponent_openings",
        known_only=mode == "opponent_known",
    )
    report = replay(record, observer=observer)
    if not report.ok:
        raise ValueError("capture did not replay with matching cards and points")
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
