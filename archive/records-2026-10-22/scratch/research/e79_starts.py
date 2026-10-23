"""E79: does a needless robber self-block cost wins when played out?

From live-loss's robber failure set (data/orchestrator/live-loss/robber_set.jsonl,
100 positions where the live bot put the robber on its own pips while a clean
hex blocked the opponent at least as much), build two starts per position:
  bad  = the log up to and including the bot's live robber move (as played)
  good = the same, but the robber goes to the first "good" hex instead
         (same victim, same stolen card), so the two arms differ only in the hex.

    .venv/bin/python data/orchestrator/scratch/research/e79_starts.py [--show] [--reps N]

Writes data/orchestrator/e79/starts_bad.jsonl and starts_good.jsonl (same
order, one line a position, e71_late.py's starts format) and checks every
start replays.
"""

import copy
import json
import os
import sys

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from catanatron.colonist import starts as S  # noqa: E402
from catanatron.colonist.capture import load as load_capture  # noqa: E402
from catanatron.colonist.convert import convert  # noqa: E402
from catanatron.colonist.replay import replay, resolve_hidden_draws  # noqa: E402
from catanatron.models.enums import ActionType  # noqa: E402
from e75_robber import col  # noqa: E402

SRC = "data/orchestrator/live-loss/robber_set.jsonl"
OUT = "data/orchestrator/e79"


class Find:
    """The log index of `you`'s k-th robber move after setup."""

    def __init__(self, you, k):
        self.you, self.k = you, k
        self.seen, self.index, self.found = -1, 0, None

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        if (
            self.found is None
            and decision.action_type == ActionType.MOVE_ROBBER
            and col(decision.color) == self.you
            and not game.state.is_initial_build_phase
        ):
            self.seen += 1
            if self.seen == self.k:
                self.found = self.index
        self.index += 1


def main():
    show = "--show" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    rows = [json.loads(line) for line in open(SRC)]
    bad_out, good_out, skipped = [], [], {}
    for r in rows:
        record = convert(load_capture(r["capture"])).record
        you = next(c for c, p in record["players"].items() if p["you"])
        finder = Find(you, r["k"])
        replay(record, observer=finder)
        i = finder.found
        if i is None:
            skipped["not found"] = skipped.get("not found", 0) + 1
            continue
        entry = {k: record[k] for k in S.KEPT if k in record}
        entry["log"] = resolve_hidden_draws(record)[: i + 1]
        payload, result = entry["log"][i]
        if show:
            print(json.dumps([payload, result]))
            show = False
        # payload: [color, "MOVE_ROBBER", [coord, victim]] (value at index 2)
        value = payload[2]
        if list(value[0]) != r["chosen"]:
            skipped["coord mismatch"] = skipped.get("coord mismatch", 0) + 1
            continue
        if value[1] is None:  # the live move stole nothing; good would steal
            skipped["no steal"] = skipped.get("no steal", 0) + 1
            continue
        good = copy.deepcopy(entry)
        good["log"][i][0][2][0] = list(r["good"][0])
        meta = {
            "capture": r["capture"],
            "bot": you,
            "turn": r["turn"],
            "won_live": r["won"],
            "chosen_own": r["chosen_own"],
            "chosen_opp": r["chosen_opp"],
            "bot_won": r["won"],
            "opp_mmr": None,
            "cut": i + 1,
        }
        ok = True
        for arm, e in (("bad", entry), ("good", good)):
            e["cuts"] = [i + 1]
            try:
                S.start_game(e, 1)
            except Exception as err:  # noqa: BLE001
                skipped[f"{arm} replay"] = skipped.get(f"{arm} replay", 0) + 1
                if show or len(skipped) < 3:
                    print(f"{arm} {r['capture']}: {err!r}"[:300])
                ok = False
                break
        if not ok:
            continue
        bad_out.append(dict(meta, start=S.pack(entry, i + 1)))
        good_out.append(dict(meta, start=S.pack(good, i + 1)))
    # each position REPS times in a row; e71_late.py seeds by line, so line j
    # gets the same dice in both files
    reps = int(sys.argv[sys.argv.index("--reps") + 1]) if "--reps" in sys.argv else 1
    for name, lines in (("starts_bad", bad_out), ("starts_good", good_out)):
        with open(f"{OUT}/{name}.jsonl", "w") as f:
            for line in lines:
                for rep in range(reps):
                    f.write(json.dumps(dict(line, rep=rep)) + "\n")
    print(f"{len(bad_out)} paired starts of {len(rows)} (x{reps}); skipped {skipped}")


if __name__ == "__main__":
    main()
