"""Per opponent, from our own completed OpenHex games (data/openhex/games).

W-L, seat split, turns, the winner's VP sources on the final board, opening
placement (pips, resource mix, ports, which seat picks), dev cards, knights,
bank trades, robber use (whom it hits: our best hex or not), player trades.

Usage: .venv/bin/python data/orchestrator/scratch/openhex/per_opp.py
"""

import collections
import glob
import json
import statistics as st

from catanatron.openhex.board import Board

PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return f"{st.mean(xs):.1f}" if xs else "-"


by = collections.defaultdict(list)
for path in sorted(glob.glob("data/openhex/games/*.json")):
    g = json.load(open(path))
    fs = g.get("final_state")
    if (
        not fs
        or g.get("game", {}).get("status") != "completed"
        or not fs.get("winner")
        or not g.get("tiles")
    ):
        continue
    me = g["me"]
    them = [c for c in g["turn_order"] if c != me][0]
    opp = [p["name"] for p in g["game"]["players"] if p["color"] != me][0]
    board = Board(g["tiles"], g["ports"])
    adj = board.catan_map.adjacent_tiles
    port_nodes = set()
    for res, nodes in board.catan_map.port_nodes.items():
        port_nodes |= set(nodes)
    owned = {me: collections.Counter(), them: collections.Counter()}
    r = dict(
        won=fs["winner"] == me, first=g["turn_order"][0] == me, turns=fs["turnNumber"]
    )
    open_pips = collections.Counter()
    open_res = {me: collections.Counter(), them: collections.Counter()}
    open_port = collections.Counter()
    cnt = {
        k: collections.Counter()
        for k in (
            "dev",
            "knight",
            "bank",
            "settle",
            "city",
            "road",
            "rob_hit_best",
            "rob",
            "mono",
            "yop",
            "rb",
            "steal",
        )
    }
    for e in sorted(
        {e["seq"]: e for e in g["events"]}.values(), key=lambda e: e["seq"]
    ):
        ev = e["event"]
        t, p = ev["type"], ev.get("player")
        if t in ("setupSettlementPlaced", "settlementBuilt", "cityBuilt"):
            n = board.node(ev["vertex"])
            owned[p][n] += 1
            if t == "setupSettlementPlaced":
                open_pips[p] += sum(PIPS.get(tl.number, 0) for tl in adj[n])
                for tl in adj[n]:
                    if tl.resource:
                        open_res[p][tl.resource] += PIPS.get(tl.number, 0)
                if n in port_nodes:
                    open_port[p] += 1
            if t == "settlementBuilt":
                cnt["settle"][p] += 1
            if t == "cityBuilt":
                cnt["city"][p] += 1
        elif t in ("robberMoved", "knightPlayed"):
            if t == "knightPlayed":
                cnt["knight"][p] += 1
            other = me if p == them else them
            # value of each hex to the victim: pips x buildings on it
            val = collections.Counter()
            for n, m in owned[other].items():
                for tl in adj[n]:
                    if tl.number:
                        val[
                            board.hex_id(tl) if hasattr(board, "hex_id") else id(tl)
                        ] += (m * PIPS[tl.number])
            cnt["rob"][p] += 1
        elif t == "devCardBought":
            cnt["dev"][p] += 1
        elif t == "bankTradeExecuted":
            cnt["bank"][p] += 1
        elif t == "monopolyPlayed":
            cnt["mono"][p] += 1
        elif t == "yearOfPlentyPlayed":
            cnt["yop"][p] += 1
        elif t == "roadBuildingPlayed":
            cnt["rb"][p] += 1
        elif t == "resourceStolen":
            cnt["steal"][p] += 1
    w = fs["winner"]
    pl = {x["color"]: x for x in fs["players"]}
    b = fs["board"]
    r["w_settle"] = sum(1 for x in b["settlements"] if x["player"] == w)
    r["w_city"] = sum(1 for x in b["cities"] if x["player"] == w)
    r["w_lr"] = fs.get("longestRoadPlayer") == w
    r["w_la"] = fs.get("largestArmyPlayer") == w
    # hidden dev VP: the winner's total minus what the board shows
    total = next(
        (
            e["event"].get("victoryPoints")
            for e in g["events"]
            if e["event"]["type"] == "gameWon"
        ),
        15,
    )
    r["w_devvp"] = (
        total - r["w_settle"] - 2 * r["w_city"] - 2 * r["w_lr"] - 2 * r["w_la"]
    )
    r["trades"] = len(fs.get("trades") or [])
    for side, c in (("us", me), ("them", them)):
        r[f"{side}_open"] = open_pips[c]
        r[f"{side}_port"] = open_port[c]
        r[f"{side}_ore_wheat"] = open_res[c]["ORE"] + open_res[c]["WHEAT"]
        r[f"{side}_wood_brick"] = open_res[c]["WOOD"] + open_res[c]["BRICK"]
        for k, v in cnt.items():
            r[f"{side}_{k}"] = v[c]
        r[f"{side}_lr"] = fs.get("longestRoadPlayer") == c
        r[f"{side}_la"] = fs.get("largestArmyPlayer") == c
    by[opp].append(r)

if by:
    any_g = next(iter(by.values()))
print("port nodes on last board:", len(port_nodes))
allr = [x for rs in by.values() for x in rs]
f = [x for x in allr if x["first"]]
print(
    f"ALL n={len(allr)} wins {sum(x['won'] for x in allr)}; we 1st {sum(x['won'] for x in f)}/{len(f)}; "
    f"we 2nd {sum(x['won'] for x in allr if not x['first'])}/{len(allr)-len(f)}"
)
order = sorted(by, key=lambda o: -len(by[o]))
print("opp | n | W-L | W-L when we go 1st / 2nd | turns (W / L) |")
for o in order:
    rs = by[o]
    w = sum(x["won"] for x in rs)
    f = [x for x in rs if x["first"]]
    s = [x for x in rs if not x["first"]]
    print(
        f"{o}: n={len(rs)} {w}-{len(rs)-w}; 1st {sum(x['won'] for x in f)}-{len(f)-sum(x['won'] for x in f)}; "
        f"2nd {sum(x['won'] for x in s)}-{len(s)-sum(x['won'] for x in s)}; "
        f"turns W {mean([x['turns'] for x in rs if x['won']])} L {mean([x['turns'] for x in rs if not x['won']])}"
    )
    lost = [x for x in rs if not x["won"]]
    if lost:
        print(
            f"   their wins: settle {mean([x['w_settle'] for x in lost])} city {mean([x['w_city'] for x in lost])} "
            f"devVP {mean([x['w_devvp'] for x in lost])} LR {sum(x['w_lr'] for x in lost)}/{len(lost)} LA {sum(x['w_la'] for x in lost)}/{len(lost)}"
        )
    won = [x for x in rs if x["won"]]
    if won:
        print(
            f"   our wins:   settle {mean([x['w_settle'] for x in won])} city {mean([x['w_city'] for x in won])} "
            f"devVP {mean([x['w_devvp'] for x in won])} LR {sum(x['w_lr'] for x in won)}/{len(won)} LA {sum(x['w_la'] for x in won)}/{len(won)}"
        )
    line = "   us/them:"
    for k in (
        "open",
        "ore_wheat",
        "wood_brick",
        "port",
        "dev",
        "knight",
        "bank",
        "settle",
        "city",
        "mono",
        "yop",
        "rb",
        "steal",
    ):
        line += (
            f" {k} {mean([x['us_'+k] for x in rs])}/{mean([x['them_'+k] for x in rs])}"
        )
    line += f" LR {sum(x['us_lr'] for x in rs)}/{sum(x['them_lr'] for x in rs)} LA {sum(x['us_la'] for x in rs)}/{sum(x['them_la'] for x in rs)}"
    line += f" playertrades {mean([x['trades'] for x in rs])}"
    print(line)
