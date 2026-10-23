"""E21 report: a pinned net on bot7 against same-hour controls bot3/4/5.

Usage: e21_report.py "2026-10-18 09:00" [pin_sha_prefix] [verdict_games]
  start   Toronto local time the pin went live (or a unix time).
  pin     optional net_sha256 prefix; bot7 games on another net after the start
          are counted apart and left out (e.g. 63d05e7d for round 69).
  verdict games on bot7 for the verdict (default 200).

Prints ranked W-L, win rate and Wilson 95% intervals for bot7 and the controls
since the start, the same for each one's previous 200 games, and the
difference-in-differences (bot7's change minus the controls' change) with a
normal 95% interval. Verdict rule (E21): bot7 gains >= 5 pp over the controls'
change at 200 games. Counts the same games as the lead's context report
(ranked, not own_bot, winner set; win = winner == bot_color). No names.
Keep this file: the lead runs it for E21."""

import datetime as dt
import json
import math
import sys
from collections import Counter
from zoneinfo import ZoneInfo

PINNED = "bot7"
CONTROLS = ("bot3", "bot4", "bot5")
BEFORE = 200
TORONTO = ZoneInfo("America/Toronto")


def parse_start(text):
    try:
        return float(text)
    except ValueError:
        local = dt.datetime.fromisoformat(text).replace(tzinfo=TORONTO)
        return local.timestamp()


def ranked(account):
    rows = []
    with open(f"data/{account}/games.jsonl") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if (
                r.get("queue", "ranked") == "ranked"
                and not r.get("own_bot")
                and r.get("winner")
            ):
                rows.append(r)
    rows.sort(key=lambda r: r.get("at", 0))
    return rows


def wins(rs):
    return sum(r["winner"] == r.get("bot_color") for r in rs)


def wilson(w, n, z=1.96):
    if n == 0:
        return 0.0, 1.0
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def line(name, rs):
    n, w = len(rs), wins(rs)
    if n == 0:
        return f"  {name:<22} no games"
    lo, hi = wilson(w, n)
    return (
        f"  {name:<22} {w}-{n - w} ({n} games)  {100 * w / n:.1f}%  "
        f"[{100 * lo:.1f}, {100 * hi:.1f}]"
    )


def rate(rs):
    n = len(rs)
    p = wins(rs) / n if n else 0.0
    return p, (p * (1 - p) / n if n else 0.0)


def when(t):
    return dt.datetime.fromtimestamp(t, TORONTO).strftime("%b %d, %-I:%M %p")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    start = parse_start(sys.argv[1])
    pin = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "-" else None
    verdict_games = int(sys.argv[3]) if len(sys.argv) > 3 else 200

    p_all = ranked(PINNED)
    p_before = [r for r in p_all if r.get("at", 0) < start][-BEFORE:]
    p_after = [r for r in p_all if r.get("at", 0) >= start]
    nets = Counter(
        (r.get("label") or "?", (r.get("net_sha256") or "?")[:8]) for r in p_after
    )
    if pin:
        p_after = [r for r in p_after if (r.get("net_sha256") or "").startswith(pin)]
    end = p_after[-1]["at"] if p_after else start
    # Controls over the same hours as bot7's counted games.
    c_after, c_before = [], []
    per_control = []
    for account in CONTROLS:
        rs = ranked(account)
        a = [r for r in rs if start <= r.get("at", 0) <= end]
        b = [r for r in rs if r.get("at", 0) < start][-BEFORE:]
        c_after += a
        c_before += b
        per_control.append((account, a, b))

    print(
        f"E21 report, start {when(start)} Toronto, now {when(dt.datetime.now().timestamp())}"
    )
    print(
        f"bot7 nets since start: {dict(nets)}"
        + (f"; counting {pin} only" if pin else "")
    )
    print("Since the start:")
    print(line("bot7 (pinned)", p_after))
    print(line("controls 3/4/5 pooled", c_after))
    for account, a, _ in per_control:
        print(line(f"  {account}", a))
    print(f"Previous {BEFORE} games before the start:")
    print(line("bot7", p_before))
    print(line("controls pooled", c_before))
    for account, _, b in per_control:
        print(line(f"  {account}", b))

    if not p_after or not c_after:
        print("No pinned games since the start yet.")
        return
    pa, va = rate(p_after)
    pb, vb = rate(p_before)
    ca, wa = rate(c_after)
    cb, wb = rate(c_before)
    did = (pa - pb) - (ca - cb)
    se = math.sqrt(va + vb + wa + wb)
    print(
        f"bot7 change {100 * (pa - pb):+.1f} pp, controls change {100 * (ca - cb):+.1f} pp"
    )
    print(
        f"Difference in differences: {100 * did:+.1f} pp  "
        f"[{100 * (did - 1.96 * se):+.1f}, {100 * (did + 1.96 * se):+.1f}]"
    )
    n = len(p_after)
    if n < verdict_games:
        print(f"No verdict yet: {n} of {verdict_games} bot7 games.")
    else:
        verdict = "ADOPT (>= +5 pp)" if did >= 0.05 else "NO ADOPTION (< +5 pp)"
        print(f"Verdict at {n} games: {verdict}")
    if n >= 100:
        print("100+ games: run lr_by_round.py 66 for the longest-road split by round.")


if __name__ == "__main__":
    main()
