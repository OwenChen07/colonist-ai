"""The human-game training set, from every bot account's captures.

    python -m catanatron.colonist.human_dataset 'captures/*/bridge-*.json' \
        --games 'data/*/games.jsonl' --out data/human

For each finished game against a person:

  - the bot's positions with the real result (`value`: value targets), and
  - the person's moves whenever they held no unplayed development card
    (`opponent`: their whole view is in the capture then; the card count is
    public, and in 1v1 every resource that moves is seen by both),

go to OUT/train/... or, for a fixed 15% of games chosen by game id,
OUT/holdout/..., which training never reads (the live yardstick).

Every game is kept whoever won: human mistakes are part of what the value
must learn (what wins against people) and of what an opponent model must
predict. Left out: games against our own bots, games shorter than
--min-rolls (early leaves and disconnects), and anything that doesn't convert
and replay exactly.

OUT/index.jsonl gets one line per capture, with why it was left out, or its
split, result and play statistics: for the bot and the person, roads built
before their third settlement and dead roads (branches that lead to no
building of theirs) at the end. Captures already in the index are skipped, so
it can run again as games arrive.
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.human_data import _seed, export_capture
from catanatron.colonist.replay import replay
from catanatron.models.enums import CITY, ROAD, SETTLEMENT, ActionType
from catanatron.models.player import Color

HOLDOUT = 0.15
EXPORTS = (("value", "value", 40), ("opponent_known", "opponent", 10_000))


def split_of(seed, holdout=HOLDOUT):
    """'holdout' for a fixed share of games, by game id; else 'train'."""
    return "holdout" if (seed * 2654435761) % 1000 < holdout * 1000 else "train"


class _RoadStats:
    """Replay observer: roads before each player's third settlement, and dead
    roads at the end."""

    def __init__(self):
        self.settlements = {}
        self.early_roads = {}
        self.final = None

    def before(self, game):
        pass

    def step(self, game, decision):
        if game.state.is_initial_build_phase:
            return
        color = decision.color.value
        if decision.action_type == ActionType.BUILD_SETTLEMENT:
            self.settlements[color] = self.settlements.get(color, 2) + 1
        elif decision.action_type == ActionType.BUILD_ROAD:
            if self.settlements.get(color, 2) < 3:
                self.early_roads[color] = self.early_roads.get(color, 0) + 1

    def after(self, game):
        self.final = game

    def stats(self, color):
        state = self.final.state
        built = state.buildings_by_color[Color[color]]
        roads = {tuple(sorted(e)) for e in built[ROAD]}
        return {
            "roads": len(roads),
            "early_roads": self.early_roads.get(color, 0),
            "dead_roads": dead_roads(roads, set(built[SETTLEMENT]) | set(built[CITY])),
            "settlements": len(built[SETTLEMENT]),
            "cities": len(built[CITY]),
        }


def dead_roads(roads, buildings):
    """Roads on branches that end at no building: repeatedly remove a road
    whose end has no other road and no building."""
    roads = set(roads)
    removed = 0
    while True:
        degree = {}
        for a, b in roads:
            degree[a] = degree.get(a, 0) + 1
            degree[b] = degree.get(b, 0) + 1
        leaves = [
            r for r in roads if any(degree[n] == 1 and n not in buildings for n in r)
        ]
        if not leaves:
            return removed
        roads -= set(leaves)
        removed += len(leaves)


def _own_bot_captures(games_files):
    own = set()
    for path in games_files:
        with open(path) as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("own_bot"):
                    own.add(row["capture"])
    return own


def process(path, out, own_names, own_captures, min_rolls, holdout):
    """One capture: exports and its index row."""
    name = Path(path).name
    row = {"capture": name, "account": Path(path).parent.name}
    conversion = convert(load_capture(path))
    record = conversion.record
    if conversion.unsupported or conversion.ended not in ("win", "resignation"):
        return {**row, "status": "incomplete"}
    you = [c for c, p in record["players"].items() if p["you"]]
    them = [c for c in record["seats"] if c not in you]
    if len(you) != 1 or len(them) != 1:
        return {**row, "status": "not 1v1"}
    opponent = record["players"][them[0]].get("name")
    if name in own_captures or opponent in own_names:
        return {**row, "status": "own_bot"}
    rolls = sum(1 for entry in record["log"] if entry[0][1] == "ROLL")
    if rolls < min_rolls:
        return {**row, "status": "short", "rolls": rolls}
    observer = _RoadStats()
    if not replay(record, observer=observer).ok:
        return {**row, "status": "replay failed"}
    seed = _seed(record)
    split = split_of(seed, holdout)
    exported = {}
    for mode, folder, keep in EXPORTS:
        try:
            _, count, total = export_capture(
                path, os.path.join(out, split, folder), keep, mode
            )
            exported[folder] = total
        except ValueError as error:
            exported[folder] = f"skipped: {error}"
    winner = observer.final.winning_color()
    winner = winner.value if winner else record.get("winner")
    if winner is None and record.get("resigned"):
        winner = them[0] if record["resigned"] == you[0] else you[0]
    return {
        **row,
        "status": "ok",
        "split": split,
        "game": seed,
        "rolls": rolls,
        "ended": conversion.ended,
        "bot_won": winner == you[0],
        "exported": exported,
        "bot": observer.stats(you[0]),
        "human": observer.stats(them[0]),
    }


def build(
    captures,
    games_files=(),
    out="data/human",
    own_names=(),
    min_rolls=20,
    holdout=HOLDOUT,
    log=print,
):
    os.makedirs(out, exist_ok=True)
    index = os.path.join(out, "index.jsonl")
    done = set()
    if os.path.exists(index):
        with open(index) as stream:
            done = {json.loads(line)["capture"] for line in stream}
    own_captures = _own_bot_captures(games_files)
    counts = {}
    with open(index, "a") as stream:
        for path in captures:
            if Path(path).name in done:
                continue
            try:
                row = process(
                    path, out, set(own_names), own_captures, min_rolls, holdout
                )
            except Exception as error:  # one bad capture must not stop the rest
                row = {
                    "capture": Path(path).name,
                    "account": Path(path).parent.name,
                    "status": "error",
                    "error": f"{type(error).__name__}: {error}"[:200],
                }
            stream.write(json.dumps(row) + "\n")
            done.add(row["capture"])
            key = row["status"] if row["status"] != "ok" else row["split"]
            counts[key] = counts.get(key, 0) + 1
    log(f"new captures: {counts or 'none'}")
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("captures", nargs="+", help="capture paths or globs")
    parser.add_argument("--games", default="data/*/games.jsonl")
    parser.add_argument("--out", default="data/human")
    parser.add_argument(
        "--own-accounts",
        default=os.environ.get("COLONIST_OWN_ACCOUNTS", ""),
        help="our bots' usernames, comma-separated",
    )
    parser.add_argument("--min-rolls", type=int, default=20)
    parser.add_argument("--holdout", type=float, default=HOLDOUT)
    args = parser.parse_args(argv)
    paths = sorted({p for pattern in args.captures for p in glob.glob(pattern)})
    if not paths:
        parser.error("no captures matched")
    build(
        paths,
        glob.glob(args.games),
        args.out,
        [n for n in args.own_accounts.split(",") if n],
        args.min_rolls,
        args.holdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
