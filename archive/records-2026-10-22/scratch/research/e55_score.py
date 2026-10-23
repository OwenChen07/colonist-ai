"""E55: score people models on holdout/opponent_exact, overall and on the
decisions where a development card can be played (hold-or-play turns).

Usage: e55_score.py HOLDOUT_DIR name=net.npz ...
"""

import sys

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist import opponent_model as O
from catanatron.colonist.net import Net
from catanatron.models.enums import ActionType

PLAY = {
    ActionType.PLAY_KNIGHT_CARD,
    ActionType.PLAY_YEAR_OF_PLENTY,
    ActionType.PLAY_ROAD_BUILDING,
    ActionType.PLAY_MONOPOLY,
}
play_idx = np.array([i for i, (t, _) in enumerate(E.action_table()) if t in PLAY])

holdout = sys.argv[1]
nets = {k: Net.load(v) for k, v in (a.split("=", 1) for a in sys.argv[2:])}
paths = O.shard_paths([holdout])
tot = {}  # (name, slice) -> [n, correct, loss, per-game losses]
for static, dynamic, legal, action in O.labelled_rows(paths):
    dev = legal[:, play_idx].any(1)
    played = np.isin(action, play_idx)
    for start in range(0, len(action), 512):
        sl = slice(start, start + 512)
        d, l, a, dv, pl = dynamic[sl], legal[sl], action[sl], dev[sl], played[sl]
        logps = {"uniform": O._log_softmax_legal(np.zeros(l.shape), l)}
        for name, net in nets.items():
            logps[name] = O._log_softmax_legal(net.forward(static, d)[0], l)
        for name, lp in logps.items():
            ce = -lp[np.arange(len(a)), a]
            ok = lp.argmax(1) == a
            for sname, m in (("all", np.ones(len(a), bool)), ("dev-playable", dv)):
                t = tot.setdefault((name, sname), [0, 0, 0.0, 0, 0])
                t[0] += int(m.sum())
                t[1] += int(ok[m].sum())
                t[2] += float(ce[m].sum())
                # on dev-playable rows: how often the model's top move plays a card
                t[3] += int(np.isin(lp.argmax(1)[m], play_idx).sum())
                t[4] += int(pl[m].sum())
print(f"{len(paths)} games")
print(
    f"{'':>10} {'slice':>13} {'moves':>7} {'top-1':>7} {'CE':>7} {'top=play':>9} {'person played':>13}"
)
for (name, sname), (n, c, loss, tp, pp) in tot.items():
    print(
        f"{name:>10} {sname:>13} {n:>7} {c / n:>7.1%} {loss / n:>7.3f} "
        f"{tp / n:>9.1%} {pp / n:>13.1%}"
    )
