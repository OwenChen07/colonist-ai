"""Road joins the bot passed up.

A "join" is a legal road for the bot that connects two of its separate road
networks and raises its longest-road length by 2 or more. At each bot
decision where one is legal and the bot did not build it, record the facts.

  road_join.py one CAPTURE [SIMS ...]   one game, with searches (r73, mix 0.5)
  road_join.py count N [JOBS]           last N ranked captures of bot3/4/5/7,
                                        no search; -> live-loss/road_join_count.json
Never prints names."""

import json
import os
import random
import sys
from multiprocessing import Pool

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.replay import replay
from catanatron.models.enums import ActionType

ACCOUNTS = ("bot3", "bot4", "bot5", "bot7")
NET_PATH = "runs/g1/best.npz"  # f240deb1..., round 73, the live net
HUMAN_PATH = "models/human/net.npz"
OUT = "data/orchestrator/live-loss/road_join_count.json"
_nets = {}


def nets():
    if not _nets:
        from catanatron.colonist.net import Net

        _nets["bot"] = Net.load(NET_PATH)
        _nets["human"] = Net.load(HUMAN_PATH)
    return _nets["bot"], _nets["human"]


def lr_after(board, color, edge):
    b = board.copy()
    before = len(b.connected_components[color])
    b.build_road(color, edge)
    return b.road_lengths[color], len(b.connected_components[color]) < before


def own_lr(board, color):
    paths = board.continuous_roads_by_player(color)
    return max((len(p) for p in paths), default=0)


class Obs:
    def __init__(self, me, search_sims=()):
        self.me = me
        self.sims = search_sims
        self.rows = []
        self.values = []  # (turn, net value for me) at every bot decision

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if decision.color.value != self.me or s.is_initial_build_phase:
            return
        acts = game.playable_actions
        color = decision.color
        if self.sims:
            bot, _ = nets()
            static, dyn = E.encode(game, color)
            logits, value = bot.forward(static, dyn[None, :])
            self.values.append((s.num_turns, float(np.ravel(value)[0])))
        roads = [a for a in acts if a.action_type == ActionType.BUILD_ROAD]
        if not roads:
            return
        board = s.board
        cur = own_lr(board, color)
        info = {}
        for a in roads:
            length, merged = lr_after(board, color, a.value)
            info[tuple(sorted(a.value))] = (length, merged)
        joins = {e: v for e, v in info.items() if v[1] and v[0] - cur >= 2}
        if not joins:
            return
        played = E.action_key(decision)
        took = played[0] == ActionType.BUILD_ROAD and played[1] in joins
        if took and self.sims:
            return
        idx = {c.value: i for c, i in s.color_to_index.items()}
        opp = next(c for c in s.color_to_index if c != color)
        best_join = max(v[0] for v in joins.values())
        row = {
            "turn": s.num_turns,
            "took": took,
            "prompt": str(s.current_prompt).split(".")[-1],
            "played": [
                played[0].value,
                list(played[1]) if isinstance(played[1], tuple) else played[1],
            ],
            "played_lr": info[played[1]][0] if played[1] in info else cur,
            "lr_now": cur,
            "best_join_lr": best_join,
            "joins": {str(e): v[0] for e, v in joins.items()},
            "opp_lr": own_lr(board, opp),
            "holder": (
                None
                if board.road_color is None
                else ("bot" if board.road_color == color else "opp")
            ),
            "vp_bot": s.player_state[f"P{idx[self.me]}_ACTUAL_VICTORY_POINTS"],
            "vp_opp": s.player_state[f"P{idx[opp.value]}_ACTUAL_VICTORY_POINTS"],
            "n_roads_legal": len(roads),
            "n_legal": len(acts),
        }
        if self.sims:
            row["search"] = self.search(game, color, acts, joins, played)
        self.rows.append(row)

    def search(self, game, color, acts, joins, played):
        from catanatron.colonist.mcts import Search

        bot, human = nets()
        out = {}
        for sims in self.sims:
            search = Search(
                bot,
                sims,
                rng=random.Random(7),
                noise=False,
                opponent_model=human,
                opponent_mix=0.5,
            )
            action, root = search.run(game)
            keys = [E.action_key(a) for a in root.actions]
            q = search.completed_q(root)
            order = np.argsort(-root.N)

            def desc(i):
                k = keys[i]
                return {
                    "move": f"{k[0].value} {k[1]}",
                    "prior": round(float(root.prior[i]), 4),
                    "N": int(root.N[i]),
                    "Q": round(float(q[i]), 4),
                }

            out[sims] = {
                "chosen": f"{E.action_key(action)[0].value} {E.action_key(action)[1]}",
                "root_value": round(float(root.value), 4),
                "top": [desc(i) for i in order[:6]],
                "joins": [
                    desc(keys.index((ActionType.BUILD_ROAD, e)))
                    for e in joins
                    if (ActionType.BUILD_ROAD, e) in keys
                ],
                "played": desc(keys.index(played)) if played in keys else None,
            }
        return out


def facts(path, sims=()):
    conv = convert(load_capture(path))
    record = conv.record
    you = [c for c, p in record["players"].items() if p["you"]]
    if len(you) != 1 or len(record["seats"]) != 2:
        return None
    obs = Obs(you[0], sims)
    rep = replay(record, observer=obs)
    if not rep.ok:
        return None
    return obs, record, rep


def one(path, sims):
    res = facts(path, sims)
    if res is None:
        print("replay failed")
        return
    obs, record, rep = res
    print(
        "bot",
        obs.me,
        "winner",
        record.get("winner"),
        "ended",
        "turns",
        rep.game.state.num_turns if hasattr(rep, "game") else "?",
    )
    print("values", [(t, round(v, 3)) for t, v in obs.values])
    for row in obs.rows:
        print(json.dumps(row, default=str))


def count_job(path):
    try:
        res = facts(path)
    except Exception as e:  # noqa: BLE001
        return {"capture": path, "error": type(e).__name__}
    if res is None:
        return {"capture": path, "error": "replay"}
    obs, record, _ = res
    return {
        "capture": path,
        "bot_won": record.get("winner") == obs.me,
        "rows": obs.rows,
    }


def ranked_captures(n):
    rows = []
    for acct in ACCOUNTS:
        with open(f"data/{acct}/games.jsonl") as f:
            for line in f:
                r = json.loads(line)
                if (
                    r.get("queue") == "ranked"
                    and r.get("capture")
                    and r.get("ended") in ("win", "resignation")
                ):
                    rows.append((r["at"], os.path.join("captures", acct, r["capture"])))
    rows.sort()
    return [p for _, p in rows[-n:] if os.path.exists(p)]


def join_gap(board, color):
    """Fewest free edges joining the bot's first two networks (BFS that avoids
    taken edges and enemy buildings), the path, and the longest road after it."""
    from collections import deque

    from catanatron.models.board import STATIC_GRAPH

    comps = board.connected_components[color]
    if len(comps) < 2:
        return None
    land = set(board.map.land_nodes)
    best = None
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            src, dst = comps[i], comps[j]
            prev = {n: None for n in src}
            q = deque(src)
            found = None
            while q and found is None:
                n = q.popleft()
                if n not in src and board.is_enemy_node(n, color):
                    continue
                for m in STATIC_GRAPH.neighbors(n):
                    if m in prev or m not in land or (n, m) in board.roads:
                        continue
                    prev[m] = n
                    if m in dst:
                        found = m
                        break
                    q.append(m)
            if found is None:
                continue
            path, n = [], found
            while prev[n] is not None:
                path.append((prev[n], n))
                n = prev[n]
            if board.is_enemy_node(found, color):
                continue
            b = board.copy()
            ok = True
            for e in reversed(path):
                try:
                    b.build_road(color, e)
                except ValueError:
                    ok = False
                    break
            lr = b.road_lengths[color] if ok else None
            cand = (len(path), path[::-1], lr)
            if best is None or cand[0] < best[0]:
                best = cand
    return best


class Roads:
    """Every bot decision with a legal road: networks, merges in 1 and 2 roads."""

    def __init__(self, me):
        self.me = me

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        s = game.state
        if decision.color.value != self.me or s.is_initial_build_phase:
            return
        color = decision.color
        board = s.board
        roads = [
            a.value
            for a in game.playable_actions
            if a.action_type == ActionType.BUILD_ROAD
        ]
        if not roads:
            return
        comps = board.continuous_roads_by_player(color)
        one, two = {}, {}
        for e in roads:
            length, merged = lr_after(board, color, e)
            one[tuple(sorted(e))] = (length, merged)
            if not merged:
                b = board.copy()
                b.build_road(color, e)
                n = len(b.connected_components[color])
                for e2 in b.buildable_edges(color):
                    b2 = b.copy()
                    b2.build_road(color, e2)
                    if len(b2.connected_components[color]) < n:
                        key = (tuple(sorted(e)), tuple(sorted(e2)))
                        two[key] = b2.road_lengths[color]
        opp = next(c for c in s.color_to_index if c != color)
        played = E.action_key(decision)
        gap = join_gap(board, color)
        print("   gap", gap)
        print(
            s.num_turns,
            str(s.current_prompt).split(".")[-1],
            "played",
            played[0].value,
            played[1],
            "| nets",
            sorted((len(p) for p in comps), reverse=True),
            "lr",
            own_lr(board, color),
            "opp",
            own_lr(board, opp),
            "holder",
            board.road_color and board.road_color.value,
            "| merge1",
            {e: v[0] for e, v in one.items() if v[1]},
            "| best1",
            max(v[0] for v in one.values()),
            "| merge2 best",
            max(two.values()) if two else None,
        )


class At:
    """Searches at the bot's road decisions on one turn; join-path roads shown."""

    def __init__(self, me, turn, sims):
        self.me, self.turn, self.sims = me, turn, sims

    def before(self, game):
        pass

    def after(self, game):
        pass

    def step(self, game, decision):
        from catanatron.colonist.mcts import Search

        s = game.state
        if decision.color.value != self.me or s.num_turns != self.turn:
            return
        if not any(
            a.action_type == ActionType.BUILD_ROAD for a in game.playable_actions
        ):
            return
        color = decision.color
        gap = join_gap(s.board, color)
        ends = {tuple(sorted(gap[1][0])), tuple(sorted(gap[1][-1]))} if gap else set()
        played = E.action_key(decision)
        print(
            f"turn {s.num_turns} played {played[1]} join ends {sorted(ends)} gap {gap}"
        )
        bot, human = nets()
        for sims in self.sims:
            search = Search(
                bot,
                sims,
                rng=random.Random(7),
                noise=False,
                opponent_model=human,
                opponent_mix=0.5,
            )
            action, root = search.run(game)
            keys = [E.action_key(a) for a in root.actions]
            q = search.completed_q(root)
            print(
                f"  sims {sims} chosen {E.action_key(action)} net value {root.value:+.3f}"
            )
            for i in np.argsort(-root.prior):
                k = keys[i]
                tag = (
                    " <join end"
                    if k[0] == ActionType.BUILD_ROAD and k[1] in ends
                    else ""
                )
                tag += " <played" if k == played else ""
                if root.N[i] or tag or root.prior[i] > 0.02:
                    after = ""
                    if k[0] == ActionType.BUILD_ROAD:
                        after = f" lr->{lr_after(s.board, color, k[1])[0]}"
                    print(
                        f"    {k[0].value} {k[1]}{after} prior {root.prior[i]:.3f} N {int(root.N[i])} Q {q[i]:+.3f}{tag}"
                    )


def main():
    if sys.argv[1] == "at":
        rec = convert(load_capture(sys.argv[2])).record
        me = next(c for c, p in rec["players"].items() if p["you"])
        replay(rec, observer=At(me, int(sys.argv[3]), [int(x) for x in sys.argv[4:]]))
        return
    if sys.argv[1] == "roads":
        rec = convert(load_capture(sys.argv[2])).record
        me = next(c for c, p in rec["players"].items() if p["you"])
        replay(rec, observer=Roads(me))
        return
    if sys.argv[1] == "one":
        one(sys.argv[2], [int(x) for x in sys.argv[3:]])
        return
    n = int(sys.argv[2])
    jobs = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    paths = ranked_captures(n)
    with Pool(jobs) as pool:
        res = pool.map(count_job, paths, chunksize=4)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(res, f, default=str)
    print("wrote", OUT, len(res))


if __name__ == "__main__":
    main()
