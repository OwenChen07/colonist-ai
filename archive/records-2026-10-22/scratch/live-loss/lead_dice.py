"""Dice luck after the late lead: for each game in led_late.json with a lead,
replay and, at every roll from the lead turn on, compare each player's actual
resource yield with the expected yield under fair 2d6 (same board, robber as
it stands). Lost vs kept leads. Usage: lead_dice.py"""

import json
import math
import random
from multiprocessing import Pool

from catanatron.apply_action import yield_resources
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType
from catanatron.models.player import Color

P = {n: (6 - abs(7 - n)) / 36 for n in range(2, 13)}


class Obs:
    def __init__(self, me, them, lead_turn):
        self.cols = (me, them)
        self.lead_turn = lead_turn
        self.pending = None
        self.exp = [0.0, 0.0]
        self.act = [0, 0]
        self.rolls = 0
        self.sevens = 0

    def before(self, game):
        pass

    def _resolve(self, game):
        if self.pending is None:
            return
        rec = game.state.action_records[-1]
        n = sum(rec.result)
        y = self.pending
        self.pending = None
        self.rolls += 1
        self.sevens += n == 7
        for k in (0, 1):
            self.exp[k] += sum(P[m] * y[m][k] for m in y)
            self.act[k] += y.get(n, (0, 0))[k]

    def step(self, game, decision):
        self._resolve(game)
        s = game.state
        if decision.action_type != ActionType.ROLL or s.num_turns < self.lead_turn:
            return
        y = {}
        for m in P:
            if m == 7:
                continue
            pay, _ = yield_resources(s.board, s.resource_freqdeck, m)
            y[m] = tuple(sum(pay.get(c, [0])) for c in self.cols)
        self.pending = y

    def after(self, game):
        self._resolve(game)


def scan(r):
    try:
        conv = convert(load_capture(r["path"]))
        rec = conv.record
        you = next(c for c, p in rec["players"].items() if p["you"])
        them = next(c for c in rec["seats"] if c != you)
        obs = Obs(Color[you], Color[them], r["lead"]["turn"])
        rep = replay(rec, observer=obs)
        if not rep.ok:
            return None
        return {
            "path": r["path"],
            "won": r["won"],
            "rolls": obs.rolls,
            "sevens": obs.sevens,
            "exp": obs.exp,
            "act": obs.act,
            "dice": rep.dice_mode,
        }
    except Exception as e:  # noqa: BLE001
        return {"path": r["path"], "error": repr(e)[:200]}


def mean_ci(xs, iters=4000):
    n = len(xs)
    m = sum(xs) / n
    rng = random.Random(0)
    bs = sorted(sum(rng.choice(xs) for _ in range(n)) / n for _ in range(iters))
    return m, bs[int(0.025 * iters)], bs[int(0.975 * iters)]


def fmt(xs):
    m, lo, hi = mean_ci(xs)
    return f"{m:+.2f} [{lo:+.2f}, {hi:+.2f}]"


def diff_ci(a, b, iters=4000):
    rng = random.Random(1)
    ds = sorted(
        sum(rng.choice(a) for _ in a) / len(a) - sum(rng.choice(b) for _ in b) / len(b)
        for _ in range(iters)
    )
    d = sum(a) / len(a) - sum(b) / len(b)
    return f"{d:+.2f} [{ds[int(0.025 * iters)]:+.2f}, {ds[int(0.975 * iters)]:+.2f}]"


def main():
    rows = json.load(open("data/orchestrator/scratch/live-loss/led_late.json"))
    rows = [r for r in rows if r["lead"]]
    with Pool(6) as pool:
        res = [x for x in pool.imap_unordered(scan, rows, chunksize=4) if x]
    errs = [x for x in res if "error" in x]
    res = [x for x in res if "error" not in x and x["rolls"] > 0]
    json.dump(res, open("data/orchestrator/scratch/live-loss/lead_dice.json", "w"))
    print(f"leads {len(rows)} scanned {len(res)} errors {len(errs)}")
    if errs:
        print("first error", errs[0])
    modes = {}
    for x in res:
        modes[x["dice"]] = modes.get(x["dice"], 0) + 1
    print("dice modes", modes)
    for name, grp in (
        ("lost", [x for x in res if not x["won"]]),
        ("kept", [x for x in res if x["won"]]),
    ):
        if not grp:
            continue
        print(
            f"\n{name} leads n={len(grp)}  rolls after lead mean {sum(x['rolls'] for x in grp) / len(grp):.1f}"
        )
        print(
            "  bot luck (actual-expected cards)   ",
            fmt([x["act"][0] - x["exp"][0] for x in grp]),
        )
        print(
            "  person luck                        ",
            fmt([x["act"][1] - x["exp"][1] for x in grp]),
        )
        print(
            "  net luck bot-person                ",
            fmt(
                [(x["act"][0] - x["exp"][0]) - (x["act"][1] - x["exp"][1]) for x in grp]
            ),
        )
        print(
            "  net luck per roll                  ",
            fmt(
                [
                    ((x["act"][0] - x["exp"][0]) - (x["act"][1] - x["exp"][1]))
                    / x["rolls"]
                    for x in grp
                ]
            ),
        )
        print(
            "  bot expected per roll %.2f, person %.2f"
            % (
                sum(x["exp"][0] / x["rolls"] for x in grp) / len(grp),
                sum(x["exp"][1] / x["rolls"] for x in grp) / len(grp),
            )
        )
        print(
            "  sevens per roll %.3f (fair 0.167)"
            % (sum(x["sevens"] for x in grp) / sum(x["rolls"] for x in grp))
        )
    lost = [x for x in res if not x["won"]]
    kept = [x for x in res if x["won"]]
    if lost and kept:
        net = lambda x: (x["act"][0] - x["exp"][0]) - (
            x["act"][1] - x["exp"][1]
        )  # noqa: E731
        print(
            "\nnet luck lost - kept:",
            diff_ci([net(x) for x in lost], [net(x) for x in kept]),
        )
        # how many lost leads would flip on luck: net luck <= -3 cards
        bad = sum(1 for x in lost if net(x) <= -3)
        print(
            f"lost leads with net luck <= -3 cards: {bad}/{len(lost)}; kept: {sum(1 for x in kept if net(x) <= -3)}/{len(kept)}"
        )
        bad = sum(1 for x in lost if net(x) < 0)
        print(
            f"lost leads with net luck < 0: {bad}/{len(lost)}; kept: {sum(1 for x in kept if net(x) < 0)}/{len(kept)}"
        )


if __name__ == "__main__":
    main()
