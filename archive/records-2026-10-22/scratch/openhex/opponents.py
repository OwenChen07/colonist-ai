"""Per-bot stats from the pulled public games (data/openhex/opponents).

For each bot, its last PER_BOT completed 1v1 games: record, game length, where
the winner's VP came from (final board + cards), dev cards bought, bank trades
(from the replay, where we have it), and think time per move.
"""

import collections
import json
import os
import statistics as st
import sys

OUT = "/home/owen/colonist-ai/data/openhex/opponents"
PER_BOT = int(sys.argv[1]) if len(sys.argv) > 1 else 30


def plist(g):
    p = g["players"]
    return json.loads(p.replace("'", '"')) if isinstance(p, str) else p


def pyjson(x):
    return json.loads(x.replace("'", '"')) if isinstance(x, str) else x


games = json.load(open(f"{OUT}/games.json"))
board = {e["name"]: e for e in json.load(open(f"{OUT}/leaderboard.json"))}
names = collections.Counter(p["name"] for g in games for p in plist(g))


def vp_sources(s, color):
    b = s["board"]
    me = next(p for p in s["players"] if p["color"] == color)
    return {
        "settle": sum(1 for x in b["settlements"] if x["player"] == color),
        "city": sum(1 for x in b["cities"] if x["player"] == color),
        "devvp": me.get("devCards", {}).get("victoryPoint", 0)
        + me.get("playedDevCards", {}).get("victoryPoint", 0),
        "road": 2 if s.get("longestRoadPlayer") == color else 0,
        "army": 2 if s.get("largestArmyPlayer") == color else 0,
    }


def bought(s, color):
    me = next(p for p in s["players"] if p["color"] == color)
    return sum(me.get("devCards", {}).values()) + sum(
        me.get("playedDevCards", {}).values()
    )


def bank_trades(gid, color):
    path = f"{OUT}/replays/{gid}.json"
    if not os.path.exists(path):
        return None
    ev = json.load(open(path)).get("events", [])
    return sum(
        1
        for e in ev
        if e.get("type") == "bankTradeExecuted" and e.get("player") == color
    )


def med(xs):
    xs = [x for x in xs if x is not None]
    return f"{st.median(xs):.1f}" if xs else "-"


def mean(xs):
    xs = [x for x in xs if x is not None]
    return f"{st.mean(xs):.1f}" if xs else "-"


rows = []
for name, _ in names.most_common():
    mine = [
        g
        for g in games
        if g["status"] == "completed" and name in [p["name"] for p in plist(g)]
    ][:PER_BOT]
    if len(mine) < 5:
        continue
    wins, turns, wvp, bvp, buys, trades, ms, opps, last = (
        0,
        [],
        [],
        [],
        [],
        [],
        [],
        {},
        None,
    )
    for g in mine:
        s = json.loads(g["stateJson"])
        ps = plist(g)
        me = next(p for p in ps if p["name"] == name)
        opp = next(p for p in ps if p["name"] != name)
        won = g.get("winnerName") == name
        wins += won
        o = opps.setdefault(opp["name"], [0, 0])
        o[0] += won
        o[1] += 1
        # turnNumber counts both players' turns
        turns.append(g["turnNumber"])
        src = vp_sources(s, me["color"])
        (wvp if won else bvp).append(src)
        buys.append(bought(s, me["color"]))
        trades.append(bank_trades(g["id"], me["color"]))
        d = pyjson(g.get("durationStats") or "{}").get(me["botId"])
        if d and d.get("count"):
            ms.append(d["totalMs"] / d["count"])
        last = last or g["completedAt"]
    n = len(mine)
    lb = board.get(name, {})
    rows.append(
        (
            name,
            lb.get("elo"),
            lb.get("games"),
            n,
            wins,
            turns,
            wvp,
            bvp,
            buys,
            trades,
            ms,
            opps,
            last,
        )
    )

for name, elo, total, n, wins, turns, wvp, bvp, buys, trades, ms, opps, last in sorted(
    rows, key=lambda r: -(r[1] or 0)
):
    print(f"\n## {name}: ladder Elo {elo} after {total} games; last game {last[:10]}")
    print(f"last {n} games: {wins} wins; turns (both players) median {med(turns)}")
    if wvp:
        avg = {k: sum(v[k] for v in wvp) / len(wvp) for k in wvp[0]}
        print(
            f"VP in its {len(wvp)} wins: settlements {avg['settle']:.1f}, cities {avg['city']:.1f} (x2), "
            f"dev VP {avg['devvp']:.1f}, longest road {sum(v['road'] > 0 for v in wvp)}/{len(wvp)}, "
            f"largest army {sum(v['army'] > 0 for v in wvp)}/{len(wvp)}"
        )
    print(
        f"dev cards bought per game {mean(buys)}; bank trades per game {mean(trades)} "
        f"({sum(t is not None for t in trades)} replays); think ms/move median {med(ms)}"
    )
    print("opponents:", ", ".join(f"{k} {v[0]}-{v[1] - v[0]}" for k, v in opps.items()))
