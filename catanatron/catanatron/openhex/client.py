"""Play OpenHex (https://open-hex.web.app) 1v1 games with the colonist net.

    python -m catanatron.openhex.client runs/g1/nets/round_066.npz --games 20

Joins the 1v1 queue, plays each game it is matched into, and rejoins until
--games are done. The position is followed from the game's events
(follow.Follower) and every decision is the live bot's search: the same net,
2400 simulations, capped by the time left on OpenHex's 60 s move clock. If the
simulator can't follow the game, the client still moves before the clock runs
out (a missed deadline aborts the game), with a plain legal-looking move.

Each game is saved to data/openhex/games/<id>.json: the board, the events, our
moves and the result. The API key is read from data/openhex/bot.json and never
printed.
"""

import os

# One core: numpy's BLAS must not spread over the machine.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

import argparse
import json
import random
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from catanatron.colonist import encode as E
from catanatron.models.enums import ActionType
from catanatron.openhex.board import HEXES, Board, as_hex, xyz
from catanatron.openhex.follow import (
    Follower,
    OutOfSync,
    color,
    openhex_color,
    resources,
    to_openhex,
)

A = ActionType
API = "https://open-hex.web.app/api"
TORONTO = ZoneInfo("America/Toronto")
KEY_FILE = "data/openhex/bot.json"
GAMES_DIR = "data/openhex/games"
MOVE_CLOCK = 60.0  # seconds per action; missing it aborts the game
RESERVE = 8.0  # seconds kept back for the network and a fallback move
# prompt: (share of the time left, cap in seconds, simulations as a multiple
# of --simulations), as the colonist bridge's budgets.
BUDGETS = {
    "BUILD_INITIAL_SETTLEMENT": (0.5, 20.0, 25 / 3),
    "BUILD_INITIAL_ROAD": (0.4, 5.0, 5 / 3),
    "ROLL": (0.33, 1.0, 1 / 3),
    "PLAY_TURN": (0.3, 5.0, 2.0),
    "MOVE_ROBBER": (0.4, 5.0, 2.0),
    "DISCARD": (0.4, 5.0, 2.0),
}


def think_budget(game, remaining, simulations):
    """(seconds, simulation cap) for the decision at `game`."""
    from catanatron.state_functions import player_has_rolled

    state = game.state
    prompt = state.current_prompt.value
    if (
        prompt == "PLAY_TURN"
        and not state.is_road_building
        and not player_has_rolled(state, state.current_color())
    ):
        prompt = "ROLL"
    share, cap, scale = BUDGETS.get(prompt, BUDGETS["PLAY_TURN"])
    seconds = max(0.05, min(cap, share * (remaining - RESERVE)))
    return seconds, max(1, round(simulations * scale))


def stamp():
    return datetime.now(TORONTO).strftime("%b %d %I:%M:%S %p")


def say(*parts):
    print(stamp(), *parts, flush=True)


class Rejected(Exception):
    """OpenHex refused an action (4xx, or a 200 that says it failed)."""


def refused(reply):
    """A 2xx action reply that still reports a failure."""
    if not isinstance(reply, dict):
        return False
    return (
        reply.get("success") is False
        or reply.get("ok") is False
        or reply.get("valid") is False
        or bool(reply.get("error"))
    )


def pending_of(state):
    """What OpenHex waits for, as {type: discard | steal | moveRobber |
    roadBuilding, ...}. The live state says pendingAction (type
    discardResources, players {color: n}); the guide says pending."""
    raw = (state or {}).get("pendingAction") or (state or {}).get("pending") or {}
    t = str(raw.get("type", "")).lower()
    for word, kind in (
        ("discard", "discard"),
        ("steal", "steal"),
        ("robber", "moveRobber"),
        ("road", "roadBuilding"),
    ):
        if word in t:
            return dict(raw, type=kind)
    return dict(raw)


def steal(move):
    """The steal that follows a robber move with a victim. OpenHex's state
    didn't show it pending in game 1, so it is sent unasked (a refusal is
    harmless)."""
    if move.action_type != A.MOVE_ROBBER or move.value[1] is None:
        return []
    return [{"type": "stealFromPlayer", "target": openhex_color(move.value[1])}]


def short(reply, n=300):
    """A reply cut for the game file, without the bulky state."""
    if isinstance(reply, dict):
        reply = {k: v for k, v in reply.items() if k != "state"}
    return json.dumps(reply)[:n]


class Api:
    def __init__(self, key, base=API):
        self.key = key
        self.base = base

    def call(self, method, path, body=None, tries=4):
        data = None if body is None else json.dumps(body).encode()
        for attempt in range(tries):
            req = urllib.request.Request(
                self.base + path,
                data=data,
                method=method,
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                    "User-Agent": "colonist-ai-r66",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    text = r.read().decode()
                    return json.loads(text) if text else {}
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:300]
                if 400 <= e.code < 500 and e.code != 429:
                    raise Rejected(f"{e.code} {detail}") from None
                say(f"{method} {path}: {e.code} {detail}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                say(f"{method} {path}: {e}")
            time.sleep(1 + 2 * attempt)
        raise ConnectionError(f"{method} {path} failed {tries} times")


class Player:
    """One OpenHex game."""

    def __init__(self, api, gid, net, simulations, rng):
        self.api = api
        self.gid = gid
        self.net = net
        self.simulations = simulations
        self.rng = rng
        self.follower = None
        self.me = None
        self.record = {"id": gid, "events": [], "moves": [], "notes": []}
        self.broken = False  # the simulator lost the game: fallback moves only
        self.mismatch = 0  # polls where OpenHex says we move, the simulator not
        self.pending_seen = set()
        self.reply_seen = set()
        self.stole = False  # the last action's reply carried a steal
        self.dumped = False  # the raw state was saved after a stuck fallback
        self.tried = (None, [])  # (event seq, fallback actions sent at it)

    # ----- logging -----
    def note(self, text):
        say(f"[{self.gid[:8]}] {text}")
        self.record["notes"].append([stamp(), text])

    def save(self):
        os.makedirs(GAMES_DIR, exist_ok=True)
        path = os.path.join(GAMES_DIR, f"{self.gid}.json")
        with open(path + ".tmp", "w") as f:
            json.dump(self.record, f)
        os.replace(path + ".tmp", path)

    # ----- the loop -----
    def play(self):
        try:
            return self._play()
        finally:  # a stop or crash keeps the game file up to date
            if self.follower is not None:
                self.save()

    def _play(self):
        last_save = time.monotonic()
        while True:
            reply = self.api.call("GET", f"/games/{self.gid}/state")
            info, state = reply["game"], reply["state"]
            self.me = reply.get("your_color", self.me)
            if self.follower is None:
                self.start(info, state)
            self.poll_events()
            if info["status"] != "active":
                self.record["game"] = info
                self.record["final_state"] = state
                self.note(f"over: {info['status']}, winner {info.get('winner')}")
                self.save()
                return info
            raw = state.get("pendingAction") or state.get("pending")
            if raw and raw.get("type") not in self.pending_seen:
                self.pending_seen.add(raw.get("type"))
                self.record["notes"].append([stamp(), "pending", raw])
            if self.must_act(info, state):
                self.act(info, state)
            if time.monotonic() - last_save > 30:
                self.save()
                last_save = time.monotonic()
            time.sleep(1.0)

    def start(self, info, state):
        board = state.get("board") or {}
        if board.get("tiles"):
            self.record.update(
                game=info,
                me=self.me,
                tiles=board["tiles"],
                ports=board.get("ports", []),
                turn_order=state["turnOrder"],
                robber=board.get("robberHex", state.get("robberHex")),
                config=state.get("config"),
            )
        else:  # back after a crash: the tiles were saved at first sight
            path = os.path.join(GAMES_DIR, f"{self.gid}.json")
            if not os.path.exists(path):
                raise RuntimeError("joined after setup: the board's tiles are gone")
            with open(path) as f:
                saved = json.load(f)
            for k in ("game", "me", "tiles", "ports", "turn_order", "robber"):
                self.record[k] = saved[k]
            self.record["notes"] = saved.get("notes", []) + [[stamp(), "resumed"]]
        b = Board(self.record["tiles"], self.record["ports"])
        order = self.record["turn_order"]
        self.follower = Follower(b, order, self.me, self.record["robber"])
        others = [p.get("name") for p in info.get("players", [])]
        self.note(f"start: we are {self.me}, order {order}, {others}")
        self.save()

    def poll_events(self):
        f = self.follower
        reply = self.api.call("GET", f"/games/{self.gid}/events?after={f.seq}")
        events = [e for e in reply.get("events", []) if e["seq"] > f.seq]
        self.record["events"].extend(events)
        if self.broken:
            f.seq = max([f.seq] + [e["seq"] for e in events])
            return
        known = set(f.unknown)
        try:
            f.feed(events)
        except (OutOfSync, KeyError, ValueError) as e:
            self.lost_sync(e)
        for t in sorted(f.unknown - known):
            self.note(f"skipped an unknown event type: {t}")

    def lost_sync(self, why):
        self.broken = True
        self.note(f"OUT OF SYNC, fallback moves from here: {why!r}")
        if isinstance(why, Exception):
            self.record["notes"].append([stamp(), traceback.format_exc()])

    def must_act(self, info, state):
        pending = pending_of(state)
        if pending.get("type") == "discard":
            return self.discard_count(state) > 0
        return info.get("current_player_color") == self.me

    def discard_count(self, state):
        """Cards we still owe for a 7 (0 if none), from OpenHex's state."""
        pending = pending_of(state)
        for key in ("players", "remaining", "discards", "amounts", "counts"):
            v = pending.get(key)
            if isinstance(v, dict) and self.me in v:
                return int(v[self.me] or 0)
            if isinstance(v, list) and self.me in v:
                return self.hand_size(state) // 2
        if pending.get("player") == self.me or pending.get("color") == self.me:
            return int(pending.get("count") or self.hand_size(state) // 2)
        return 0

    def my_state(self, state):
        return next(p for p in state.get("players", []) if p.get("color") == self.me)

    def hand_size(self, state):
        return sum(self.my_state(state).get("hand", {}).values())

    def remaining(self, info):
        deadline = info.get("action_deadline")
        if not deadline:
            return MOVE_CLOCK
        try:
            end = datetime.fromisoformat(deadline.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return MOVE_CLOCK
        return max(0.0, min(MOVE_CLOCK, end - time.time()))

    # ----- deciding -----
    def act(self, info, state):
        started = time.monotonic()
        clock = self.remaining(info)
        pending = pending_of(state)
        if pending.get("type") == "steal":
            # In 1v1 the only one to steal from is the opponent.
            return self.send({"type": "stealFromPlayer", "target": self.opponent()})
        if not self.broken:
            try:
                self.follower.settle()
                game = self.follower.replay()
                if game.state.current_color() != color(self.me):
                    self.mismatch += 1
                    if self.mismatch < 4:
                        return  # the events may lag the state: poll again
                    raise OutOfSync("OpenHex says we move, the simulator doesn't")
                self.mismatch = 0
                actions = self.decide(game, clock - (time.monotonic() - started))
            except Exception as e:  # noqa: BLE001 - any failure: fall back
                self.lost_sync(e)
            else:
                return self.send_all(actions, started)
        return self.fallback(state)

    def search(self, game, remaining):
        from catanatron.colonist.mcts import Search

        if len(game.playable_actions) == 1:
            return game.playable_actions[0], 1
        seconds, simulations = think_budget(game, remaining, self.simulations)
        search = Search(self.net, simulations, rng=self.rng, noise=False)
        try:
            action, _ = search.run(game, time_limit=seconds)
        except ValueError:
            # The search redeals the opponent's hidden cards; a deal that gives
            # them 15 points leaves it no choice to make. Take the net's move.
            actions = game.playable_actions
            priors, _ = self.net.evaluate(
                game, game.state.current_color(), E.legal_indices(actions)
            )
            return actions[int(priors.argmax())], 0
        key = E.action_key(action)
        move = next(a for a in game.playable_actions if E.action_key(a) == key)
        return move, search.used

    def decide(self, game, clock):
        """Our OpenHex actions for this decision, in order."""
        t0 = time.monotonic()
        left = lambda: clock - (time.monotonic() - t0)
        me = color(self.me)
        b = self.follower.board
        s = game.state
        if s.is_discarding:
            g = game.copy()
            cards, n = [], s.discard_counts[s.color_to_index[me]]
            while g.state.is_discarding and g.state.current_color() == me:
                move, _ = self.search(g, left() / max(1, n - len(cards)) + RESERVE)
                cards.append(move.value)
                g.execute(move, validate_action=False)
            self.moves.append(("discard", [str(c) for c in cards]))
            return [{"type": "discardResources", "resources": resources(cards)}]
        move, used = self.search(game, left())
        self.moves.append((str(move), used))
        if move.action_type == A.PLAY_KNIGHT_CARD:
            g = game.copy()
            g.execute(move, validate_action=False)
            robber, used = self.search(g, left())
            self.moves.append((str(robber), used))
            return [{"type": "playKnight", "hex": xyz(robber.value[0])}] + steal(robber)
        return [to_openhex(b, game, move)] + steal(move)

    @property
    def moves(self):
        return self.record["moves"]

    def opponent(self):
        return next(c for c in self.record["turn_order"] if c != self.me)

    # ----- sending -----
    def send(self, action, started=None):
        body = {"action": action}
        if started is not None:
            body["duration_ms"] = int((time.monotonic() - started) * 1000)
        reply = self.api.call("POST", f"/games/{self.gid}/actions", body)
        if action["type"] not in self.reply_seen:  # the guide doesn't show replies
            self.reply_seen.add(action["type"])
            self.record["notes"].append([stamp(), "reply", action, short(reply)])
        if refused(reply):
            raise Rejected(f"200 {short(reply)}")
        self.after_send(reply)
        return reply

    def after_send(self, reply):
        """A robber move or knight that leaves a steal pending: steal now."""
        reply = reply if isinstance(reply, dict) else {}
        events = [e.get("event", {}) for e in reply.get("events") or []]
        self.stole = any(e.get("type") == "resourceStolen" for e in events)
        state = reply.get("state") or {}
        pending = pending_of(state)
        if pending.get("type") == "steal":
            self.send({"type": "stealFromPlayer", "target": self.opponent()})
            self.stole = True

    def send_all(self, actions, started):
        for action in actions:
            if action["type"] == "stealFromPlayer" and self.stole:
                continue  # the robber move's reply already stole
            try:
                self.send(action, started)
            except Rejected as e:
                if action["type"] == "stealFromPlayer":
                    self.note(f"steal refused (none was due?): {e}")
                    continue
                self.lost_sync(f"{action} rejected: {e}")
                state = self.api.call("GET", f"/games/{self.gid}/state")["state"]
                return self.fallback(state)

    def fallback(self, state):
        """A move OpenHex should accept, without the simulator: try candidates
        until one is taken."""
        pending = pending_of(state)
        kind = pending.get("type")
        me = self.my_state(state)
        robber = state.get("board", {}).get("robberHex", state.get("robberHex"))
        if kind == "discard":
            hand = dict(me.get("hand", {}))
            n = self.discard_count(state) or sum(hand.values()) // 2
            give = {}
            for _ in range(n):
                r = max(hand, key=hand.get)
                hand[r] -= 1
                give[r] = give.get(r, 0) + 1
            options = [{"type": "discardResources", "resources": give}]
        elif kind == "steal":
            options = [{"type": "stealFromPlayer", "target": self.opponent()}]
        elif kind == "moveRobber":
            robber = as_hex(robber) if robber is not None else None
            hexes = [h for h in HEXES if h != robber]
            self.rng.shuffle(hexes)
            options = [{"type": "moveRobber", "hex": xyz(h)} for h in hexes]
        elif kind == "roadBuilding":
            options = [{"type": "buildRoad", "edge": e} for e in range(72)]
        elif state.get("isSetupPhase"):
            options = [
                {"type": "placeInitialSettlement", "vertex": v} for v in range(54)
            ]
            options += [{"type": "placeInitialRoad", "edge": e} for e in range(72)]
        else:
            # A steal OpenHex may want without showing it pending.
            options = [
                {"type": "rollDice"},
                {"type": "stealFromPlayer", "target": self.opponent()},
                {"type": "endTurn"},
            ]
        # An action taken without anything happening (no new event) was not
        # really taken: don't send it again, try the next one.
        seq = self.follower.seq
        if self.tried[0] != seq:
            self.tried = (seq, [])
        options = [a for a in options if a not in self.tried[1]]
        for action in options:
            self.tried[1].append(action)
            try:
                self.send(action)
                self.moves.append(("fallback", action))
                return
            except Rejected:
                continue
        self.note(f"no fallback move was accepted (pending {pending})")
        if not self.dumped:  # what is OpenHex waiting for? keep its state
            self.dumped = True
            raw = {k: v for k, v in state.items() if k != "board"}
            self.record["notes"].append([stamp(), "stuck state", raw])
            self.save()


def load_key():
    with open(KEY_FILE) as f:
        return json.load(f)["api_key"]


def active_games(api):
    reply = api.call("GET", "/games?status=active")
    if isinstance(reply, dict):
        reply = reply.get("games", [])
    return [g for g in reply if g.get("status", "active") == "active"]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("net")
    parser.add_argument("--games", type=int, default=1)
    parser.add_argument("--simulations", type=int, default=2400)
    parser.add_argument("--core", type=int, default=None, help="pin to one CPU")
    args = parser.parse_args()
    if args.core is not None:
        os.sched_setaffinity(0, {args.core})
    from catanatron.colonist.net import Net

    net = Net.load(args.net)
    api = Api(load_key())
    rng = random.Random()
    results = []
    joined = False
    try:
        while len(results) < args.games:
            games = active_games(api)
            if games:
                gid = games[0]["id"]
                joined = False
                player = Player(api, gid, net, args.simulations, rng)
                try:
                    info = player.play()
                except Exception as e:  # noqa: BLE001 - keep the session going
                    say(f"[{gid[:8]}] game loop failed: {e!r}")
                    if player.follower is not None:
                        player.save()
                    time.sleep(10)
                    continue
                me = player.me
                winner = info.get("winner")  # {botId, color, name, seat}
                if isinstance(winner, dict):
                    winner = winner.get("color")
                results.append((gid, info.get("status"), winner, me))
                wins = sum(1 for _, _, w, c in results if w and w == c)
                say(f"games {len(results)}/{args.games}, wins {wins}")
                continue
            if not joined:
                try:
                    api.call("POST", "/queue/join", {"mode": "1v1"})
                except Rejected as e:
                    say(f"queue join: {e}")
                joined = True
                say("in the 1v1 queue")
            time.sleep(5)
    finally:
        if joined:
            try:
                api.call("POST", "/queue/leave", {"mode": "1v1"}, tries=1)
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    main()
