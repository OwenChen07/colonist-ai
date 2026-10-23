"""Audit item 2: do colonist's rolls fit our balanced-dice model?

Usage: audit_dice.py [N_CAPTURES]
For a random sample of ok captures: replay, note the model's odds before each
roll and the total rolled. Reports dice breaks, log-likelihood vs plain dice,
and calibration of 7s and of each total.
"""

import collections
import json
import math
import random
import sys

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType

PLAIN = [min(t - 1, 13 - t) / 36 for t in range(2, 13)]


class Obs:
    def __init__(self):
        self.odds = []
        self.game = None

    def before(self, game):
        self.game = game

    def step(self, game, decision):
        if decision.action_type == ActionType.ROLL:
            d = game.state.dice
            self.odds.append((d.mode, d.odds(decision.color)))
        else:
            self.odds.append(None)

    def after(self, game):
        pass


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    rows = [json.loads(l) for l in open("data/human/index.jsonl")]
    rows = [r for r in rows if r["status"] == "ok"]
    random.Random(1).shuffle(rows)
    broke, games, rolls = collections.Counter(), 0, 0
    ll_bal = ll_plain = 0.0
    sev_buckets = collections.defaultdict(lambda: [0, 0.0, 0])  # n, sum p, hits
    tot_pred = [0.0] * 11
    tot_obs = [0] * 11
    for r in rows[:n]:
        rec = convert(load_capture(f"captures/{r['account']}/{r['capture']}")).record
        obs = Obs()
        rep = replay(rec, observer=obs)
        if not rep.ok:
            broke["replay not ok"] += 1
            continue
        games += 1
        broke["broke" if rep.dice_broke_at is not None else "held"] += 1
        # results of ROLL entries, in order of application
        from catanatron.colonist.record import resolve_hidden_draws

        log = resolve_hidden_draws(rec)
        for (payload, result), o in zip(log, obs.odds):
            if o is None:
                continue
            mode, p = o
            if mode != "balanced":
                continue
            t = int(result[0]) + int(result[1]) - 2
            rolls += 1
            ll_bal += math.log(max(p[t], 1e-12))
            ll_plain += math.log(PLAIN[t])
            b = min(int(p[5] * 20), 19)
            sev_buckets[b][0] += 1
            sev_buckets[b][1] += p[5]
            sev_buckets[b][2] += t == 5
            for i in range(11):
                tot_pred[i] += p[i]
            tot_obs[t] += 1
    print(f"games {games}, dice: {dict(broke)}, balanced rolls {rolls}")
    print(
        f"mean log-lik per roll: balanced {ll_bal/rolls:.4f}, plain {ll_plain/rolls:.4f}"
    )
    print("7 calibration (pred bucket: n, mean pred, observed):")
    for b in sorted(sev_buckets):
        k, sp, h = sev_buckets[b]
        print(f"  {b/20:.2f}: n={k} pred={sp/k:.3f} obs={h/k:.3f}")
    print("totals: pred vs observed counts")
    for i in range(11):
        sd = math.sqrt(tot_pred[i])
        print(
            f"  {i+2}: pred {tot_pred[i]:.0f} obs {tot_obs[i]} z {(tot_obs[i]-tot_pred[i])/max(sd,1e-9):+.1f}"
        )


if __name__ == "__main__":
    main()
