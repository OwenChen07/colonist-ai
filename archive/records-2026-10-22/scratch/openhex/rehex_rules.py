"""Reverse-engineer rehex-2.11's play from completed games (lead's ask, Oct 19 10:05 PM).

Sources: our completed games vs rehex (data/openhex/games) and the public
replays already on disk that include rehex (data/openhex/opponents/replays).
Replays every game's events, tracks both hands exactly (1v1: steals are
visible to both), and records each rehex decision by class.

Usage: .venv/bin/python data/orchestrator/scratch/openhex/rehex_rules.py [--games]
"""

import collections
import glob
import json
import statistics as st
import sys

from catanatron.openhex.board import (
    EDGES,
    HEXES,
    VERTICES,
    as_edge,
    as_hex,
    as_vertex,
    vertex_hexes,
)

REHEX = "dcc6de42b64b4b78"
PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
TERRAIN = {
    "forest": "wood",
    "hills": "brick",
    "pasture": "sheep",
    "fields": "wheat",
    "mountains": "ore",
}
RES = ["wood", "brick", "sheep", "wheat", "ore"]
COST = {
    "road": {"wood": 1, "brick": 1},
    "settlement": {"wood": 1, "brick": 1, "sheep": 1, "wheat": 1},
    "city": {"wheat": 2, "ore": 3},
    "dev": {"sheep": 1, "wheat": 1, "ore": 1},
}
NEIGH = collections.defaultdict(set)
for a, b in EDGES:
    NEIGH[a].add(b)
    NEIGH[b].add(a)


def can(hand, what):
    return all(hand[r] >= n for r, n in COST[what].items())


def load():
    """[(source, tiles{hex:(res,num)}, port_of{vertex: rate/res}, robber, rehex color, winner color, events)]"""
    out = []
    for path in sorted(glob.glob("data/openhex/games/*.json")):
        g = json.load(open(path))
        names = {p["color"]: p["name"] for p in g.get("game", {}).get("players", [])}
        fs = g.get("final_state") or {}
        if (
            g.get("game", {}).get("status") != "completed"
            or not fs.get("winner")
            or not g.get("tiles")
        ):
            continue
        rx = [c for c, n in names.items() if n.startswith("rehex")]
        if not rx:
            continue
        tiles = {
            as_hex(t["hex"]): (TERRAIN.get(t["terrain"]), t.get("number"))
            for t in g["tiles"]
        }
        ports = g["ports"]
        evs = [
            e["event"]
            for e in sorted(
                {e["seq"]: e for e in g["events"]}.values(), key=lambda e: e["seq"]
            )
        ]
        robber = [h for h, (r, n) in tiles.items() if r is None][0]
        out.append(
            (
                "ours",
                path,
                tiles,
                ports,
                robber,
                rx[0],
                fs["winner"],
                evs,
                g["turn_order"],
            )
        )
    for path in sorted(glob.glob("data/openhex/opponents/replays/*.json")):
        r = json.load(open(path))
        if r.get("status") != "completed" or not any(
            p["botId"] == REHEX for p in r["players"]
        ):
            continue
        rx = [p["color"] for p in r["players"] if p["botId"] == REHEX][0]
        win = [p["color"] for p in r["players"] if p["botId"] == r.get("winnerBotId")]
        tiles = {
            as_hex(t["location"]): (TERRAIN.get(t["terrain"]), t.get("number"))
            for t in r["board"]["tiles"]
        }
        ports = [
            {"edge": p["vertices"], "rate": p["rate"], "resource": p.get("resource")}
            for p in r["board"]["ports"]
        ]
        opp = [p["name"] for p in r["players"] if p["botId"] != REHEX][0]
        out.append(
            (
                "pub:" + opp,
                path,
                tiles,
                ports,
                as_hex(r["board"]["robber"]),
                rx,
                win[0] if win else None,
                r["events"],
                r["turnOrder"],
            )
        )
    return out


def main():
    games = load()
    C = collections.defaultdict(collections.Counter)  # class -> counter
    L = collections.defaultdict(list)  # class -> list of values
    per_game = []
    for src, path, tiles, ports, robber, rx, winner, evs, order in games:
        op = [c for c in order if c != rx][0]
        port_of = {}
        for p in ports:
            a, b = as_edge(p["edge"])
            port_of[a] = port_of[b] = p.get("resource") or "3:1"
        hand = {c: collections.Counter() for c in order}
        settle = {c: set() for c in order}
        city = {c: set() for c in order}
        roads = {c: 0 for c in order}
        vp_award = {c: 0 for c in order}
        lr = la = None
        knights = {c: 0 for c in order}
        devs = {c: 0 for c in order}  # cards held (unplayed, incl. this turn's)
        rolled = False
        turn = 0
        turn_actions = []
        first_city_turn = None
        settle_at_first_city = None
        rehex_bought = []
        hexn_dev_cards = collections.Counter()

        def vp(c):
            return len(settle[c]) + 2 * len(city[c]) + vp_award[c]

        def prod(c, h):
            """c's pips on hex h (cities double)."""
            n = tiles[h][1]
            if not n:
                return 0
            p = PIPS[n]
            return sum(p for v in settle[c] if h in vertex_hexes(v)) + sum(
                2 * p for v in city[c] if h in vertex_hexes(v)
            )

        def vscore(v):
            return sum(
                PIPS.get(tiles[h][1], 0)
                for h in vertex_hexes(v)
                if h in tiles and tiles[h][0]
            )

        def vres(v):
            return sorted(
                tiles[h][0] for h in vertex_hexes(v) if h in tiles and tiles[h][0]
            )

        def occupied():
            return set().union(*settle.values(), *city.values())

        def legal_spots():
            occ = occupied()
            return [
                v
                for v in VERTICES
                if v not in occ
                and not (NEIGH[v] & occ)
                and any(h in tiles for h in vertex_hexes(v))
            ]

        def dist(v, targets):
            seen, frontier, d = {v}, [v], 0
            while frontier:
                if any(x in targets for x in frontier):
                    return d
                nxt = []
                for x in frontier:
                    for y in NEIGH[x]:
                        if y not in seen:
                            seen.add(y)
                            nxt.append(y)
                frontier, d = nxt, d + 1
            return 99

        setup_n = 0
        pub = src != "ours"
        held_knights = ready_knights = 0
        start_blocked, start_la = False, None
        for ev in evs:
            t = ev["type"]
            p = ev.get("player")
            mine = p == rx
            if t == "setupSettlementPlaced":
                v = as_vertex(ev["vertex"])
                if mine:
                    spots = legal_spots()
                    ranked = sorted(spots, key=vscore, reverse=True)
                    best = vscore(ranked[0])
                    k = 1 + len(settle[rx])
                    feats = lambda s: (
                        [
                            (tiles[h][0], PIPS.get(tiles[h][1], 0))
                            for h in vertex_hexes(s)
                            if h in tiles and tiles[h][0]
                        ],
                        port_of.get(s),
                    )
                    have = set(vres(next(iter(settle[rx])))) if settle[rx] else set()
                    L["setup_choices"].append(
                        (k, have, [feats(s) for s in spots], spots.index(v))
                    )
                    rank = 1 + sum(1 for s in spots if vscore(s) > vscore(v))
                    C[f"setup{k}_pip_rank"][min(rank, 6)] += 1
                    L[f"setup{k}_pips_minus_best"].append(vscore(v) - best)
                    L[f"setup{k}_pips"].append(vscore(v))
                    rs = vres(v)
                    C[f"setup{k}_ntypes"][len(set(rs))] += 1
                    for r in rs:
                        C[f"setup{k}_res"][r] += 1
                    C[f"setup{k}_port"][port_of.get(v, "none") != "none"] += 1
                    if settle[op]:
                        C[f"setup{k}_dist_to_opp"][dist(v, settle[op])] += 1
                    if k == 2:
                        first = set(vres(next(iter(settle[rx]))))
                        new = set(rs) - first
                        C["setup2_new_types"][len(new)] += 1
                        # among top-pip spots, did it prefer new resource types?
                        alt_best_new = max(
                            len(set(vres(s)) - first)
                            for s in spots
                            if vscore(s) == vscore(v)
                        )
                        C["setup2_max_new_types_at_same_pips"][
                            (len(new), alt_best_new)
                        ] += 1
                        both = first | set(rs)
                        C["setup_total_types"][len(both)] += 1
                        C["setup_has_ore_wheat"][
                            ("ore" in both) + ("wheat" in both)
                        ] += 1
                        C["setup_has_wood_brick"][
                            ("wood" in both) + ("brick" in both)
                        ] += 1
                    # first player's seat
                    C["setup_seat"][(order.index(rx), k)] += 1
                settle[p].add(v)
                setup_n += 1
            elif t in ("setupRoadPlaced", "freeRoadPlaced", "roadBuilt"):
                roads[p] += 1
                if t == "roadBuilt":
                    for r, n in COST["road"].items():
                        hand[p][r] -= n
                    if mine:
                        turn_actions.append("road")
                        C["road_when_opp_has_LR"][lr == op] += 1
                        C["road_when_self_has_LR"][lr == rx] += 1
                        C["road_build_ctx"][
                            (
                                "could_settle"
                                if can(
                                    hand[rx] + collections.Counter(COST["road"]),
                                    "settlement",
                                )
                                else "-"
                            )
                        ] += 1
                elif t == "freeRoadPlaced" and mine:
                    turn_actions.append("freeroad")
            elif t == "setupResourcesGranted":
                hand[p].update(ev["resources"])
            elif t == "diceRolled":
                rolled = True
                if mine:
                    turn_actions.append("roll")
            elif t == "resourcesProduced":
                for c, res in ev.get("production", {}).items():
                    hand[c].update(res)
            elif t == "settlementBuilt":
                v = as_vertex(ev["vertex"])
                settle[p].add(v)
                for r, n in COST["settlement"].items():
                    hand[p][r] -= n
                if mine:
                    turn_actions.append("settle")
                    spots = [s for s in legal_spots()]  # after placing; approximate
                    L["settle_pips"].append(vscore(v))
                    if port_of.get(v):
                        C["settle_on_port"][port_of[v]] += 1
            elif t == "cityBuilt":
                v = as_vertex(ev["vertex"])
                settle[p].discard(v)
                city[p].add(v)
                for r, n in COST["city"].items():
                    hand[p][r] -= n
                if mine:
                    turn_actions.append("city")
                    # which settlement did it upgrade: best pips?
                    cands = list(settle[rx]) + [v]
                    rank = 1 + sum(1 for s in cands if vscore(s) > vscore(v))
                    C["city_on_best_settlement_rank"][rank] += 1
                    if first_city_turn is None:
                        first_city_turn = turn
                        settle_at_first_city = len(settle[rx]) + 1
            elif t == "devCardBought":
                for r, n in COST["dev"].items():
                    hand[p][r] -= n
                devs[p] += 1
                if mine:
                    turn_actions.append("dev")
                    h = hand[rx] + collections.Counter(COST["dev"])
                    C["dev_buy_could_city"][can(h, "city") and len(settle[rx]) > 0] += 1
                    C["dev_buy_could_settle"][can(h, "settlement")] += 1
                    C["dev_buy_vp_bucket"][min(vp(rx) // 3 * 3, 12)] += 1
                    if ev.get("card"):
                        held_knights += ev["card"] == "knight"
                        hexn_dev_cards[ev["card"]] += 1
                        C["dev_card_drawn"][ev["card"]] += 1
            elif t == "knightPlayed":
                knights[p] += 1
                devs[p] -= 1
                h = as_hex(ev.get("hex", ev.get("location")))
                if mine:
                    held_knights -= 1
                    turn_actions.append("knight")
                    C["knight_before_roll"][not rolled] += 1
                    C["knight_robber_was_on_own"][prod(rx, robber) > 0] += 1
                    C["knight_when_holding_LA"][la == rx] += 1
                    C["knight_count_vs_opp"][
                        (
                            (knights[rx] - knights[op])
                            if abs(knights[rx] - knights[op]) < 3
                            else 9
                        )
                    ] += 1
                    C["knight_vp_diff"][max(-3, min(3, vp(rx) - vp(op)))] += 1
                    robber_choice(C, "knight", h, robber, tiles, prod, rx, op, vp, hand)
                robber = h
            elif t == "robberMoved":
                h = as_hex(ev.get("hex", ev.get("location")))
                if mine:
                    robber_choice(C, "seven", h, robber, tiles, prod, rx, op, vp, hand)
                robber = h
            elif t == "resourceStolen":
                r = ev.get("resource")
                tgt = ev.get("target")
                if r:
                    hand[tgt][r] -= 1
                    hand[p][r] += 1
            elif t == "resourcesDiscarded":
                if mine:
                    before = hand[rx].copy()
                    n = sum(before.values())
                    C["discard_count_ok"][sum(ev["resources"].values()) == n // 2] += 1
                    for r in RES:
                        L["disc_" + r].append((before[r], ev["resources"].get(r, 0)))
                    kept = before - collections.Counter(ev["resources"])
                    C["discard_keeps_city"][can(kept, "city")] += 1
                    C["discard_keeps_settle"][can(kept, "settlement")] += 1
                    C["discard_keeps_dev"][can(kept, "dev")] += 1
                    C["discard_keeps_road"][can(kept, "road")] += 1
                    # does it discard from the largest pile first?
                    order_ = sorted(RES, key=lambda r: -before[r])
                    C["discard_from_largest"][
                        ev["resources"].get(order_[0], 0) > 0
                    ] += 1
                    C["discard_kept_counts"][tuple(kept[r] for r in RES)] += 0  # touch
                    L["discard_detail"].append((dict(before), ev["resources"]))
                hand[p].subtract(ev["resources"])
            elif t == "bankTradeExecuted":
                off, req = ev["offering"], ev["requesting"]
                if mine:
                    turn_actions.append("trade")
                    ((ro, no),) = off.items()
                    ((rq, nq),) = req.items()
                    rate = no // nq
                    C["trade_rate"][rate] += 1
                    C["trade_give"][ro] += 1
                    C["trade_get"][rq] += 1
                    before = hand[rx].copy()
                    L["trade_handsize"].append(sum(before.values()))
                    C["trade_handsize_bucket"][min(sum(before.values()), 12)] += 1
                    C["trade_gives_largest_pile"][
                        before[ro] == max(before.values())
                    ] += 1
                    C["trade_before_roll"][not rolled] += 1
                    L["trade_pending"].append((dict(before), ro, no, rq))
                hand[p].subtract(off)
                hand[p].update(req)
            elif t == "monopolyPlayed":
                tot = sum(ev["collected"].values())
                hand[p][ev["resource"]] += tot
                for c, n in ev["collected"].items():
                    hand[c][ev["resource"]] -= n
                devs[p] -= 1
                if mine:
                    turn_actions.append("mono")
                    opp_before = hand[op].copy()
                    opp_before[ev["resource"]] += tot
                    C["mono_takes_opp_largest"][
                        opp_before[ev["resource"]] == max(opp_before.values())
                    ] += 1
                    C["mono_resource"][ev["resource"]] += 1
                    L["mono_collected"].append(tot)
                    C["mono_before_roll"][not rolled] += 1
            elif t == "yearOfPlentyPlayed":
                devs[p] -= 1
                for r in ev["resources"]:
                    hand[p][r] += 1
                if mine:
                    turn_actions.append("yop")
                    C["yop_pick"][tuple(sorted(ev["resources"]))] += 1
                    C["yop_then_can_city"][can(hand[rx], "city")] += 1
                    C["yop_then_can_settle"][can(hand[rx], "settlement")] += 1
            elif t == "roadBuildingPlayed":
                devs[p] -= 1
                if mine:
                    turn_actions.append("rb")
            elif t == "longestRoadAwarded":
                if lr:
                    vp_award[lr] -= 2
                lr = p
                vp_award[p] += 2
                if p == rx:
                    C["LR_taken"]["rehex"] += 1
                else:
                    C["LR_taken"]["opp"] += 1
            elif t == "largestArmyAwarded":
                if la:
                    vp_award[la] -= 2
                la = p
                vp_award[p] += 2
            elif t == "turnEnded":
                if p == rx:
                    # knight decision at this turn (public replays: card types known)
                    if ready_knights > 0:
                        blocked = start_blocked
                        race = (
                            "holds LA"
                            if start_la == rx
                            else (
                                "LA reachable"
                                if knights[rx] - ("knight" in turn_actions) + 1
                                >= max(3, knights[op] + (la == op))
                                else "no LA yet"
                            )
                        )
                        C["knight_ready_played"][
                            (
                                ("own hex blocked" if blocked else "not blocked"),
                                "knight" in turn_actions,
                            )
                        ] += 1
                        if not blocked:
                            C["knight_ready_unblocked_by_race"][
                                (race, "knight" in turn_actions)
                            ] += 1
                    C["turns_by_LR"][
                        (
                            "opp" if lr == op else "self" if lr == rx else "none",
                            turn_actions.count("road") + turn_actions.count("freeroad"),
                        )
                    ] += 0
                    L[
                        "roads_per_turn_"
                        + ("oppLR" if lr == op else "selfLR" if lr == rx else "noLR")
                    ].append(turn_actions.count("road"))
                    trade_then = [
                        a for a in turn_actions if a not in ("roll", "knight")
                    ]
                    if "trade" in trade_then:
                        after = trade_then[trade_then.index("trade") :]
                        C["trade_followed_by"][
                            next((a for a in after if a != "trade"), "nothing")
                        ] += 1
                    turn += 1
                    seq = ",".join(a for a in turn_actions)
                    C["turn_seq"][seq] += 1
                    hs = sum(hand[rx].values())
                    L["end_handsize"].append(hs)
                    C["end_hand_gt7"][hs > 7] += 1
                    C["end_hand_gt9"][hs > 9] += 1
                    # left resources unspent that could build
                    h = hand[rx]
                    C["end_could_city"][
                        can(h, "city") and len(settle[rx]) > 0 and len(city[rx]) < 4
                    ] += 1
                    C["end_could_dev"][can(h, "dev")] += 1
                    if vp(rx) >= 12:
                        C["endgame_turn_seq"][seq] += 1
                    neg = [r for r in RES if h[r] < 0]
                    C["hand_tracking_negative"][bool(neg)] += 1
                turn_actions = []
                rolled = False
                # next player's turn starts: snapshot for rehex's knight decision
                ready_knights = held_knights - 0 if pub else 0
                start_blocked = prod(rx, robber) > 0
                start_la = la
        per_game.append(
            dict(
                src=src,
                path=path.split("/")[-1][:8],
                won=winner == rx,
                turns=turn,
                first=order[0] == rx,
                first_city_turn=first_city_turn,
                settle_at_first_city=settle_at_first_city,
                final=(len(settle[rx]), len(city[rx]), roads[rx], knights[rx]),
                opp_final=(len(settle[op]), len(city[op]), roads[op], knights[op]),
                lr=lr == rx,
                la=la == rx,
            )
        )

    print("games:", len(games), collections.Counter(g[0] for g in games))
    rec = collections.Counter(
        (g["src"] if g["src"] == "ours" else "pub", g["won"]) for g in per_game
    )
    print("rehex record:", dict(rec))
    byopp = collections.defaultdict(lambda: [0, 0])
    for g in per_game:
        byopp[g["src"]][0 if g["won"] else 1] += 1
    print("rehex W-L by source:", dict(byopp))
    for k in sorted(C):
        if k == "turn_seq" or k == "endgame_turn_seq":
            continue
        print(k, dict(sorted(C[k].items(), key=lambda kv: str(kv[0]))))
    print("turn_seq top:", C["turn_seq"].most_common(25))
    print("endgame_turn_seq top:", C["endgame_turn_seq"].most_common(15))
    for k in sorted(L):
        v = L[k]
        if k.startswith("disc_"):
            have = sum(a for a, b in v)
            gone = sum(b for a, b in v)
            print(k, f"held {have} discarded {gone} ({gone / max(have, 1):.0%})")
        elif k in ("discard_detail", "trade_pending", "setup_choices"):
            continue
        else:
            print(
                k,
                f"n={len(v)} mean={st.mean(v):.2f} median={st.median(v)}" if v else "-",
            )
    print(
        "first city turn (rehex turns):",
        sorted(g["first_city_turn"] or 99 for g in per_game),
    )
    print(
        "settlements at first city:",
        collections.Counter(g["settle_at_first_city"] for g in per_game),
    )
    fit_setup(L["setup_choices"])
    if "--games" in sys.argv:
        for g in per_game:
            print(g)
        for d in L["discard_detail"]:
            print("discard", d)
        for d in L["trade_pending"][:40]:
            print("trade", d)


def fit_setup(choices):
    """Grid-search a linear opening score: sum pips*w[res] + new-type bonus + port bonus."""
    import itertools

    grid = [0.5, 1.0, 1.5, 2.0]
    best = []
    for ws in itertools.product(grid, repeat=5):
        w = dict(zip(RES, ws))
        for bonus in (0, 1, 2, 3):
            for div in (0, 1, 2):
                hits = {1: 0, 2: 0}
                for k, have, spots, ci in choices:

                    def score(f):
                        hexes, port = f
                        types = {r for r, _ in hexes}
                        return (
                            sum(p * w[r] for r, p in hexes)
                            + bonus * len(types - have) * (k == 2)
                            + div * len(types)
                        )

                    sc = [score(f) for f in spots]
                    hits[k] += sc[ci] == max(sc)
                best.append((hits[1] + hits[2], hits[1], hits[2], ws, bonus, div))
    best.sort(reverse=True)
    n1 = sum(1 for c in choices if c[0] == 1)
    print(
        f"setup fit (exact argmax, ties count as hits): n1={n1} n2={len(choices) - n1}"
    )
    print(
        "  pips only:",
        [b for b in best if b[3] == (1.0,) * 5 and b[4] == 0 and b[5] == 0][0][:3],
    )
    for b in best[:6]:
        print(
            "  ",
            b[:3],
            "weights",
            dict(zip(RES, b[3])),
            "new-type bonus",
            b[4],
            "diversity",
            b[5],
        )


def robber_choice(C, kind, h, prev, tiles, prod, rx, op, vp, hand):
    cands = [x for x in tiles if x != prev]

    # friendly robber: no hex next to a player with <= 2 VP
    def ok(x):
        for c in (rx, op):
            if vp(c) <= 2 and prod(c, x) > 0:
                return False
            # also unproductive buildings (desert): approximate by prod only
        return True

    cands = [x for x in cands if ok(x)] or cands
    opp_hit = {x: prod(op, x) for x in cands}
    own_hit = {x: prod(rx, x) for x in cands}
    best_opp = max(opp_hit.values())
    rank = 1 + sum(1 for x in cands if opp_hit[x] > opp_hit[h])
    C[f"robber_{kind}_rank_by_opp_pips"][min(rank, 5)] += 1
    C[f"robber_{kind}_hits_own"][own_hit.get(h, prod(rx, h)) > 0] += 1
    C[f"robber_{kind}_opp_on_hex"][opp_hit.get(h, prod(op, h)) > 0] += 1
    net = {x: opp_hit[x] - own_hit[x] for x in cands}
    rank_net = 1 + sum(1 for x in cands if net[x] > net.get(h, -99))
    C[f"robber_{kind}_rank_by_net"][min(rank_net, 5)] += 1
    if best_opp > 0 and opp_hit.get(h, 0) == best_opp:
        # among ties: does it pick the one without own buildings?
        ties = [x for x in cands if opp_hit[x] == best_opp]
        C[f"robber_{kind}_ties_at_best"][len(ties)] += 1
    best_net = max(net.values())
    ties = [x for x in cands if net[x] == best_net]
    if len(ties) > 1 and h in ties:
        idx = sorted(HEXES.index(x) for x in ties)
        C["robber_tie_pick_by_hexindex"][
            (
                "lowest"
                if HEXES.index(h) == idx[0]
                else "highest" if HEXES.index(h) == idx[-1] else "middle"
            )
        ] += 1
        top_pips = max(PIPS.get(tiles[x][1], 0) for x in ties)
        C["robber_tie_pick_highest_number_pips"][
            PIPS.get(tiles[h][1], 0) == top_pips
        ] += 1
        # resource the opponent holds least of / most of
        oh = hand[op]
        C["robber_tie_pick_res_opp_holds_most"][
            oh[tiles[h][0]] == max(oh[tiles[x][0]] for x in ties)
        ] += 1
    # resource on hex vs what opp lacks
    C[f"robber_{kind}_hex_resource"][tiles[h][0] or "desert"] += 1
    C[f"robber_{kind}_hex_number_pips"][PIPS.get(tiles[h][1], 0)] += 1


if __name__ == "__main__":
    main()
