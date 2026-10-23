"""E78: how often the bot's robber move is one the own-pips filter drops.
Before the switch this is the share the filter would change; after it, it
should be 0 (any hit means the filter is not on). Per account, ranked games
with a capture from START (unix seconds or UTC iso) to END.

    .venv/bin/python data/orchestrator/scratch/live-play/e78_robber.py ACCOUNT START [END]
"""

import json
import sys
from datetime import datetime, timezone

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.colonist.robber_policy import robber_root_actions
from catanatron.colonist import encode as E
from catanatron.models.enums import ActionType


def stamp(s):
    if s.replace(".", "").isdigit():
        return float(s)
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


def col(c):
    return getattr(c, "value", c)


class Obs:
    def __init__(self, you):
        self.you = you
        self.moves = self.dropped = self.narrowed = 0

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        if (
            decision.action_type != ActionType.MOVE_ROBBER
            or col(decision.color) != self.you
        ):
            return
        actions = [
            a for a in game.playable_actions if a.action_type == ActionType.MOVE_ROBBER
        ]
        if not actions:
            return
        keep = robber_root_actions(game, actions)
        self.moves += 1
        self.narrowed += len(keep) < len(actions)
        kept = {E.action_key(a) for a in keep}
        # hexes only: the steal victim does not change the filter's verdict
        if decision.value[0] not in {a.value[0] for a in keep} and kept:
            self.dropped += 1


def main():
    acct, start = sys.argv[1], stamp(sys.argv[2])
    end = stamp(sys.argv[3]) if len(sys.argv) > 3 else 1e12
    games = moves = dropped = narrowed = skipped = 0
    hit_games = 0
    for line in open(f"data/{acct}/games.jsonl"):
        if not line.strip():
            continue
        g = json.loads(line)
        if (
            g.get("queue") != "ranked"
            or not g.get("capture")
            or not start <= g["at"] < end
        ):
            continue
        try:
            record = convert(load_capture(f"captures/{acct}/{g['capture']}")).record
            you = [c for c, p in record["players"].items() if p["you"]]
            if len(you) != 1:
                skipped += 1
                continue
            obs = Obs(you[0])
            replay(record, observer=obs)
        except (OSError, ValueError, KeyError, IndexError, AttributeError, TypeError):
            skipped += 1
            continue
        games += 1
        moves += obs.moves
        dropped += obs.dropped
        narrowed += obs.narrowed
        hit_games += obs.dropped > 0
    share = 100 * dropped / moves if moves else float("nan")
    print(
        f"{acct}: {games} games ({skipped} skipped), {moves} bot robber moves; "
        f"filter narrows the choice on {narrowed}; bot's move is one it drops on "
        f"{dropped} ({share:.1f}%), in {hit_games} games"
    )


if __name__ == "__main__":
    sys.exit(main())
