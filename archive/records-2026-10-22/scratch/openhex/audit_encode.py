"""Audit item 4: the net's input, live against offline.

For N of the bots' own captures: feed the frames to a LiveGame the way the
bridge does and encode every position where the bot has a decision; then
convert the whole capture, replay it (the path human data and the records
take) and encode the same log position. Counts the positions and the
features that differ, by feature name. Also checks a few features against
colonist's own payload where it shows them (bank, dev cards left).
Usage: audit_encode.py [N]
"""

import collections
import json
import random
import sys

import numpy as np

from catanatron.colonist import convert as C
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.encode import (
    DICE_SIZE,
    DYNAMIC_SIZE,
    MY_EXTRA,
    NUM_EDGES,
    NUM_NODES,
    NUM_TILES,
    PLAYER_SIZE,
    encode,
)
from catanatron.colonist.live import LiveGame
from catanatron.colonist.replay import replay

N = int(sys.argv[1]) if len(sys.argv) > 1 else 50


def names():
    out = ["robber_tile"] * NUM_TILES
    out += ["buildings"] * (NUM_NODES * 4)
    out += ["roads"] * (NUM_EDGES * 2)
    base = "vp has_army has_road roads_left settl_left cities_left road_len has_rolled".split()
    block = base + ["hand_total", "dev_total"]
    block += [f"hand_{r}" for r in "wood brick sheep wheat ore".split()]
    block += [f"played_{c}" for c in "knight yop mono rb".split()]
    block += [f"prod_{i}" for i in range(5)] + [f"prod_robbed_{i}" for i in range(5)]
    block += [f"port_{i}" for i in range(6)]
    assert len(block) == PLAYER_SIZE, len(block)
    extra = ["actual_vp", "played_dev_this_turn"]
    extra += [f"dev_{c}" for c in "knight yop mono rb vp".split()]
    extra += [f"playable_{c}" for c in "knight yop mono rb".split()]
    assert len(extra) == MY_EXTRA
    out += ["me." + b for b in block] + ["me." + e for e in extra]
    out += ["them." + b for b in block]
    phase = "initial discarding my_discard moving_knight road_building free_roads my_turn num_turns dev_deck_left".split()
    out += ["phase." + p for p in phase] + [f"phase.bank_{i}" for i in range(5)]
    out += ["dice"] * DICE_SIZE
    assert len(out) == DYNAMIC_SIZE, (len(out), DYNAMIC_SIZE)
    return out


NAMES = names()

rows = [json.loads(l) for l in open("data/human/index.jsonl")]
rows = [r for r in rows if r["status"] == "ok"]
random.Random(11).shuffle(rows)

C_ = collections.Counter()
diff_feat = collections.Counter()
examples = collections.defaultdict(list)
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
        C_["no_bot_seat_or_error"] += 1
        continue
    game_id = (payload.get("gameSettings") or {}).get("id")
    seen = {}
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
                live.conv.reconcile(p["gameState"])
                live._rebuild()
        elif not live.apply(f):
            continue
        if live.error or live.over:
            break
        if live.bot_to_move and live.position_key != last_key:
            last_key = live.position_key
            _, dyn = encode(live.game, live.bot)
            seen[live.position_key] = dyn
    if live.error:
        C_["live_error"] += 1
        continue
    rec = C.convert(frames).record
    offline = {}

    class Obs:
        def before(self, game):
            pass

        def step(self, game, decision):
            i = len(game.state.action_records)
            if i in seen and i not in offline:
                offline[i] = encode(game, live.bot)[1]

        def after(self, game):
            i = len(game.state.action_records)
            if i in seen and i not in offline:
                offline[i] = encode(game, live.bot)[1]

    rep = replay(rec, name=path, observer=Obs())
    if not rep.ok:
        C_["replay_fail"] += 1
        continue
    games += 1
    for k, a in seen.items():
        C_["positions"] += 1
        b = offline.get(k)
        if b is None:
            C_["no_offline_match"] += 1
            continue
        d = np.nonzero(np.abs(a - b) > 1e-6)[0]
        if len(d) == 0:
            C_["identical"] += 1
            continue
        C_["differ"] += 1
        for name in sorted({NAMES[j] for j in d}):
            diff_feat[name] += 1
            if len(examples[name]) < 3:
                j = next(j for j in d if NAMES[j] == name)
                examples[name].append(
                    (path.split("/")[-2], k, float(a[j]), float(b[j]))
                )

print("games", games)
for k, v in sorted(C_.items()):
    print(f"{v:7d}  {k}")
print("features that differ (positions):")
for name, n in diff_feat.most_common():
    print(f"{n:7d}  {name}  e.g. {examples[name]}")
