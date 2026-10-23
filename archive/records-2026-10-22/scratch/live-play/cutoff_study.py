"""E72: the "cut-off" second settlement (owner's question, Oct 20, 10:52 PM).

For every captured live game in data/human/index.jsonl (status ok), replays the
setup (first 8 log entries: seat 1 settles, seat 2 settles twice, seat 1 settles)
and, for each player's SECOND settlement node n placed by X against opponent Y:

  wb      n touches a wood hex and a brick hex.
  target  T = Y's best expansion spot: among free nodes legal for a settlement
          at graph distance 2 or 3 from Y's FIRST settlement, the one with the
          highest prior of round 152's net from Y's chair (a copy of the
          position just before X's placement, Y set to move in setup).
  cut     n == T, or n is on a shortest path from Y's first settlement to T,
          or n is next to T (so T is no longer legal).  'strict' drops the
          last case (the lead's literal definition).
  cutwb   cut and wb: the trick the owner describes.

For our own second settlement it also takes the playing net's prior (from the
capture's net label) over the legal spots: the mass on cut+wb spots, the best
such spot's rank, and whether the bot picked one.

Writes one JSON row per game to data/orchestrator/scratch/live-play/cutoff_rows.jsonl.

    .venv/bin/python data/orchestrator/scratch/live-play/cutoff_study.py [workers]
"""

import json
import os
import sys
from collections import deque
from multiprocessing import Pool

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.net import Net
from catanatron.colonist.replay import decision_of, game_for_record
from catanatron.models.enums import ActionPrompt, ActionRecord, ActionType
from catanatron.models.actions import generate_playable_actions
from catanatron.models.player import Color
from catanatron.serialization import _tupled, action_from_json

OUT = "data/orchestrator/scratch/live-play/cutoff_rows.jsonl"
RATINGS = "data/orchestrator/analysis/opponent_ratings.json"
NETS = {
    "r73": "runs/g1/nets/round_073.npz",
    "r150": "runs/g1/nets/round_150.npz",
    "r152": "runs/g1/nets/round_152.npz",
    "e54": "data/orchestrator/e54/run1/iter-30/net.npz",
    "people": "models/human/net.npz",
}
ADJ = {n: set() for n in range(E.NUM_NODES)}
for a, b in E.EDGES:
    ADJ[a].add(b)
    ADJ[b].add(a)


def net_key(label):
    s = (label or "").lower()
    if "e54" in s or "iter30" in s or "iter-30" in s:
        return "e54"
    if "people" in s or "human" in s or "opponent model" in s:
        return "people"
    for r in ("73", "150", "152"):
        if f"round {r}" in s or f"round_{r}" in s or s.endswith(f"r{r}"):
            return f"r{r}"
    return None


_nets = {}


def get_net(key):
    if key not in _nets:
        _nets[key] = Net.load(NETS[key]) if key and os.path.exists(NETS[key]) else None
    return _nets[key]


def dist_from(src):
    d = {src: 0}
    q = deque([src])
    while q:
        u = q.popleft()
        for v in ADJ[u]:
            if v not in d:
                d[v] = d[u] + 1
                q.append(v)
    return d


def settlement_priors(game, color, net):
    """{node: prior} for `color` placing a setup settlement here, from its chair."""
    g = game.copy()
    s = g.state
    i = s.color_to_index[color]
    s.current_player_index = i
    s.current_turn_index = i
    s.current_prompt = ActionPrompt.BUILD_INITIAL_SETTLEMENT
    s.is_initial_build_phase = True
    actions = [
        a
        for a in generate_playable_actions(s)
        if a.action_type == ActionType.BUILD_SETTLEMENT
    ]
    legal = [E.action_index(a) for a in actions]
    pri, _ = net.evaluate(g, color, legal)
    return {a.value: float(p) for a, p in zip(actions, pri)}


def wood_brick(game, node):
    res = {t.resource for t in game.state.board.map.adjacent_tiles[node]}
    return "WOOD" in res and "BRICK" in res


def cut_info(game, y_first, node_priors_y):
    """Y's target and the set of nodes that cut it off (all, strict)."""
    d0 = dist_from(y_first)
    cands = {n: p for n, p in node_priors_y.items() if d0.get(n) in (2, 3)}
    if not cands:
        return None, set(), set()
    t = max(cands, key=cands.get)
    dt = dist_from(t)
    path = {n for n in d0 if d0[n] + dt.get(n, 99) == d0[t] and n != y_first}
    strict = path | {t}
    return t, strict | ADJ[t], strict


def study(task):
    account, capture, won = task
    path = os.path.join("captures", account, capture)
    try:
        cap = load_capture(path)
        bot = {}
        raw = json.load(open(path))
        bot = raw.get("bot") or {}
        record = convert(cap).record
    except Exception as e:  # noqa: BLE001
        return {"capture": capture, "account": account, "error": repr(e)[:80]}
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return {"capture": capture, "account": account, "error": "seats"}
    you = you[0]
    seats = record["seats"]
    game = game_for_record(record)
    ref = get_net("r152")
    playing = net_key(bot.get("label"))
    pnet = get_net(playing) if playing else None
    firsts, out = {}, {}
    for payload, result in record["log"][:8]:
        action = action_from_json(payload)
        decision = decision_of(action, game.playable_actions)
        if decision.action_type == ActionType.BUILD_SETTLEMENT:
            x = decision.color.value
            if x not in firsts:
                firsts[x] = decision.value
            else:
                y = next(c for c in seats if c != x)
                n = decision.value
                ypri = settlement_priors(game, Color[y], ref)
                t, cut, strict = cut_info(game, firsts[y], ypri)
                legal = [
                    a.value
                    for a in game.playable_actions
                    if a.action_type == ActionType.BUILD_SETTLEMENT
                ]
                row = {
                    "seat": seats.index(x) + 1,
                    "wb": wood_brick(game, n),
                    "cut": n in cut,
                    "strict": n in strict,
                    "target": t,
                    "avail": [m for m in legal if m in cut and wood_brick(game, m)],
                    "avail_strict": [
                        m for m in legal if m in strict and wood_brick(game, m)
                    ],
                }
                if x == you:
                    for key, net in (("ref", ref), ("play", pnet)):
                        if net is None:
                            continue
                        xp = settlement_priors(game, Color[x], net)
                        order = sorted(xp, key=xp.get, reverse=True)
                        row[f"{key}_mass"] = sum(xp.get(m, 0) for m in row["avail"])
                        row[f"{key}_best_rank"] = min(
                            (order.index(m) + 1 for m in row["avail"] if m in xp),
                            default=None,
                        )
                        row[f"{key}_chosen_rank"] = (
                            order.index(n) + 1 if n in xp else None
                        )
                        row[f"{key}_top"] = order[0]
                out["bot" if x == you else "opp"] = row
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
        "label": bot.get("label"),
        "net": playing,
        **out,
    }


def main():
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    rows = {}
    for line in open("data/human/index.jsonl"):
        if line.strip():
            r = json.loads(line)
            if r.get("status") == "ok" and r.get("bot_won") is not None:
                rows[(r["account"], r["capture"])] = r["bot_won"]
    tasks = [(a, c, w) for (a, c), w in sorted(rows.items(), key=lambda kv: kv[0][1])]
    if len(sys.argv) > 2:  # a quick test on the latest games
        tasks = tasks[-int(sys.argv[2]) :]
    os.nice(15)
    with open("data/orchestrator/jobs/cutoff_study.pid", "w") as f:
        f.write(str(os.getpid()))
    with Pool(workers) as pool, open(OUT, "w") as out:
        for i, row in enumerate(pool.imap(study, tasks, chunksize=8)):
            out.write(json.dumps(row) + "\n")
            if i % 500 == 0:
                print(i, len(tasks), flush=True)
    print("done", len(tasks))


if __name__ == "__main__":
    main()
