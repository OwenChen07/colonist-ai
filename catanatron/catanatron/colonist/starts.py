"""Self-play starting positions from real games against people.

    python -m catanatron.colonist.starts data/human --out data/human/starts.jsonl.gz

Self-play from an empty board only reaches the positions the net steers
into itself, so it rarely learns what people do to it (racing longest road
while building cities, say). A share of self-play games (selfplay.py,
--human-starts) instead start from a position of a real game and are played
out by the net with full search, so it learns those positions' value and
its best play in them.

Each line of the output is one game from the human dataset's training split
(holdout games are left out, as everywhere): its board, seats, rules and
whole log with every hidden draw resolved, but no names, and `cuts`, the log
indices where a turn starts (before the roll) between --min-turn and
--max-turn. start_game() replays a game to one of its cuts.

What the bot couldn't see (the person's unplayed development cards, the deck's
order) is filled in as resolve_hidden_draws does, then the deck is shuffled
for the self-play game; every search re-deals the opponent's hidden cards
anyway (mcts.redeal_hidden_cards).
"""

import argparse
import base64
import gzip
import json
import os
import random
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.record import resolve_hidden_draws
from catanatron.colonist.replay import replay
from catanatron.colonist.rules import COLONIST_1V1
from catanatron.models.enums import ActionType

MIN_TURN, MAX_TURN = 16, 60  # Catanatron turns (both players'), after setup
KEPT = ("map", "seats", "rules", "robber")


class _Cuts:
    """Replay observer: the log indices where a turn starts after setup."""

    def __init__(self, min_turn, max_turn):
        self.min_turn, self.max_turn = min_turn, max_turn
        self.cuts = []
        self.index = 0

    def before(self, game):
        pass

    def step(self, game, decision):
        state = game.state
        if (
            decision.action_type == ActionType.ROLL
            and not state.is_initial_build_phase
            and self.min_turn <= state.num_turns <= self.max_turn
        ):
            self.cuts.append(self.index)
        self.index += 1

    def after(self, game):
        pass


def start_entry(record, min_turn=MIN_TURN, max_turn=MAX_TURN):
    """The starts line for a converted record, or None if it has no cuts or
    isn't a colonist 1v1 game."""
    rules = record["rules"]
    if len(record["seats"]) != 2 or any(
        rules.get(k) != COLONIST_1V1[k]
        for k in ("vps_to_win", "discard_limit", "friendly_robber")
    ):
        return None
    observer = _Cuts(min_turn, max_turn)
    if not replay(record, observer=observer).ok or not observer.cuts:
        return None
    entry = {k: record[k] for k in KEPT if k in record}
    entry["log"] = resolve_hidden_draws(record)
    entry["cuts"] = observer.cuts
    return entry


def _cached(out, min_turn, max_turn):
    """The last build's lines by capture ({key: line}) and the captures it
    skipped, from OUT and its .keys file; empty if they don't match."""
    try:
        with open(out + ".keys") as stream:
            keys = json.load(stream)
        with gzip.open(out, "rt") as stream:
            lines = stream.read().splitlines(keepends=True)
    except (OSError, ValueError):
        return {}, set()
    if (keys.get("min_turn"), keys.get("max_turn")) != (min_turn, max_turn) or len(
        keys["lines"]
    ) != len(lines):
        return {}, set()
    return dict(zip(keys["lines"], lines)), set(keys["skipped"])


def build(dataset, out, min_turn=MIN_TURN, max_turn=MAX_TURN, log=print, full=False):
    """Writes the starts file from DATASET/index.jsonl's training games.
    Captures the last build already converted (OUT.keys) are reused, so a
    round only replays its new games; FULL rebuilds every game."""
    with open(os.path.join(dataset, "index.jsonl")) as stream:
        rows = [json.loads(line) for line in stream]
    rows = [r for r in rows if r.get("status") == "ok" and r.get("split") == "train"]
    root = os.environ.get("COLONIST_CAPTURES", "captures")
    cached, skipped = ({}, set()) if full else _cached(out, min_turn, max_turn)
    keys = {"min_turn": min_turn, "max_turn": max_turn, "lines": [], "skipped": []}
    games = cuts = new = 0
    tmp = out + ".tmp"
    with gzip.open(tmp, "wt") as stream:
        for row in rows:
            key = row["account"] + "/" + row["capture"]
            line = cached.get(key)
            if line is None and key in skipped:
                keys["skipped"].append(key)
                continue
            if line is None:
                new += 1
                try:
                    entry = start_entry(
                        convert(load_capture(os.path.join(root, key))).record,
                        min_turn,
                        max_turn,
                    )
                except Exception:  # one bad capture must not stop the rest
                    continue  # (and isn't remembered, so it is tried again)
                if entry is None:
                    keys["skipped"].append(key)
                    continue
                line = json.dumps(entry, separators=(",", ":")) + "\n"
            stream.write(line)
            keys["lines"].append(key)
            games += 1
            cuts += len(json.loads(line)["cuts"])
    with open(out + ".keys.tmp", "w") as stream:
        json.dump(keys, stream)
    os.replace(tmp, out)
    os.replace(out + ".keys.tmp", out + ".keys")
    log(f"{games} games ({new} new), {cuts} starting positions -> {out}")
    return games, cuts


def load(path):
    with gzip.open(path, "rt") as stream:
        return [json.loads(line) for line in stream]


def pack(entry, cut):
    """One cut of the entry as a short string (its log up to the cut,
    gzipped, base64), for a match task's start (evaluate.play_game)."""
    small = {k: v for k, v in entry.items() if k != "source_cut_count"}
    small.update(log=entry["log"][:cut], cuts=[cut])
    text = json.dumps(small, separators=(",", ":")).encode()
    return base64.b64encode(gzip.compress(text, mtime=0)).decode("ascii")


def unpack(text):
    return json.loads(gzip.decompress(base64.b64decode(text)))


def start_game(entry, seed):
    """A self-play Game at one of the entry's cuts (picked by `seed`), with
    its own random stream for everything from there on."""
    rng = random.Random(seed)
    # A targeted start may narrow the eligible cuts. Consume the draw used by
    # its source entry so that changing the cut list does not also change the
    # subsequent dice/development-card random stream.
    source_cut_count = entry.get("source_cut_count")
    if source_cut_count is None:
        cut = entry["cuts"][rng.randrange(len(entry["cuts"]))]
    else:
        rng.randrange(source_cut_count)
        cut = entry["cuts"][random.Random(seed).randrange(len(entry["cuts"]))]
    record = {**entry, "log": entry["log"][:cut]}
    game = None

    class _Last:
        def before(self, g):
            nonlocal game
            game = g

        def step(self, g, decision):
            pass

        def after(self, g):
            pass

    report = replay(record, observer=_Last())
    if not report.ok:
        raise ValueError(f"start position did not replay: {report.errors[:1]}")
    state = game.state
    state.random = random.Random(rng.random())
    game.random = state.random
    game.seed = seed
    state.random.shuffle(state.development_listdeck)
    return game


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dataset", help="human_dataset.py's output folder")
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-turn", type=int, default=MIN_TURN)
    parser.add_argument("--max-turn", type=int, default=MAX_TURN)
    parser.add_argument(
        "--full", action="store_true", help="convert every game again (no cache)"
    )
    args = parser.parse_args(argv)
    build(args.dataset, args.out, args.min_turn, args.max_turn, full=args.full)
    return 0


if __name__ == "__main__":
    sys.exit(main())
