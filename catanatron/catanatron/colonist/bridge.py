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
import hashlib
import json
import os
import random
import sys
import tempfile
import time
import uuid

import msgpack

import numpy as np

from catanatron.colonist import convert as C
from catanatron.colonist import encode as E
from catanatron.colonist.capture import FORMAT, decode, unpack_client
from catanatron.colonist.live import LiveGame
from catanatron.models.enums import ActionType
from catanatron.serialization import action_from_json

HOST, PORT = "127.0.0.1", 8765
ROOM_JOINED = 1  # message type when joining a game room: carries its serverId


def in_progress(payload):
    """Is this full-game message a snapshot of a game already under way?"""
    state = payload.get("gameState") or {}
    if (state.get("currentState") or {}).get("completedTurns", 0) > 0:
        return True
    board = state.get("mapState") or {}
    pieces = list((board.get("tileCornerStates") or {}).values()) + list(
        (board.get("tileEdgeStates") or {}).values()
    )
    return any(p.get("owner") for p in pieces)


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
        spectator_state=None,
        bot=None,
    ):
        self.net = net
        # Which net and search played (net_info), saved with each capture so
        # every live game's data says which version produced it.
        self.bot = bot
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
            None  # (position key, described plan, time sent) while unconfirmed
        )
        self.seq = 0
        self.save_dir = save_dir
        self.snapshot_writer = None
        if spectator_state:
            from catanatron.colonist.spectator import SnapshotWriter

            self.snapshot_writer = SnapshotWriter(spectator_state)
        self.frames = []  # this game's messages, for save()
        self.saved_path = None
        self.since_sent = []  # message types colonist sent since the last move
        self.last_log_id = -1  # highest Colonist game-log id seen in a diff
        self.retry_count = 0  # a pending end turn may be retried once on reconnect
        self.no_effect_retry = False  # retry one acknowledged road with no board effect
        self.sent_raws = []
        self.pending_sends = {}  # sendId -> (reply, last sent, retries)
        self.offers = {}  # open trade offers between players: id -> offer
        self.declined = set()  # offer ids the bot has declined

    def reset(self):
        self.live, self.answered, self.expect = None, None, None
        self.offers, self.declined = {}, set()
        self.channel = self.game_id = None
        self.last_log_id = -1
        self.retry_count = 0
        self.no_effect_retry = False
        self.sent_raws = []
        self.pending_sends = {}
        return [self.status("waiting", "waiting for a game to start")]

    @staticmethod
    def status(state, text):
        return {"kind": "status", "state": state, "text": text}

    def handle(self, message):
        """One message from the extension -> list of replies. Never raises: a
        failure stops the bot for this game (with the error in the status)
        instead of dropping the connection."""
        try:
            replies = self._handle(message)
            if (
                self.snapshot_writer
                and self.live
                and (not message.get("replay") or message.get("kind") == "replay-done")
            ):
                try:
                    self.snapshot_writer.update(
                        self.live,
                        force=self.live.over or bool(self.live.error),
                    )
                except Exception as error:
                    # Spectator publishing must never stop or change ranked play.
                    print(f"spectator snapshot failed: {error!r}", file=sys.stderr)
            return replies
        except Exception as e:
            import traceback

            traceback.print_exc()
            where = f"bridge error on a {message.get('kind')} message: {e!r}"
            if self.live is not None:
                self.live.error = where
            self.expect = None
            self.pending_sends.clear()
            return [self.status("stopped", where)]

    def _handle(self, message):
        kind = message.get("kind")
        if kind == "reset":
            return self.reset()
        if kind == "replay-done":
            if not self.live:
                return []
            if self.live.error:
                # Save the full replay backlog, including frames that arrived
                # after the fault, so recovery can tell whether the game ended.
                return [self.status("stopped", self.live.error)]
            return self.decline_offers() + self.think()
        if kind == "send-confirmed":
            self.pending_sends.pop(message.get("sendId"), None)
            return []
        if (
            kind == "send-failed"
            and self.expect is not None
            and message.get("sendId") in self.pending_sends
        ):
            self.live.error = (
                f"could not send move: {message.get('reason', 'unknown error')}"
            )
            self.expect = None
            self.pending_sends.clear()
            return [self.status("stopped", self.live.error)]
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
        if frame.dir == "out" and not message.get("replay"):
            # The page can still be running an older extension script that
            # echoes the sent frame but does not send send-confirmed.  The
            # echo is direct evidence that the browser sent these bytes; do
            # not retry a move merely because its separate ack was lost.
            sent = frame.data if isinstance(frame.data, dict) else None
            if sent and sent.get("header") == [3, 1]:
                body = sent.get("body") or {}
                for send_id, (reply, _, _) in list(self.pending_sends.items()):
                    if frame.socket != reply["socket"]:
                        continue
                    planned = unpack_client(base64.b64decode(reply["b64"]))
                    observed = (
                        sent.get("channel"),
                        body.get("action"),
                        body.get("payload"),
                    )
                    expected = (
                        (
                            planned["channel"],
                            planned["body"].get("action"),
                            planned["body"].get("payload"),
                        )
                        if planned
                        else None
                    )
                    if observed == expected:
                        self.pending_sends.pop(send_id, None)
                        break
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
                if payload.get("playerColor") != self.live.conv.you:
                    self.live.error = (
                        "cloud browser lost player control and is now a spectator"
                    )
                    return [self.status("stopped", self.live.error)]
                # A reconnect may skip diffs while the socket is down. Recover
                # the missing log entries and board changes from the full
                # snapshot, while retaining the history we already followed.
                if message.get("socket") != self.socket and not (
                    replaying and self.expect
                ):
                    self.socket = message.get("socket")
                # Colonist can also reconnect the game session on the same
                # socket ("isReconnectingSession", seen live on new accounts)
                # and resend the full game: a move sent just before is lost
                # the same way, so recover the same way.
                if not replaying and self.expect is not None:
                    return self.retry_after_reconnect(payload)
                self.reconcile_snapshot(payload)
                if self.live.error:
                    return [self.status("stopped", self.live.error)]
                return self.check_timeout()
            self.game_id = game_id
            if self.live is not None:
                # A new game: the previous one's messages go to their own file.
                self.save()
                self.frames, self.saved_path = self.frames[-1:], None
            self.live = LiveGame(payload, frame.t)
            self.answered, self.expect = None, None
            self.pending_sends.clear()
            self.last_log_id = max(
                (int(k) for k in payload["gameState"].get("gameLogState", {})),
                default=-1,
            )
            self.retry_count = 0
            self.no_effect_retry = False
            self.sent_raws = []
            self.offers, self.declined = {}, set()
            self.socket = message.get("socket")
            if not self.live.error and in_progress(payload):
                # A game joined midway (e.g. after a page reload) comes as a
                # snapshot without its history, and the converter can only
                # follow a game from its start. Playing on it once sent a
                # setup move into a game in progress.
                self.live.error = (
                    "joined a game already in progress; the bot only follows "
                    "games it saw from the start"
                )
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
        if frame.type == C.DIFF:
            logs = (
                (frame.data["data"].get("payload") or {})
                .get("diff", {})
                .get("gameLogState", {})
            )
            self.last_log_id = max([self.last_log_id, *(int(k) for k in logs)])
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

    def reconcile_snapshot(self, payload):
        """Apply the part of a same-game snapshot missed during a reconnect
        (convert._Converter.reconcile, which offline conversion uses too)."""
        state = payload["gameState"]
        try:
            changed = self.live.conv.reconcile(state)
            if changed:
                self.live._rebuild()
        except Exception as error:
            self.live.error = f"could not reconcile game snapshot: {error}"
            return
        logs = state.get("gameLogState") or {}
        self.last_log_id = max([self.last_log_id, *(int(k) for k in logs)])

    def retry_after_reconnect(self, payload):
        """A reconnect while a move was unconfirmed. If the new full snapshot
        shows nothing happened (same turn, no new log entries; for a roll, dice
        not thrown), resend the move once: it can't happen twice. If the game
        moved on, catch up from the snapshot, check the move as usual and play
        on. Reconnects mid-move stopped a casual bot twice in 15 minutes (seen
        live: a roll, then a road)."""
        _, plan, _ = self.expect
        cur = payload["gameState"].get("currentState", {})
        logs = payload["gameState"].get("gameLogState", {})
        new_logs = [
            e.get("text", {}).get("type")
            for k, e in logs.items()
            if int(k) > self.last_log_id
        ]
        unchanged = (
            cur.get("completedTurns") == self.live.conv.completed
            and cur.get("currentTurnPlayerColor") == self.live.conv.current
            and all(kind in C.LOG_IGNORED for kind in new_logs)
        )
        kind = plan[0]["type"]
        if kind == "ROLL" and payload["gameState"].get("diceState", {}).get(
            "diceThrown"
        ):
            unchanged = False
        if not unchanged:
            before = self.live.position_key
            self.reconcile_snapshot(payload)
            if self.live.error:
                self.expect = None
                self.pending_sends.clear()
                return [self.status("stopped", self.live.error)]
            replies = self.check_played(before)
            if any(r.get("state") == "stopped" for r in replies):
                return replies
            if self.expect is not None:
                # The game moved on without our move showing: can't tell why.
                self.live.error = (
                    "reconnected while a move was unconfirmed; stopped safely"
                )
                self.expect = None
                self.pending_sends.clear()
                return [self.status("stopped", self.live.error)]
            return replies + self.think()
        if self.retry_count:
            self.live.error = "reconnected while a move was unconfirmed; stopped safely"
            self.expect = None
            self.pending_sends.clear()
            return [self.status("stopped", self.live.error)]
        self.retry_count += 1
        self.last_log_id = max([self.last_log_id, *(int(k) for k in logs)])
        from catanatron.colonist.client import NotKnownYet

        try:
            context = {
                "channel": self.channel,
                "creator": self.live.conv.you,
                "phase": game_phase(self.live.game),
            }
            raws = self.encode(plan, context)
        except NotKnownYet as error:
            self.live.error = f"could not retry {kind.lower()}: {error}"
            self.expect = None
            self.pending_sends.clear()
            return [self.status("stopped", self.live.error)]
        self.expect = (self.live.position_key, plan, time.time())
        self.since_sent = []
        return [
            self.status(
                "following",
                f"reconnected; retrying unconfirmed {kind.lower().replace('_', ' ')}",
            ),
            *self.send_replies(raws),
        ]

    def send_replies(self, raws):
        """Give each browser send a stable ID for safe acknowledgment and retry."""
        self.pending_sends.clear()
        replies = []
        for raw in raws:
            reply = {
                "kind": "send",
                "socket": self.socket,
                "b64": base64.b64encode(raw).decode(),
                "sendId": uuid.uuid4().hex,
            }
            self.pending_sends[reply["sendId"]] = [reply, time.time(), 0]
            replies.append(reply)
        return replies

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
        _, plan, _ = self.expect
        expected = plan[0]
        self.expect = None
        self.pending_sends.clear()
        self.sent_raws = []
        mine = [
            action_from_json(entry[0])
            for entry in self.live.record["log"][before:]
            if entry[0][0] == self.live.bot.value
        ]
        if not mine:
            return []  # the update was someone else's (e.g. the opponent's discard)
        if expected["type"] == "DISCARD_RESOURCE":
            # colonist logs a 7's discards together, in its own order.
            sent = sorted(m["resource"] for m in plan)
            done = sorted(
                self.live.describe(a)["resource"]
                for a in mine
                if a.action_type == ActionType.DISCARD_RESOURCE
            )
            if sent != done:
                self.live.error = (
                    f"sent discards {sent}, but colonist recorded discards {done}"
                )
                return [self.status("stopped", self.live.error)]
            return []
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
        if self.expect:
            now = time.time()
            retries = []
            for send_id, pending in self.pending_sends.items():
                reply, sent_at, attempts = pending
                if now - sent_at < 2:
                    continue
                if attempts == 0:
                    pending[1:] = [now, 1]
                    retries.append(reply)
                else:
                    self.live.error = "browser did not acknowledge a move after retry"
                    self.expect = None
                    self.pending_sends.clear()
                    return [self.status("stopped", self.live.error)]
            if retries:
                return retries
        if (
            self.expect
            and self.expect[1][0]["type"] == "BUILD_ROAD"
            and not self.no_effect_retry
            and not self.pending_sends
            and self.sent_raws
            and self.live.bot_to_move
            and self.live.position_key == self.expect[0]
            and time.time() - self.expect[2] >= 7
        ):
            # The browser confirmed the send, but the server sometimes ignores
            # the road. Repeating the same edge is safe: if the first request
            # takes effect late, that edge is already occupied.
            self.no_effect_retry = True
            return [
                self.status("following", "retrying an unconfirmed road once"),
                *self.send_replies(self.sent_raws),
            ]
        timeout = self.move_timeout
        if self.expect and self.expect[1][0]["type"] == "MOVE_ROBBER":
            pending = self.live.conv.pending_robber
            if pending is not None:
                color, coordinate = pending
                expected_tile = self.expect[1][0].get("tile")
                if (
                    color == self.live.conv.you
                    and self.live.tile_of_cube.get(tuple(coordinate)) == expected_tile
                ):
                    # Colonist has placed the robber, but waits for the steal
                    # result (or the next log entry when nobody was robbed).
                    timeout += 60
        if self.expect and time.time() - self.expect[2] > timeout:
            label = self.expect[1][0]["label"]
            self.expect = None
            self.pending_sends.clear()
            seen = ", ".join(str(t) for t in self.since_sent) or "nothing"
            self.live.error = (
                f"sent {label}, but colonist didn't show it within "
                f"{timeout:.0f} s (colonist sent message types: {seen})"
            )
            return [self.status("stopped", self.live.error)]
        return []

    def think(self):
        live = self.live
        if not live.bot_to_move or live.position_key == self.answered:
            return []
        self.answered = live.position_key
        self.retry_count = 0
        self.no_effect_retry = False
        self.sent_raws = []
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
                self.sent_raws = self.encode(plan, context)
                replies.extend(self.send_replies(self.sent_raws))
                played = True
                self.expect = (live.position_key, plan, time.time())
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
        bot = getattr(self, "bot", None)
        if bot:
            doc["bot"] = {k: v for k, v in bot.items() if k != "kind"}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.save_dir,
                prefix=".bridge-",
                suffix=".json",
                delete=False,
            ) as f:
                temporary = f.name
                json.dump(doc, f)
            os.replace(temporary, self.saved_path)
        except OSError as e:
            print(f"  could not save the game: {e}", flush=True)
            return None
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
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


async def serve_forever(make_bridge, host=HOST, port=PORT, info=None):
    """Serve until stopped. Each connection (one per colonist tab) gets its own
    Bridge from make_bridge(), so two tabs never mix their games. `info` (which
    net, how much search) is sent first on every connection, for the panel."""
    from websockets.asyncio.server import serve

    async def handler(socket):
        bridge = make_bridge()
        print(f"extension connected from {socket.remote_address}", flush=True)
        if info:
            await socket.send(_dumps(info))
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
        if info:
            print(f"  {describe_info(info)}", flush=True)
        await asyncio.Future()


def net_info(path, simulations, play, label=None):
    """What the extension's panel shows about the bot: which net file (and the
    training epoch, when the file records it), how much search, which mode.
    Also a hash of the file and an optional label (e.g. the training round),
    which saved captures keep."""
    info = {
        "kind": "info",
        "net": path,
        "simulations": simulations,
        "play": play,
    }
    if label:
        info["label"] = label
    try:
        with open(path, "rb") as f:
            info["sha256"] = hashlib.file_digest(f, "sha256").hexdigest()[:16]
    except OSError:
        pass
    try:
        with np.load(path) as z:
            if "meta_epoch" in z:
                info["epoch"] = int(z["meta_epoch"])
    except (OSError, ValueError):
        pass
    return info


def describe_info(info):
    epoch = f", epoch {info['epoch']}" if "epoch" in info else ""
    if info.get("label"):
        epoch += f" ({info['label']})"
    mode = "playing" if info.get("play") else "advising"
    return f"net {info['net']}{epoch}, {info['simulations']} simulations, {mode}"


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
    parser.add_argument("--label", help="names the net in captures, e.g. 'sp1 round 8'")
    parser.add_argument(
        "--spectator-state",
        help="write a redacted live-board snapshot to this local JSON path",
    )
    args = parser.parse_args(argv)
    if (
        not args.spectator_state
        and args.save
        and os.path.basename(os.path.normpath(args.save)) == "ranked"
    ):
        # Also covers an already-running ranked runner when it starts a fresh
        # bridge for a newly promoted model.
        args.spectator_state = os.path.join("data", "ranked", "spectator-state.json")
    from catanatron.colonist.net import Net

    net = Net.load(args.net)
    info = net_info(args.net, args.simulations, args.play, args.label)
    try:
        asyncio.run(
            serve_forever(
                lambda: Bridge(
                    net,
                    args.simulations,
                    play=args.play,
                    move_timeout=args.move_timeout,
                    save_dir=args.save or None,
                    spectator_state=args.spectator_state,
                    bot=info,
                ),
                port=args.port,
                info=info,
            )
        )
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
