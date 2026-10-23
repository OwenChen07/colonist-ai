"""Where the clock goes: per prompt, time and simulations used against the
caps in bridge.BUDGETS, over the last N ranked games per account.
Reads data/<account>/bridge.log (moves) and games.jsonl (queue, sims)."""

import json
import re
import statistics
import sys
from collections import Counter, defaultdict

ROOT = "/home/owen/colonist-ai/data"
ACCOUNTS = (
    sys.argv[1].split(",") if len(sys.argv) > 1 else ["bot3", "bot4", "bot5", "bot7"]
)
N = int(sys.argv[2]) if len(sys.argv) > 2 else 200
# prompt: (cap seconds, simulations multiple), as in bridge.BUDGETS
CAPS = {
    "INIT_SETTLEMENT1": (20.0, 25 / 3),
    "INIT_SETTLEMENT2": (20.0, 25 / 3),
    "INIT_ROAD": (5.0, 5 / 3),
    "ROLL": (1.0, 1 / 3),
    "PLAY_TURN": (5.0, 2.0),
    "ROBBER": (5.0, 2.0),
    "DISCARD": (5.0, 2.0),
}
MOVE = re.compile(r"  move: (.*) \(win chance \d+%, (\d+) ms(?:, (\d+) sims)?\)")
SAVED = re.compile(r"game saved to (\S+)")


def parse_games(path):
    """capture name -> list of moves (label, ms, sims) of that game."""
    games, cur = {}, []
    with open(path, errors="replace") as f:
        for line in f:
            if "following: game started" in line:
                cur = []
                continue
            m = MOVE.match(line.rstrip("\n"))
            if m:
                cur.append((m.group(1), int(m.group(2)), int(m.group(3) or -1)))
                continue
            s = SAVED.search(line)
            if s:
                name = s.group(1).rsplit("/", 1)[-1]
                if cur:
                    games[name] = cur
                cur = []
    return games


def classify(moves):
    """(prompt, label, ms, sims, turn index) per move."""
    out, settlements, pre_roll, turn, last_init_settle = [], 0, False, 0, False
    for label, ms, sims in moves:
        if label.startswith("settlement") and settlements < 2:
            settlements += 1
            out.append((f"INIT_SETTLEMENT{settlements}", label, ms, sims, -1))
            last_init_settle = True
            continue
        if label.startswith("road") and last_init_settle:
            last_init_settle = False
            out.append(("INIT_ROAD", label, ms, sims, -1))
            if settlements == 2:
                pre_roll = True
            continue
        last_init_settle = False
        if label == "roll":
            out.append(("ROLL", label, ms, sims, turn))
            pre_roll = False
            continue
        if label.startswith("robber"):
            out.append(("ROBBER", label, ms, sims, turn))
            continue
        if label.startswith("discard"):
            out.append(("DISCARD", label, ms, sims, turn))
            continue
        if pre_roll and not label.startswith("road"):
            out.append(("ROLL", label, ms, sims, turn))
        else:
            out.append(("PLAY_TURN", label, ms, sims, turn))
        if label == "end turn":
            pre_roll = True
            turn += 1
    return out


def pct(xs, q):
    if not xs:
        return 0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def main():
    rows = defaultdict(list)  # prompt -> (ms, sims, cap_ms, cap_sims)
    turn_ms, turn_actions = [], []
    labels = Counter()
    per_acct = {}
    for acct in ACCOUNTS:
        games = parse_games(f"{ROOT}/{acct}/bridge.log")
        recs = []
        with open(f"{ROOT}/{acct}/games.jsonl") as f:
            for line in f:
                try:
                    g = json.loads(line)
                except ValueError:
                    continue
                if g.get("queue") == "ranked" and g.get("capture") in games:
                    recs.append(g)
        recs = recs[-N:]
        per_acct[acct] = len(recs)
        for g in recs:
            sims0 = g.get("simulations") or 2400
            turns = defaultdict(lambda: [0, 0])
            for prompt, label, ms, sims, turn in classify(games[g["capture"]]):
                labels[re.sub(r" (at|on|to) .*| \d.*", "", label)] += 1
                cap_s, scale = CAPS[prompt]
                rows[prompt].append((ms, sims, cap_s * 1000, round(sims0 * scale)))
                if prompt == "PLAY_TURN":
                    turns[turn][0] += ms
                    turns[turn][1] += 1
            for t_ms, n in turns.values():
                turn_ms.append(t_ms)
                turn_actions.append(n)
    print("ranked games per account:", per_acct)
    hdr = f"{'prompt':16} {'moves':>6} {'searched':>8} {'med ms':>7} {'p90 ms':>7} {'cap ms':>7} {'med sims':>8} {'cap sims':>8} {'hit sims':>8} {'hit time':>8} {'short':>6} {'<50% cap':>8}"
    print(hdr)
    for prompt in CAPS:
        r = rows[prompt]
        searched = [x for x in r if x[1] > 0]
        if not r:
            continue
        hs = sum(1 for ms, s, cm, cs in searched if s >= 0.99 * cs)
        ht = sum(1 for ms, s, cm, cs in searched if s < 0.99 * cs and ms >= 0.9 * cm)
        short = len(searched) - hs - ht
        half = sum(1 for ms, s, cm, cs in searched if s < 0.5 * cs and ms < 0.5 * cm)
        n = max(1, len(searched))
        print(
            f"{prompt:16} {len(r):6} {len(searched):8} "
            f"{int(statistics.median([x[0] for x in searched] or [0])):7} "
            f"{pct([x[0] for x in searched], 0.9):7} {int(r[0][2]):7} "
            f"{int(statistics.median([x[1] for x in searched] or [0])):8} {r[0][3]:8} "
            f"{hs / n:8.0%} {ht / n:8.0%} {short / n:6.0%} {half / n:8.0%}"
        )
    print(
        f"turn (actions after the roll): {len(turn_ms)} turns, actions med {statistics.median(turn_actions)}, "
        f"p90 {pct(turn_actions, 0.9)}; thinking per turn med {statistics.median(turn_ms) / 1000:.1f} s, "
        f"p90 {pct(turn_ms, 0.9) / 1000:.1f} s, max {max(turn_ms) / 1000:.1f} s"
    )
    print("labels:", labels.most_common(25))


if __name__ == "__main__":
    main()
