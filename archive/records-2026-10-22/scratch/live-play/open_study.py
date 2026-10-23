"""Opening study part 2: what each player's two setup settlements produce.

For every captured live game (data/human/index.jsonl, status ok) replays the
first 8 log entries and records, for the bot and the opponent: seat, pips per
resource over both settlements, total pips, distinct resources, and the port
touched (if any).  Writes open_rows.jsonl; prints a summary by seat.

    .venv/bin/python data/orchestrator/scratch/live-play/open_study.py [workers]
"""

import json
import math
import os
import sys
from multiprocessing import Pool

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import decision_of, game_for_record
from catanatron.models.enums import ActionRecord, ActionType
from catanatron.serialization import _tupled, action_from_json

OUT = "data/orchestrator/scratch/live-play/open_rows.jsonl"
RATINGS = "data/orchestrator/analysis/opponent_ratings.json"
RES = ("WOOD", "BRICK", "SHEEP", "WHEAT", "ORE")


def pips(n):
    return 0 if n is None else 6 - abs(7 - n)


def study(task):
    account, capture, won = task
    path = os.path.join("captures", account, capture)
    try:
        record = convert(load_capture(path)).record
    except Exception as e:  # noqa: BLE001
        return {"capture": capture, "error": repr(e)[:80]}
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return {"capture": capture, "error": "seats"}
    you, seats = you[0], record["seats"]
    game = game_for_record(record)
    m = game.state.board.map
    port_of = {}
    for res, nodes in m.port_nodes.items():
        for n in nodes:
            port_of[n] = res or "3:1"
    out = {}
    for payload, result in record["log"][:8]:
        action = action_from_json(payload)
        decision = decision_of(action, game.playable_actions)
        if decision.action_type == ActionType.BUILD_SETTLEMENT:
            x = decision.color.value
            who = "bot" if x == you else "opp"
            row = out.setdefault(
                who,
                {
                    "seat": seats.index(x) + 1,
                    "pips": dict.fromkeys(RES, 0),
                    "port": None,
                    "nodes": [],
                },
            )
            n = decision.value
            row["nodes"].append(n)
            for t in m.adjacent_tiles[n]:
                if t.resource in RES:
                    row["pips"][t.resource] += pips(t.number)
            if n in port_of:
                row["port"] = port_of[n]
        game.execute(
            decision,
            validate_action=False,
            action_record=ActionRecord(action, _tupled(result)),
        )
    src = record.get("source") or {}
    return {
        "capture": capture,
        "account": account,
        "won": won,
        "ranked": bool(src.get("ranked")),
        **out,
    }


def wilson(k, n, z=1.96):
    if n == 0:
        return "n=0"
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{100 * p:4.1f}% [{100 * (c - h):4.1f}-{100 * (c + h):4.1f}] ({k}/{n})"


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def report():
    rows = [json.loads(l) for l in open(OUT) if l.strip()]
    rows = [r for r in rows if "bot" in r and "opp" in r]
    ratings = json.load(open(RATINGS)) if os.path.exists(RATINGS) else {}
    for r in rows:
        r["mmr"] = (ratings.get(r["capture"]) or {}).get("opp_mmr")
        for who in ("bot", "opp"):
            p = r[who]["pips"]
            r[who]["tot"] = sum(p.values())
            r[who]["kinds"] = sum(v > 0 for v in p.values())
            r[who]["wb"] = p["WOOD"] + p["BRICK"]
            r[who]["ow"] = p["ORE"] + p["WHEAT"]
    print(f"games {len(rows)}")
    for name, sub in (
        ("all", rows),
        ("ranked", [r for r in rows if r["ranked"]]),
        (
            "ranked opp 2100+",
            [r for r in rows if r["ranked"] and (r["mmr"] or 0) >= 2100],
        ),
    ):
        print(f"\n== {name}")
        for seat in (1, 2):
            s = [r for r in sub if r["bot"]["seat"] == seat]
            print(f" bot seat {seat}: win {wilson(sum(r['won'] for r in s), len(s))}")
            for who, lab in (("bot", "bot"), ("opp", "opp")):
                print(
                    f"   {lab:3s} (seat {seat if who == 'bot' else 3 - seat}): pips {mean([r[who]['tot'] for r in s]):5.2f}"
                    f" wood+brick {mean([r[who]['wb'] for r in s]):4.2f} ore+wheat {mean([r[who]['ow'] for r in s]):4.2f}"
                    f" kinds {mean([r[who]['kinds'] for r in s]):4.2f} port {mean([r[who]['port'] is not None for r in s]):4.2f}"
                )
            # people's seat-2 winners vs bot's seat-2 games
        s2 = [r for r in sub if r["bot"]["seat"] == 2]
        p2w = [r["opp"] for r in sub if r["bot"]["seat"] == 1 and not r["won"]]
        print(
            f"  people seat 2 when they beat us (n={len(p2w)}): pips {mean([o['tot'] for o in p2w]):5.2f}"
            f" wb {mean([o['wb'] for o in p2w]):4.2f} ow {mean([o['ow'] for o in p2w]):4.2f}"
        )
        # bot seat-2 win rate by pip edge over opponent and by wood+brick
        for lab, key, cuts in (
            (
                "pip edge (bot-opp)",
                lambda r: r["bot"]["tot"] - r["opp"]["tot"],
                (-3, 0, 3),
            ),
            ("bot wood+brick pips", lambda r: r["bot"]["wb"], (4, 7, 10)),
            ("bot ore+wheat pips", lambda r: r["bot"]["ow"], (4, 7, 10)),
        ):
            parts = []
            lo = -99
            for c in cuts + (99,):
                b = [r for r in s2 if lo <= key(r) < c]
                parts.append(f"[{lo},{c}) {wilson(sum(r['won'] for r in b), len(b))}")
                lo = c
            print(f"  seat 2 by {lab}: " + " | ".join(parts))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        return report()
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    rows = {}
    for line in open("data/human/index.jsonl"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "ok" and r.get("bot_won") is not None:
                rows[(r["account"], r["capture"])] = r["bot_won"]
    tasks = [(a, c, w) for (a, c), w in sorted(rows.items(), key=lambda kv: kv[0][1])]
    os.nice(15)
    with Pool(workers) as pool, open(OUT, "w") as out:
        for row in pool.imap(study, tasks, chunksize=16):
            out.write(json.dumps(row) + "\n")
    report()


if __name__ == "__main__":
    main()
