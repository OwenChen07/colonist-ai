"""Audit item 4b: our live position against colonist's own state, on what
the replay checks don't cover (bank, longest road length, development cards
left, robber tile, largest army). Follows each capture with a LiveGame and
keeps colonist's gameState up to date by merging the diffs; compares at every
position where the bot has a decision.
Usage: audit_state.py [N]
"""

import collections
import json
import random
import sys

from catanatron.colonist import convert as C
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.live import LiveGame
from catanatron.models.enums import RESOURCES
from catanatron.models.player import Color
from catanatron.state_functions import get_largest_army, get_longest_road_length

N = int(sys.argv[1]) if len(sys.argv) > 1 else 50


def merge(dst, src):
    for k, v in src.items():
        if isinstance(dst, list):
            i = int(k)
            while len(dst) <= i:
                dst.append(None)
            if isinstance(v, dict) and isinstance(dst[i], (dict, list)):
                merge(dst[i], v)
            else:
                dst[i] = v
            continue
        if isinstance(v, dict) and isinstance(dst.get(k), (dict, list)):
            merge(dst[k], v)
        else:
            dst[k] = v


rows = [json.loads(l) for l in open("data/human/index.jsonl")]
rows = [r for r in rows if r["status"] == "ok"]
random.Random(13).shuffle(rows)

K = collections.Counter()
ex = collections.defaultdict(list)


def note(name, path, at, ours, theirs):
    K[name] += 1
    if name == "road_len":
        K[
            f"road_len ours={ours} colonist={theirs} initial={live.game.state.is_initial_build_phase}"
        ] += 1
    if len(ex[name]) < 3:
        ex[name].append((path, at, ours, theirs))


games = 0
for r in rows:
    if games >= N:
        break
    path = f"captures/{r['account']}/{r['capture']}"
    frames = load_capture(path)
    start = C.game_start(frames)
    if start is None:
        continue
    payload = start.data["data"]["payload"]
    live = LiveGame(payload, start.t)
    if live.bot is None or live.error:
        continue
    game_id = (payload.get("gameSettings") or {}).get("id")
    gs = json.loads(json.dumps(payload["gameState"]))
    games += 1
    last_key = None
    for f in frames:
        if f.dir != "in" or f.seq < start.seq:
            continue
        if f.type == C.GAME_STATE and f.seq > start.seq:
            p = f.data["data"].get("payload")
            if (
                isinstance(p, dict)
                and "gameState" in p
                and (p.get("gameSettings") or {}).get("id") == game_id
            ):
                gs = json.loads(json.dumps(p["gameState"]))
                live.conv.reconcile(p["gameState"])
                live._rebuild()
        else:
            if f.type == C.DIFF:
                merge(gs, (f.data["data"].get("payload") or {}).get("diff", {}))
            if not live.apply(f):
                continue
        if live.error or live.over:
            break
        if not (live.bot_to_move and live.position_key != last_key):
            continue
        last_key = at = live.position_key
        s = live.game.state
        K["positions"] += 1
        bank = (gs.get("bankState") or {}).get("resourceCards") or {}
        theirs = [bank.get(str(i)) for i in sorted(C.RESOURCE, key=int)]
        names = [C.RESOURCE[i] for i in sorted(C.RESOURCE, key=int)]
        ours = [s.resource_freqdeck[RESOURCES.index(n)] for n in names]
        if ours != theirs:
            note("bank", path, at, ours, theirs)
        devs = (gs.get("mechanicDevelopmentCardsState") or {}).get(
            "bankDevelopmentCards", {}
        )
        cards = devs.get("cards")
        if cards is not None:
            n = (
                len([c for c in cards if c is not None])
                if isinstance(cards, list)
                else len(cards)
            )
            if n != len(s.development_listdeck):
                note("dev_left", path, at, len(s.development_listdeck), n)
        lr = gs.get("mechanicLongestRoadState") or {}
        for col, sim in live.conv.seat.items():
            want = (lr.get(str(col)) or {}).get("longestRoad")
            got = get_longest_road_length(s, Color[sim])
            if want is not None and want != got:
                note("road_len", path, at, got, want)
        rob = (gs.get("mechanicRobberState") or {}).get("locationTileIndex")
        ours_tile = live.tile_of_cube.get(s.board.robber_coordinate)
        if rob is not None and rob != ours_tile:
            note("robber", path, at, ours_tile, rob)
        la = gs.get("mechanicLargestArmyState") or {}
        holder = [
            live.conv.seat.get(int(c))
            for c, v in la.items()
            if isinstance(v, dict) and v.get("hasLargestArmy")
        ]
        army = get_largest_army(s)[0]
        if la and holder != ([army.value] if army else []):
            note("largest_army", path, at, army.value if army else None, holder)

print("games", games)
for k, v in sorted(K.items()):
    print(f"{v:7d}  {k}")
for k, e in ex.items():
    print(k, e)
