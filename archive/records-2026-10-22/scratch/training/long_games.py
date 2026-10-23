"""Live value error and bias by game length (games' last turn) and phase."""

import sys

import numpy as np

from catanatron.colonist.calibrate import load_value_shards
from catanatron.colonist.net import Net

net = Net.load(sys.argv[1])
parts = load_value_shards(sys.argv[2:])
LEN = ((0, 59, "<60"), (60, 89, "60-89"), (90, 10**6, "90+"))
PH = ((0, 20, "open"), (20, 60, "mid"), (60, 90, "late"), (90, 10**6, "90+"))
rows = []  # (game length, turn, v, z, game id)
for gi, (static, dynamic, game, z, turn) in enumerate(parts):
    _, v = net.forward_rows([static[g] for g in game], dynamic)
    v = np.asarray(v, np.float64).reshape(-1)
    length = int(turn.max()) if len(turn) else 0
    for a, b, c in zip(turn, v, z):
        rows.append((length, int(a), float(b), float(c), gi))
r = np.array(rows)
L, T, V, Z, G = r.T
print(
    f"{len(parts)} games, {len(r)} positions, max turn range {L.min():.0f}-{L.max():.0f}"
)
for lo, hi, name in LEN:
    m = (L >= lo) & (L <= hi)
    games = np.unique(G[m])
    # one z per game: the bot's result
    wins = np.mean([Z[G == g][0] > 0 for g in games]) if len(games) else float("nan")
    print(
        f"\nlength {name}: {len(games)} games, bot won {wins:.1%}, "
        f"MSE {np.mean((V[m] - Z[m]) ** 2):.3f}, mean v {V[m].mean():+.3f} vs mean z {Z[m].mean():+.3f}"
    )
    for plo, phi, pname in PH:
        k = m & (T >= plo) & (T < phi)
        if k.sum() < 50:
            continue
        acc = np.mean(np.sign(V[k]) == np.sign(Z[k]))
        print(
            f"  {pname:>4}: n {int(k.sum()):5d}  MSE {np.mean((V[k] - Z[k]) ** 2):.3f}  "
            f"acc {acc:.1%}  mean v {V[k].mean():+.3f}  mean z {Z[k].mean():+.3f}  "
            f"|v| {np.abs(V[k]).mean():.3f}"
        )
# No hindsight: by current turn only (the bot knows the turn, not the length).
print("\nby current turn, all games: v bin -> mean v / mean z (n)")
for tlo, thi in ((40, 60), (60, 75), (75, 90), (90, 10**6)):
    cells = []
    for a, b in ((-1, -0.5), (-0.5, 0.5), (0.5, 1.01)):
        s = (T >= tlo) & (T < thi) & (V >= a) & (V < b)
        if s.sum():
            cells.append(
                f"[{a:+.1f},{b:+.1f}) {V[s].mean():+.2f}/{Z[s].mean():+.2f} n{int(s.sum())}"
            )
    k = (T >= tlo) & (T < thi)
    print(
        f"  turn {tlo}-{thi}: MSE {np.mean((V[k]-Z[k])**2):.3f} bias {V[k].mean()-Z[k].mean():+.3f} | "
        + "  ".join(cells)
    )
# Calibration in long games' late phase: binned v vs realised z.
k = (L >= 90) & (T >= 60)
if k.sum():
    print("\n90+ games, turn>=60: v bin -> mean z (n)")
    for a, b in ((-1, -0.5), (-0.5, -0.2), (-0.2, 0.2), (0.2, 0.5), (0.5, 1.01)):
        s = k & (V >= a) & (V < b)
        if s.sum():
            print(
                f"  v in [{a:+.1f},{b:+.1f}): mean v {V[s].mean():+.3f} mean z {Z[s].mean():+.3f} (n {int(s.sum())})"
            )
k = (L < 90) & (T >= 60)
if k.sum():
    print("\n<90 games, turn>=60: v bin -> mean z (n)")
    for a, b in ((-1, -0.5), (-0.5, -0.2), (-0.2, 0.2), (0.2, 0.5), (0.5, 1.01)):
        s = k & (V >= a) & (V < b)
        if s.sum():
            print(
                f"  v in [{a:+.1f},{b:+.1f}): mean v {V[s].mean():+.3f} mean z {Z[s].mean():+.3f} (n {int(s.sum())})"
            )
