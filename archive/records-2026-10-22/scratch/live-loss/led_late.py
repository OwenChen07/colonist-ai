"""Games where the bot led late (net value > THRESH at a bot decision after
turn TURN) and how they ended. Value from runs/g1/best.npz (current best),
not the net that played. Usage: led_late.py [last N ranked games per account]"""

import json
import math
import sys
from multiprocessing import Pool

from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.net import Net
from catanatron.colonist.replay import replay
from catanatron.models.player import Color

NET = "runs/g1/best.npz"
TURN, THRESH = 75, 0.5
ACCOUNTS = ("bot3", "bot4", "bot5", "bot7")
_net = None


def wilson(k, n, z=1.96):
    if n == 0:
        return "n=0"
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{100 * p:.1f}% [{100 * (c - h):.1f}, {100 * (c + h):.1f}] ({k}/{n})"


def snap(state, color):
    i = state.color_to_index[color]
    p = state.player_state
    return {
        "vp": p[f"P{i}_ACTUAL_VICTORY_POINTS"],
        "road": bool(p[f"P{i}_HAS_ROAD"]),
        "army": bool(p[f"P{i}_HAS_ARMY"]),
        "cities": 4 - p[f"P{i}_CITIES_AVAILABLE"],
        "setts": 5 - p[f"P{i}_SETTLEMENTS_AVAILABLE"],
        "knights": p[f"P{i}_PLAYED_KNIGHT"],
        "vpcards": p[f"P{i}_VICTORY_POINT_IN_HAND"],
        "road_len": p[f"P{i}_LONGEST_ROAD_LENGTH"],
    }


class Obs:
    def __init__(self, me, them):
        self.me, self.them = me, them
        self.lead = None  # snapshot at first v > THRESH after TURN
        self.maxv = -2.0
        self.final = None

    def before(self, game):
        pass

    def step(self, game, decision):
        if decision.color != self.me or game.state.num_turns < TURN:
            return
        _, v = _net.evaluate(game, self.me)
        self.maxv = max(self.maxv, v)
        if v > THRESH and self.lead is None:
            s = game.state
            self.lead = {
                "turn": s.num_turns,
                "v": v,
                "me": snap(s, self.me),
                "them": snap(s, self.them),
            }

    def after(self, game):
        s = game.state
        self.final = {
            "turn": s.num_turns,
            "me": snap(s, self.me),
            "them": snap(s, self.them),
        }


def scan(path):
    global _net
    if _net is None:
        _net = Net.load(NET)
    try:
        conv = convert(load_capture(path))
        if conv.unsupported or conv.ended not in ("win", "resignation"):
            return None
        rec = conv.record
        you = [c for c, p in rec["players"].items() if p["you"]]
        if len(you) != 1 or len(rec["seats"]) != 2:
            return None
        them = next(c for c in rec["seats"] if c != you[0])
        obs = Obs(Color[you[0]], Color[them])
        rep = replay(rec, observer=obs)
        if not rep.ok:
            return None
        return {
            "path": path,
            "won": rep.winner == you[0],
            "lead": obs.lead,
            "maxv": obs.maxv,
            "final": obs.final,
        }
    except Exception as e:  # noqa: BLE001
        return {"path": path, "error": repr(e)[:200]}


def main():
    last = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    paths = []
    for acc in ACCOUNTS:
        rows = [json.loads(x) for x in open(f"data/{acc}/games.jsonl")]
        rows = [r for r in rows if r.get("queue") == "ranked"][-last:]
        paths += [f"captures/{acc}/{r['capture']}" for r in rows]
    with Pool(6) as pool:
        res = [r for r in pool.imap_unordered(scan, paths, chunksize=4) if r]
    errs = [r for r in res if "error" in r]
    res = [r for r in res if "error" not in r]
    json.dump(res, open("data/orchestrator/scratch/live-loss/led_late.json", "w"))
    print(f"paths {len(paths)} replayed {len(res)} errors {len(errs)}")
    if errs:
        print("first error", errs[0])
    reached = [r for r in res if r["final"]["turn"] >= TURN]
    led = [r for r in reached if r["lead"]]
    print(
        f"games reaching turn {TURN}: {len(reached)}, bot wins {wilson(sum(r['won'] for r in reached), len(reached))}"
    )
    print(
        f"bot led (v>{THRESH}) after turn {TURN}: {len(led)}, bot wins {wilson(sum(r['won'] for r in led), len(led))}"
    )
    never = [r for r in reached if not r["lead"]]
    print(
        f"never led after turn {TURN}: bot wins {wilson(sum(r['won'] for r in never), len(never))}"
    )
    blown = [r for r in led if not r["won"]]
    print(f"blown leads: {len(blown)}")
    if not blown:
        return
    n = len(blown)

    def cnt(f):
        return wilson(sum(1 for r in blown if f(r)), n)

    L = lambda r: r["lead"]  # noqa: E731
    F = lambda r: r["final"]  # noqa: E731
    print(" at the lead moment:")
    print(
        "  mean v %.2f, mean turn %.0f, mean VP bot %.1f vs person %.1f"
        % (
            sum(L(r)["v"] for r in blown) / n,
            sum(L(r)["turn"] for r in blown) / n,
            sum(L(r)["me"]["vp"] for r in blown) / n,
            sum(L(r)["them"]["vp"] for r in blown) / n,
        )
    )
    print(
        "  bot holds road",
        cnt(lambda r: L(r)["me"]["road"]),
        "| person holds road",
        cnt(lambda r: L(r)["them"]["road"]),
    )
    print(
        "  bot holds army",
        cnt(lambda r: L(r)["me"]["army"]),
        "| person holds army",
        cnt(lambda r: L(r)["them"]["army"]),
    )
    print(" at the end (person won):")
    print(
        "  turns from lead to end, mean %.0f"
        % (sum(F(r)["turn"] - L(r)["turn"] for r in blown) / n)
    )
    print(
        "  person took road from bot",
        cnt(lambda r: L(r)["me"]["road"] and F(r)["them"]["road"]),
    )
    print(
        "  person gained road (bot didn't hold)",
        cnt(
            lambda r: not L(r)["them"]["road"]
            and not L(r)["me"]["road"]
            and F(r)["them"]["road"]
        ),
    )
    print(
        "  person took army from bot",
        cnt(lambda r: L(r)["me"]["army"] and F(r)["them"]["army"]),
    )
    print(
        "  person gained army (none held)",
        cnt(
            lambda r: not L(r)["them"]["army"]
            and not L(r)["me"]["army"]
            and F(r)["them"]["army"]
        ),
    )
    print(
        "  any bonus swing to person",
        cnt(
            lambda r: (F(r)["them"]["road"] and not L(r)["them"]["road"])
            or (F(r)["them"]["army"] and not L(r)["them"]["army"])
        ),
    )
    print("  person ends with VP card(s)", cnt(lambda r: F(r)["them"]["vpcards"] > 0))
    for key in ("cities", "setts", "vpcards", "vp"):
        gb = sum(F(r)["me"][key] - L(r)["me"][key] for r in blown) / n
        gp = sum(F(r)["them"][key] - L(r)["them"][key] for r in blown) / n
        print(f"  mean {key} gained after lead: bot {gb:+.2f}, person {gp:+.2f}")
    # comparison: held leads
    kept = [r for r in led if r["won"]]
    k = len(kept)
    if k:
        print(
            " held leads for comparison: n=%d, person took road from bot %s, person took army from bot %s"
            % (
                k,
                wilson(
                    sum(1 for r in kept if L(r)["me"]["road"] and F(r)["them"]["road"]),
                    k,
                ),
                wilson(
                    sum(1 for r in kept if L(r)["me"]["army"] and F(r)["them"]["army"]),
                    k,
                ),
            )
        )
        print(
            "  mean turns lead to end %.0f; bot VP at lead %.1f vs person %.1f"
            % (
                sum(F(r)["turn"] - L(r)["turn"] for r in kept) / k,
                sum(L(r)["me"]["vp"] for r in kept) / k,
                sum(L(r)["them"]["vp"] for r in kept) / k,
            )
        )


if __name__ == "__main__":
    main()
