"""The bridge between colonist in the browser and the AI (PLAN.md, Phase 4).

    python -m catanatron.colonist.bridge models/ab2/net.npz --simulations 200
    python -m catanatron.colonist.bridge models/ab2/net.npz --play     # plays its moves

Runs a small server on this machine (ws://127.0.0.1:8765) that the bot
extension (bot/) connects to. The extension forwards every message colonist
sends; the bridge follows the game with a LiveGame and, whenever the bot's
account has a decision to make, runs the network and search and sends back:

  - the move, in colonist's terms (corner/edge/tile indexes, resource ids),
    for the extension to show and, once clicking is built, to play;
  - the top few moves with their share of the search, and the AI's estimate
    of its chance to win, for the overlay.

With --play, it also sends each move: the same message colonist's own client
sends (client.py; colonist allows bots), through the extension, on the game's
socket. Then it checks the result: the next update must show exactly that move
by the bot, within --move-timeout seconds, or it stops for that game. Moves
whose message format isn't known yet are only suggested.

If the position ever stops matching colonist's (see live.py), the bridge
reports it and suggests nothing more for that game.

With --save DIR (default captures/, which git ignores), every game it follows
is saved as a capture, the recorder's format, whenever the bot stops, the
game ends or the tab disconnects: the file to look at when something goes
wrong. Room-join messages lose their reconnect token first; nothing else is
changed, so the file holds usernames and chat and stays on this machine.

Messages are JSON. From the extension:
  {"kind": "frame", "dir": "in"|"out", "b64": "<MessagePack bytes>", "t": ms,
   "socket": id, "replay": true if resent after a reconnect}
  {"kind": "reset"}                       (a new page: forget the game)
  {"kind": "replay-done"}                 (the resent game is complete)
To the extension:
  {"kind": "status", "state": "waiting"|"following"|"thinking"|"stopped", "text": ...}
  {"kind": "move", "position": n, "move": {...}, "plan": [...], "options": [...],
   "win_chance": p, "ms": t, "played": bool}
  {"kind": "send", "socket": id, "b64": "<MessagePack bytes>"}   (--play)
"""

import argparse
import asyncio
import base64
import json
import os
import random
import sys
import time

import msgpack

import numpy as np

from catanatron.colonist import convert as C
from catanatron.colonist import encode as E
from catanatron.colonist.capture import FORMAT, decode
from catanatron.colonist.live import LiveGame
from catanatron.models.enums import ActionType
from catanatron.serialization import action_from_json

HOST, PORT = "127.0.0.1", 8765
ROOM_JOINED = 1  # message type when joining a game room: carries its serverId


def game_phase(game):
    """The phase colonist's client messages depend on (see client.py)."""
    if game.state.is_initial_build_phase:
        return "setup"
    if game.state.is_road_building:
        return "road_building"
    return "main"


class Bridge:
    """The bridge's logic, without the network: feed it messages, get back
    the replies to send."""

    def __init__(
        self,
        net,
        simulations=200,
        seed=0,
        play=False,
        move_timeout=15.0,
        encode=None,
        save_dir=None,
    ):
        self.net = net
        self.simulations = simulations
        self.rng = random.Random(seed)
        self.play = play
        self.move_timeout = move_timeout
        if encode is None:
            from catanatron.colonist.client import encode
        self.encode = encode
        self.live = None
        self.answered = None  # position key of the last move sent
        self.socket = None  # the game's socket, as the extension numbers them
        # The room's serverId: the channel game messages are sent on. (The
        # same message carries a reconnect token, which is never kept.)
        self.channel = None
        self.game_id = None  # colonist's id for the game being followed
        self.expect = (
            None  # (position key, described move, time sent) while unconfirmed
        )
        self.seq = 0
        self.save_dir = save_dir
        self.frames = []  # this game's messages, for save()
        self.saved_path = None
        self.since_sent = []  # message types colonist sent since the last move
        self.offers = {}  # open trade offers between players: id -> offer
        self.declined = set()  # offer ids the bot has declined

    def reset(self):
        self.live, self.answered, self.expect = None, None, None
        self.offers, self.declined = {}, set()
        self.channel = self.game_id = None
        return [self.status("waiting", "waiting for a game to start")]

    @staticmethod
    def status(state, text):
        return {"kind": "status", "state": state, "text": text}

    def handle(self, message):
        """One message from the extension -> list of replies. Never raises: a
        failure stops the bot for this game (with the error in the status)
        instead of dropping the connection."""
        try:
            return self._handle(message)
        except Exception as e:
            import traceback

            traceback.print_exc()
            where = f"bridge error on a {message.get('kind')} message: {e!r}"
            if self.live is not None:
                self.live.error = where
            self.expect = None
            return [self.status("stopped", where)]

    def _handle(self, message):
        kind = message.get("kind")
        if kind == "reset":
            return self.reset()
        if kind == "replay-done":
            if not self.live or self.live.error:
                return []
            return self.decline_offers() + self.think()
        if kind != "frame":
            return []
        self.seq += 1
        self._keep(message)
        frame = decode(
            {
                **message,
                "seq": self.seq,
                "t": message.get("t", 0),
                "socket": message.get("socket", 0),
            }
        )
        if frame.dir != "in":
            return []
        replaying = bool(message.get("replay"))
        if frame.type == ROOM_JOINED:
            payload = frame.data["data"].get("payload")
            if isinstance(payload, dict) and payload.get("serverId"):
                self.channel = str(payload["serverId"])
            return self.check_timeout() if self.live else []
        if frame.type == C.GAME_STATE:
            payload = frame.data["data"].get("payload")
            if not isinstance(payload, dict) or "gameState" not in payload:
                return []  # same message type, another layout: not a game start
            game_id = (payload.get("gameSettings") or {}).get("id")
            if self.live is not None and not self.live.over and game_id == self.game_id:
                # colonist resends the whole game during play; the game is
                # already followed update by update, and rebuilding from a
                # mid-game snapshot would lose the moves before it.
                return self.check_timeout()
            self.game_id = game_id
            if self.live is not None:
                # A new game: the previous one's messages go to their own file.
                self.save()
                self.frames, self.saved_path = self.frames[-1:], None
            self.live = LiveGame(payload, frame.t)
            self.answered, self.expect = None, None
            self.offers, self.declined = {}, set()
            self.socket = message.get("socket")
            if self.live.error:
                return [self.status("stopped", self.live.error)]
            who = self.live.bot.value if self.live.bot else "a spectator"
            replies = [self.status("following", f"game started; playing as {who}")]
            if not replaying:
                # The page restarts its count of game messages (sequence numbers).
                replies.insert(0, {"kind": "new-game"})
            return replies + ([] if replaying else self.think())
        if self.live is None:
            return []
        if self.expect is not None:
            self.since_sent.append(frame.type)
        self.track_offers(frame)
        declines = [] if replaying else self.decline_offers()
        before = self.live.position_key
        if not self.live.apply(frame):
            return declines + self.check_timeout()
        if self.live.error:
            return [
                self.status(
                    "stopped", f"out of sync, not playing on: {self.live.error}"
                )
            ]
        if self.live.over:
            return declines + [self.status("waiting", "game over")]
        stop = self.check_played(before)
        if stop:
            return declines + stop
        return declines + ([] if replaying else self.think())

    def track_offers(self, frame):
        """Keep the open trade offers between players up to date."""
        if frame.type != C.DIFF:
            return
        diff = (frame.data["data"].get("payload") or {}).get("diff") or {}
        active = (diff.get("tradeState") or {}).get("activeOffers") or {}
        for offer_id, change in active.items():
            if change is None:
                self.offers.pop(offer_id, None)
            else:
                offer = self.offers.setdefault(offer_id, {})
                for key, value in change.items():
                    if isinstance(value, dict) and isinstance(offer.get(key), dict):
                        offer[key] = {**offer[key], **value}
                    else:
                        offer[key] = value

    def decline_offers(self):
        """The bot never trades with players (PLAN.md): decline every offer
        that is waiting on its answer, once."""
        if not self.play or self.live is None or self.live.error:
            return []
        you = str(self.live.conv.you)
        replies = []
        for offer_id, offer in self.offers.items():
            pending = (offer.get("playerResponses") or {}).get(you) == 0
            if not pending or offer_id in self.declined:
                continue
            from catanatron.colonist.client import NotKnownYet, decline_trade

            try:
                raws = decline_trade(offer_id, {"channel": self.channel})
            except NotKnownYet:
                continue
            self.declined.add(offer_id)
            replies += [
                {
                    "kind": "send",
                    "socket": self.socket,
                    "b64": base64.b64encode(r).decode(),
                }
                for r in raws
            ]
            replies.append(self.status("following", f"declined trade offer {offer_id}"))
        return replies

    def check_played(self, before):
        """After the position changed: if a move was sent, it must be exactly
        what the bot did next."""
        if self.expect is None or self.live.position_key == before:
            return []
        _, expected, _ = self.expect
        self.expect = None
        mine = [
            action_from_json(entry[0])
            for entry in self.live.record["log"][before:]
            if entry[0][0] == self.live.bot.value
        ]
        if not mine:
            return []  # the update was someone else's (e.g. the opponent's discard)
        try:
            done = self.live.describe(mine[0])
        except Exception:
            done = {"label": str(mine[0])}
        if {k: v for k, v in done.items() if k != "label"} != {
            k: v for k, v in expected.items() if k != "label"
        }:
            self.live.error = (
                f"sent {expected['label']}, but colonist recorded {done['label']}"
            )
            return [self.status("stopped", self.live.error)]
        return []

    def check_timeout(self):
        if self.expect and time.time() - self.expect[2] > self.move_timeout:
            label = self.expect[1]["label"]
            self.expect = None
            seen = ", ".join(str(t) for t in self.since_sent) or "nothing"
            self.live.error = (
                f"sent {label}, but colonist didn't show it within "
                f"{self.move_timeout:.0f} s (colonist sent message types: {seen})"
            )
            return [self.status("stopped", self.live.error)]
        return []

    def think(self):
        live = self.live
        if not live.bot_to_move or live.position_key == self.answered:
            return []
        self.answered = live.position_key
        start = time.time()
        game = live.game
        move, options, value = self.choose(game)
        plan = [live.describe(move)]
        if move.action_type == ActionType.DISCARD_RESOURCE:
            # colonist takes all discards at once; the simulator one at a time.
            plan = [live.describe(a) for a in self.plan_discards(game)]
        replies = []
        played = False
        if self.play:
            from catanatron.colonist.client import NotKnownYet

            try:
                context = {
                    "channel": self.channel,
                    "creator": live.conv.you,
                    "phase": game_phase(game),
                }
                for raw in self.encode(plan, context):
                    replies.append(
                        {
                            "kind": "send",
                            "socket": self.socket,
                            "b64": base64.b64encode(raw).decode(),
                        }
                    )
                played = True
                self.expect = (live.position_key, plan[0], time.time())
                self.since_sent = []
            except NotKnownYet as e:
                replies.append(self.status("following", f"suggesting only: {e}"))
        move = {
            "kind": "move",
            "position": live.position_key,
            "move": plan[0],
            "plan": plan,
            "options": [
                {"move": live.describe(a), "share": round(s, 3)} for a, s in options
            ],
            "win_chance": round((value + 1) / 2, 3),
            "ms": int((time.time() - start) * 1000),
            "played": played,
        }
        return [move] + replies

    # ----- saving games -----
    def _keep(self, message):
        if self.save_dir is None or "b64" not in message:
            return
        b64 = message["b64"]
        if message.get("dir") == "in":
            b64 = _without_token(b64)
        self.frames.append(
            {
                "seq": len(self.frames),
                "t": message.get("t", 0),
                "dir": message.get("dir"),
                "socket": message.get("socket", 0),
                "b64": b64,
            }
        )

    def save(self):
        """Write this game's messages as a capture; returns the path (or None
        with nothing to save). The same game keeps one file, rewritten."""
        if self.save_dir is None or self.live is None or not self.frames:
            return None
        if self.saved_path is None:
            os.makedirs(self.save_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.saved_path = os.path.join(self.save_dir, f"bridge-{stamp}.json")
        doc = {"format": FORMAT, "source": "bridge", "messages": self.frames}
        try:
            with open(self.saved_path, "w") as f:
                json.dump(doc, f)
        except OSError as e:
            print(f"  could not save the game: {e}", flush=True)
            return None
        return self.saved_path

    def choose(self, game):
        """(move, [(move, share)], value for the player to move)."""
        actions = game.playable_actions
        if len(actions) == 1:
            _, value = self.net.evaluate(
                game, game.state.current_color(), E.legal_indices(actions)
            )
            return actions[0], [(actions[0], 1.0)], value
        if self.simulations == 0:
            priors, value = self.net.evaluate(
                game, game.state.current_color(), E.legal_indices(actions)
            )
            order = np.argsort(-priors)[:3]
            return (
                actions[order[0]],
                [(actions[i], float(priors[i])) for i in order],
                value,
            )
        from catanatron.colonist.mcts import Search

        search = Search(self.net, self.simulations, rng=self.rng, noise=False)
        action, root = search.run(game)
        _, value = search.root_targets(root)
        # Show moves by the share of simulations they got: the search's
        # finalists, the move it plays among them.
        share = root.N / max(root.N.sum(), 1)
        key = E.action_key(action)
        chosen = next(i for i, a in enumerate(root.actions) if E.action_key(a) == key)
        order = [chosen] + [i for i in np.argsort(-share) if i != chosen][:2]
        move = next(a for a in actions if E.action_key(a) == key)
        return move, [(root.actions[i], float(share[i])) for i in order], value

    def plan_discards(self, game):
        """All of the bot's discards for this 7, chosen one by one on a copy."""
        g = game.copy()
        color = g.state.current_color()
        plan = []
        while (
            g.state.is_discarding
            and g.state.current_color() == color
            and g.winning_color() is None
        ):
            move, _, _ = self.choose(g)
            plan.append(move)
            g.execute(move, validate_action=False)
        return plan


def _without_token(b64):
    """A room-join message without its reconnect token (anything else as is)."""
    try:
        doc = msgpack.unpackb(base64.b64decode(b64), raw=False, strict_map_key=False)
    except Exception:
        return b64
    data = doc.get("data") if isinstance(doc, dict) else None
    payload = data.get("payload") if isinstance(data, dict) else None
    if not isinstance(payload, dict) or "reconnectToken" not in payload:
        return b64
    payload["reconnectToken"] = "<removed>"
    return base64.b64encode(msgpack.packb(doc)).decode()


def _dumps(reply):
    """JSON for the extension; numpy numbers become plain ones."""
    return json.dumps(
        reply, default=lambda o: o.item() if hasattr(o, "item") else str(o)
    )


async def serve_forever(make_bridge, host=HOST, port=PORT):
    """Serve until stopped. Each connection (one per colonist tab) gets its own
    Bridge from make_bridge(), so two tabs never mix their games."""
    from websockets.asyncio.server import serve

    async def handler(socket):
        bridge = make_bridge()
        print(f"extension connected from {socket.remote_address}", flush=True)
        for reply in bridge.reset():
            await socket.send(_dumps(reply))
        async for raw in socket:
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            # Thinking can take a second or two: keep the connection responsive.
            replies = await asyncio.to_thread(bridge.handle, message)
            if any(
                r["kind"] == "status" and r["state"] in ("stopped", "waiting")
                for r in replies
            ):
                saved = bridge.save()
                if saved:
                    print(f"  game saved to {saved}", flush=True)
            for reply in replies:
                if reply["kind"] == "move":
                    print(
                        f"  move: {reply['move']['label']} "
                        f"(win chance {reply['win_chance']:.0%}, {reply['ms']} ms)",
                        flush=True,
                    )
                elif reply["kind"] == "status":
                    print(f"  {reply['state']}: {reply['text']}", flush=True)
                await socket.send(_dumps(reply))
        saved = bridge.save()
        print(
            "extension disconnected" + (f"; game saved to {saved}" if saved else ""),
            flush=True,
        )

    async with serve(handler, host, port, max_size=2**24):
        print(f"bridge listening on ws://{host}:{port}", flush=True)
        await asyncio.Future()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("net", help="net.npz")
    parser.add_argument("--simulations", type=int, default=200)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument(
        "--play",
        action="store_true",
        help="send the bot's moves (otherwise only suggest)",
    )
    parser.add_argument("--move-timeout", type=float, default=15.0)
    parser.add_argument(
        "--save",
        default="captures",
        metavar="DIR",
        help="save each game followed here (default captures/); '' to turn off",
    )
    args = parser.parse_args(argv)
    from catanatron.colonist.net import Net

    net = Net.load(args.net)
    try:
        asyncio.run(
            serve_forever(
                lambda: Bridge(
                    net,
                    args.simulations,
                    play=args.play,
                    move_timeout=args.move_timeout,
                    save_dir=args.save or None,
                ),
                port=args.port,
            )
        )
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
