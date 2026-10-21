"""The bridge between colonist in the browser and the AI (PLAN.md, Phase 4).

    python -m catanatron.colonist.bridge models/ab2/net.npz --simulations 200
    python -m catanatron.colonist.bridge models/ab2/net.npz --play     # plays its moves
    python -m catanatron.colonist.bridge models/ab2/net.npz --play \
        --opponent-model models/human/net.npz --opponent-mix 0.5

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
import copy
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
# Thinking time (bridge.think): colonist's timer gives each decision a
# deadline. Keep this much of it in reserve (messages, colonist's handling),
# and spend at most a share of the rest, up to a cap in seconds, with the
# simulations capped too. Seen live: an opening settlement gets 60 s, its
# road 15 s, the roll 3 s (8 s holding a card playable before it), the turn's
# actions 30 s from the roll plus 5 s for every build, buy or trade, a robber
# move 15 s and a 7's discards 20 s.
TIME_RESERVE = 2.5
# prompt: (share of the time left, cap in seconds, simulations cap as a
# multiple of --simulations; at 2400: 20000, 4000, 800, 4800).
BUDGETS = {
    "BUILD_INITIAL_SETTLEMENT": (0.4, 20.0, 25 / 3),
    "BUILD_INITIAL_ROAD": (0.4, 5.0, 5 / 3),
    "ROLL": (0.33, 1.0, 1 / 3),
    "PLAY_TURN": (0.3, 5.0, 2.0),
    "MOVE_ROBBER": (0.4, 5.0, 2.0),
    "DISCARD": (0.4, 5.0, 2.0),
}


def budgets_from_env(text):
    """BUDGETS overrides from THINK_BUDGETS, e.g. "PLAY_TURN=0.3:10:5,ROLL=0.33:2:0.67"
    (prompt=share:cap seconds:simulations multiple), for one account's clock
    trial (E60) from its env file. Empty: no change."""
    out = {}
    for item in filter(None, (part.strip() for part in (text or "").split(","))):
        prompt, _, values = item.partition("=")
        if prompt not in BUDGETS:
            raise ValueError(f"THINK_BUDGETS: unknown prompt {prompt!r}")
        share, cap, scale = (float(v) for v in values.split(":"))
        if not (0 < share <= 1 and cap > 0 and scale > 0):
            raise ValueError(f"THINK_BUDGETS: bad values in {item!r}")
        out[prompt] = (share, cap, scale)
    return out


#: Resends of one unconfirmed move, each after a snapshot proves it didn't land.
MAX_RECONNECT_RETRIES = 3

#: Seconds the browser has to confirm a send before it is resent once, and
#: again before the bot stops. Was 2: on a bot VM short of memory the page froze
#: for 7 s (bot8's first game, Oct 19: its own 1 s pings stopped) and the bot
#: stopped in the opening, losing the game by inactivity.
ACK_SECONDS = 5


def think_budget(game, remaining, simulations):
    """(seconds or None, simulation cap) for the player to move, given the
    seconds left before colonist's timer acts (None: unknown, so a plain
    `simulations` search)."""
    from catanatron.state_functions import player_has_rolled

    state = game.state
    prompt = state.current_prompt.value
    # Road Building is often played before the roll; its free roads are a
    # build decision, not the roll's 1 s / 800 simulations.
    if (
        prompt == "PLAY_TURN"
        and not state.is_road_building
        and not player_has_rolled(state, state.current_color())
    ):
        prompt = "ROLL"
    share, cap, scale = BUDGETS.get(prompt, BUDGETS["PLAY_TURN"])
    if remaining is None:
        return None, simulations
    seconds = max(0.05, min(cap, share * (remaining - TIME_RESERVE)))
    return seconds, max(1, round(simulations * scale))


# Seconds before colonist's timer acts to send a discard planned early (its
# timer fired 0.3-0.8 s after the deadline in captures).
EARLY_DISCARD_MARGIN = 3.0


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


def setup_start(payload):
    """For a snapshot of a game still in its opening placements whose log
    runs from entry 0 (seen live: Sherie3424's page first saw a game after
    one settlement, during a reconnect loop), the same game with nothing
    placed yet, or None. Following it from there and then catching up from
    the snapshot (reconcile) plays the opening moves through, as if the bot
    had seen them happen."""
    state = payload.get("gameState") or {}
    logs = state.get("gameLogState") or {}
    order = payload.get("playOrder") or []
    current = state.get("currentState") or {}
    if "0" not in logs or current.get("completedTurns", 0) >= 2 * len(order):
        return None
    start = copy.deepcopy(payload)
    s = start["gameState"]
    board = s["mapState"]
    for corner in board.get("tileCornerStates", {}).values():
        corner.pop("owner", None)
        corner.pop("buildingType", None)
    for edge in board.get("tileEdgeStates", {}).values():
        edge.pop("owner", None)
        edge.pop("type", None)
    s["gameLogState"] = {}
    s["currentState"] = {
        **current,
        "completedTurns": 0,
        "currentTurnPlayerColor": order[0] if order else None,
    }
    for player in (s.get("playerStates") or {}).values():
        player["victoryPointsState"] = {}
    return start


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
        opponent_model=None,
        opponent_mix=0.0,
        judgment_filter=False,
        endgame_filter=False,
        robber_filter=False,
    ):
        self.net = net
        # A net trained on people's moves (opponent_model.py): the search
        # mixes it into the opponent's prior, at this weight (0: off).
        self.opponent_model = opponent_model
        self.opponent_mix = opponent_mix
        self.judgment_filter = judgment_filter
        self.endgame_filter = endgame_filter
        self.robber_filter = robber_filter
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
        self.timer_discarded = False  # this update has colonist's timer discarding
        # When colonist's timer acts on the current decision (local clock),
        # from its currentState's startTime and allocatedTime.
        self.deadline = None
        self.timer_start = self.timer_allocated = None
        self.budget = (None, None)  # (seconds, simulations) for choose()
        self.searched = 0  # simulations the last decision ran
        # The bot's discard for a 7, planned while the earlier seat (the
        # opponent) still had to discard first: {turn, plan, seconds, sent}.
        self.early_discard = None
        self.retry_count = 0  # resends of the pending move after reconnects
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
        self.deadline = self.early_discard = None
        self.timer_start = self.timer_allocated = None
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
            if message.get("kind") == "frame" and not message.get("replay"):
                replies = replies + self.early_discard_due()
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
            if message.get("reason") == "game socket not open":
                # The game socket closed and no newer one is open yet: colonist
                # reconnects within a second or two (seen live, ranked 2026-10-12:
                # the new socket rejoined as the same player 1.3 s later). Wait
                # for its snapshot, which resends the move once
                # (retry_after_reconnect); without one, the move times out.
                self.pending_sends.clear()
                return [
                    self.status(
                        "following", "move not sent: waiting for colonist to reconnect"
                    )
                ]
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
            opening = setup_start(payload) if in_progress(payload) else None
            self.live = LiveGame(opening or payload, frame.t)
            if opening is not None:
                self.reconcile_snapshot(payload)
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
            # The game's first snapshot carries the opening settlement's 60 s:
            # without it the first seat's first settlement (no diff yet) got
            # a plain search, or a previous game's expired deadline.
            # A replayed start counts too: the extension reconnecting as a game
            # began (2 of E80's first 4 games, Oct 21) left the first
            # settlement with no timer, so a plain search of 1.5-6 s.
            self.deadline = self.early_discard = None
            self.timer_start = self.timer_allocated = None
            self.note_deadline(
                payload["gameState"].get("currentState"), frame.t, replaying
            )
            if not self.live.error and in_progress(payload) and opening is None:
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
        self.timer_discarded = False
        if frame.type == C.DIFF:
            diff = (frame.data["data"].get("payload") or {}).get("diff", {})
            logs = diff.get("gameLogState", {})
            foreign_log_player = any(
                (entry.get("text") or {}).get("playerColor") not in self.live.conv.seat
                for entry in logs.values()
                if (entry.get("text") or {}).get("playerColor") is not None
                and (entry.get("text") or {}).get("type") not in C.LOG_IGNORED
            )
            foreign_state_player = any(
                int(color) not in self.live.conv.seat
                for color in diff.get("playerStates", {})
            )
            turn_color = (diff.get("currentState") or {}).get("currentTurnPlayerColor")
            foreign_turn = (
                turn_color is not None and turn_color not in self.live.conv.seat
            )
            if foreign_log_player or foreign_state_player or foreign_turn:
                # Reconnecting sessions have sent diffs for a player not in
                # this match. A later full snapshot restores the real log.
                return declines + [
                    self.status("following", "waiting for game snapshot")
                ]
            if self.live.game.state.is_initial_build_phase and any(
                entry.get("text", {}).get("type") == C.LOG_PLACED_SETUP
                for entry in logs.values()
            ):
                # During a reconnect, Colonist briefly sent setup placements
                # that its next full snapshot contradicted (first BLUE on
                # RED's turn, then BLUE at the wrong corner). Keep the last
                # verified position and log cursor until a coherent update.
                probe = copy.deepcopy(self.live)
                probe.apply(frame)
                if probe.error:
                    return declines + [
                        self.status("following", "waiting for setup snapshot")
                    ]
            self.note_deadline(diff.get("currentState"), frame.t, replaying)
            self.last_log_id = max([self.last_log_id, *(int(k) for k in logs)])
            # The 64 comes in the same update as the discards it chose.
            self.timer_discarded = any(
                isinstance(entry, dict)
                and (entry.get("text") or {}).get("type") == C.LOG_TIMER_DISCARDED
                and entry["text"].get("playerColor") == self.live.conv.you
                for entry in logs.values()
            )
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
        not thrown), resend the move: it can't happen twice. A new account's
        session can reconnect every few seconds (seen live: hersh3499, 20
        reconnects in one game, two of them on the same settlement), so a move
        is resent up to MAX_RECONNECT_RETRIES times, each after a snapshot
        shows it didn't land. If the game moved on, catch up from the
        snapshot, check the move as usual and play on. Reconnects mid-move
        stopped a casual bot twice in 15 minutes (seen live: a roll, then a
        road)."""
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
        if self.retry_count >= MAX_RECONNECT_RETRIES:
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
        if expected["type"] == "DISCARD_RESOURCE":
            # Earlier-seat discards can be inserted before the opponent's
            # already-recorded discards, so the old log length misses them.
            phase = self.live.conv.discard_phase
            if phase is not None:
                before = min(before, phase)
        mine = [
            action_from_json(entry[0])
            for entry in self.live.record["log"][before:]
            if entry[0][0] == self.live.bot.value
        ]
        if not mine:
            # The update was someone else's (e.g. the opponent's discard): the
            # move sent is still expected. Forgetting it, the bot thought again
            # and sent a second discard (seen live: bot5).
            return []
        self.expect = None
        self.pending_sends.clear()
        self.sent_raws = []
        if expected["type"] == "DISCARD_RESOURCE":
            # colonist logs a 7's discards together, in its own order.
            sent = sorted(m["resource"] for m in plan)
            done = sorted(
                self.live.describe(a)["resource"]
                for a in mine
                if a.action_type == ActionType.DISCARD_RESOURCE
            )
            if sent != done and self.timer_discarded:
                # The bot was too slow (it waits for an earlier seat's
                # discard): colonist's timer chose its cards, and the game
                # goes on from them.
                return [
                    self.status(
                        "following",
                        f"colonist's timer discarded {done} for the bot",
                    )
                ]
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
                if now - sent_at < ACK_SECONDS:
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

    def note_deadline(self, current, t, replay=False):
        """colonist's currentState gives each decision's time: startTime (ms)
        and allocatedTime (s). A 7's discards get 20 s (seen live), then
        colonist's timer picks the cards. A replayed frame (the page resending
        its game to a new connection) arrived at t, not now: the page runs on
        the bridge's machine, so t is on our clock."""
        if not isinstance(current, dict):
            return
        start, allocated = current.get("startTime"), current.get("allocatedTime")
        if isinstance(start, (int, float)):
            # colonist's clock in ours: when the frame came, less how long
            # before that it started.
            lag = max(0.0, (t - start) / 1000) if t else 0.0
            arrived = t / 1000 if replay and t else time.time()
            self.timer_start = arrived - lag
        if isinstance(allocated, (int, float)):
            # A build, buy or trade adds 5 s to the turn: the allowance grows
            # with no new start time.
            self.timer_allocated = allocated
        if self.timer_start is not None and self.timer_allocated is not None:
            self.deadline = self.timer_start + self.timer_allocated

    def time_left(self):
        """Seconds before colonist's timer acts on the current decision, or
        None before any timer has been seen."""
        return None if self.deadline is None else self.deadline - time.time()

    def waiting_to_discard(self):
        """The bot must discard for a 7, but the simulator wants the earlier
        seat's discard first (colonist takes both at once)."""
        live = self.live
        if live is None or live.error or live.over or live.game is None:
            return False
        state = live.game.state
        return bool(
            live.bot is not None
            and state.is_discarding
            and state.current_color() != live.bot
            and state.discard_counts[state.color_to_index[live.bot]] > 0
            and live.game.winning_color() is None
        )

    def plan_discard_early(self):
        """Plan the bot's discard now, on a guess of the earlier seat's
        discard (the net's choice), so a slow opponent can't leave the bot
        without time: colonist's timer would pick its cards at random."""
        live = self.live
        turn = live.conv.completed
        if self.early_discard and self.early_discard["turn"] == turn:
            return []
        start = time.time()
        g = live.game.copy()
        while (
            g.state.is_discarding
            and g.state.current_color() != live.bot
            and g.winning_color() is None
        ):
            actions = g.playable_actions
            g.execute(
                actions[int(np.argmax(self.opponent_prior(g)))], validate_action=False
            )
        if not g.state.is_discarding or g.state.current_color() != live.bot:
            return []
        self.budget = think_budget(g, self.time_left(), self.simulations)
        plan = [live.describe(a) for a in self.plan_discards(g)]
        seconds = time.time() - start
        self.early_discard = {
            "turn": turn,
            "plan": plan,
            "seconds": seconds,
            "sent": False,
        }
        cards = ", ".join(m["label"].removeprefix("discard ") for m in plan)
        return [
            self.status(
                "following",
                f"planned discard {cards} before the opponent's ({seconds:.1f} s)",
            )
        ]

    def opponent_prior(self, game):
        """The opponent's prior over their legal moves in `game`: the net's,
        or its mixture with the opponent model, as the search uses."""
        color = game.state.current_color()
        legal = E.legal_indices(game.playable_actions)
        priors, _ = self.net.evaluate(game, color, legal)
        if self.opponent_model is None or self.opponent_mix <= 0:
            return priors
        human, _ = self.opponent_model.evaluate(game, color, legal)
        return (1 - self.opponent_mix) * priors + self.opponent_mix * human

    def early_discard_due(self):
        """Send the planned discard when the opponent hasn't discarded and
        colonist's timer is about to."""
        early = self.early_discard
        if (
            not self.play
            or early is None
            or early["sent"]
            or self.expect is not None
            or self.deadline is None
            or time.time() < self.deadline - EARLY_DISCARD_MARGIN
            or early["turn"] != self.live.conv.completed
            or not self.waiting_to_discard()
        ):
            return []
        early["sent"] = True
        replies = self.send_plan(early["plan"], game_phase(self.live.game))
        return [
            self.status("following", "the opponent hasn't discarded: sent the plan")
        ] + replies

    def use_early_discard(self):
        """The opponent has discarded and it is the bot's turn to: the plan
        made earlier, when there isn't time to search the real position."""
        early = self.early_discard
        if (
            early is None
            or early["sent"]
            or early["turn"] != self.live.conv.completed
            or self.deadline is None
        ):
            return None
        left = self.deadline - time.time() - EARLY_DISCARD_MARGIN
        if left > 1.5 * early["seconds"] + 1:
            return None
        early["sent"] = True
        return early["plan"]

    def send_plan(self, plan, phase):
        """Encode and send a plan; it is then expected to show up."""
        live = self.live
        context = {"channel": self.channel, "creator": live.conv.you, "phase": phase}
        self.sent_raws = self.encode(plan, context)
        replies = self.send_replies(self.sent_raws)
        self.expect = (live.position_key, plan, time.time())
        self.since_sent = []
        return replies

    def think(self):
        live = self.live
        if self.play and self.expect is None and self.waiting_to_discard():
            return self.plan_discard_early()
        early = self.early_discard
        if (
            early
            and early["sent"]
            and early["turn"] == live.conv.completed
            and live.game is not None
            and live.game.state.is_discarding
        ):
            return []  # the planned discard is already sent for this 7
        if self.expect is not None:
            # A sent move isn't confirmed yet. The opponent's discard logged
            # first still leaves the bot to move in the simulator, and a new
            # search sent a second discard (seen live: bot5, then bot4).
            return []
        if not live.bot_to_move or live.position_key == self.answered:
            return []
        self.answered = live.position_key
        self.retry_count = 0
        self.no_effect_retry = False
        self.sent_raws = []
        start = time.time()
        game = live.game
        self.budget = think_budget(game, self.time_left(), self.simulations)
        self.searched = 0
        early = (
            self.use_early_discard() if game.state.is_discarding and self.play else None
        )
        if early is not None:
            plan, options, value = early, [], 0.0
        else:
            move, options, value = self.choose(game)
            plan = [live.describe(move)]
            options = [
                {"move": live.describe(a), "share": round(s, 3)} for a, s in options
            ]
            if move.action_type == ActionType.DISCARD_RESOURCE:
                # colonist takes all discards at once; the simulator one at a time.
                plan = [live.describe(a) for a in self.plan_discards(game)]
        replies = []
        played = False
        if self.play:
            from catanatron.colonist.client import NotKnownYet

            try:
                replies.extend(self.send_plan(plan, game_phase(game)))
                played = True
            except NotKnownYet as e:
                replies.append(self.status("following", f"suggesting only: {e}"))
        move = {
            "kind": "move",
            "position": live.position_key,
            "move": plan[0],
            "plan": plan,
            "options": options,
            "win_chance": round((value + 1) / 2, 3),
            "ms": int((time.time() - start) * 1000),
            "simulations": self.searched,
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
            return self.net_choice(game)
        from catanatron.colonist.mcts import Search

        seconds, simulations = self.budget
        root_filter = None
        if self.judgment_filter:
            from catanatron.colonist.judgment_policy import filtered_root_actions

            root_filter = lambda copy, choices: filtered_root_actions(
                copy.state, choices
            )
        if self.endgame_filter:
            from catanatron.colonist.endgame_policy import endgame_root_actions

            previous = root_filter
            root_filter = lambda copy, choices: endgame_root_actions(
                copy, previous(copy, choices) if previous else choices
            )
        if self.robber_filter:
            from catanatron.colonist.robber_policy import robber_root_actions

            earlier = root_filter
            root_filter = lambda copy, choices: robber_root_actions(
                copy, earlier(copy, choices) if earlier else choices
            )
        search = Search(
            self.net,
            simulations or self.simulations,
            rng=self.rng,
            noise=False,
            opponent_model=self.opponent_model if self.opponent_mix > 0 else None,
            opponent_mix=self.opponent_mix,
            root_action_filter=root_filter,
        )
        try:
            action, root = search.run(game, time_limit=seconds)
        except ValueError as error:
            # The search redeals the opponent's hidden cards; a deal that gives
            # them 15 points leaves it no choice to make. Take the net's move.
            print(f"search failed, playing the net's move: {error}", file=sys.stderr)
            return self.net_choice(game)
        self.searched += search.used
        _, value = search.root_targets(root)
        # Show moves by the share of simulations they got: the search's
        # finalists, the move it plays among them.
        share = root.N / max(root.N.sum(), 1)
        key = E.action_key(action)
        chosen = next(i for i, a in enumerate(root.actions) if E.action_key(a) == key)
        order = [chosen] + [i for i in np.argsort(-share) if i != chosen][:2]
        move = next(a for a in actions if E.action_key(a) == key)
        return move, [(root.actions[i], float(share[i])) for i in order], value

    def net_choice(self, game):
        """choose() without search: the net's most likely move."""
        actions = game.playable_actions
        priors, value = self.net.evaluate(
            game, game.state.current_color(), E.legal_indices(actions)
        )
        order = np.argsort(-priors)[:3]
        return (
            actions[order[0]],
            [(actions[i], float(priors[i])) for i in order],
            value,
        )

    def plan_discards(self, game):
        """All of the bot's discards for this 7, chosen one by one on a copy;
        a time budget is shared between the cards."""
        g = game.copy()
        color = g.state.current_color()
        plan = []
        seconds, simulations = self.budget
        count = g.state.discard_counts[g.state.color_to_index[color]]
        if seconds is not None and count > 1:
            self.budget = (seconds / count, simulations)
        while (
            g.state.is_discarding
            and g.state.current_color() == color
            and g.winning_color() is None
        ):
            move, _, _ = self.choose(g)
            plan.append(move)
            g.execute(move, validate_action=False)
        self.budget = (seconds, simulations)
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
                        f"(win chance {reply['win_chance']:.0%}, {reply['ms']} ms, "
                        f"{reply.get('simulations', 0)} sims)",
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

    # No keepalive timeout: the page is on this machine, and a late pong is a
    # busy machine, not a dead page. With websockets' 20 s timeout, a 40 s
    # opening search on a 2-core VM shared by two bots (E80, Oct 21) dropped
    # the link 11 times in 18 games (once in the log before): the finished move was lost and searched
    # again with half the clock gone.
    async with serve(handler, host, port, max_size=2**24, ping_timeout=None):
        print(f"bridge listening on ws://{host}:{port}", flush=True)
        if info:
            print(f"  {describe_info(info)}", flush=True)
        await asyncio.Future()


def net_info(
    path,
    simulations,
    play,
    label=None,
    opponent_model=None,
    opponent_mix=0,
    judgment_filter=False,
    robber_filter=False,
):
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
    if judgment_filter:
        info["judgment_filter"] = "last_road_v1"
    if robber_filter:
        info["robber_filter"] = "own_pips_v1"
    try:
        with open(path, "rb") as f:
            info["sha256"] = hashlib.file_digest(f, "sha256").hexdigest()[:16]
    except OSError:
        pass
    if opponent_model and opponent_mix > 0:
        # Games played with an opponent model are told apart in captures.
        info["opponent_model"] = opponent_model
        info["opponent_mix"] = opponent_mix
        try:
            with open(opponent_model, "rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()[:16]
            info["opponent_sha256"] = digest
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
    opponent = ""
    if info.get("opponent_model"):
        opponent = (
            f", opponent model {info['opponent_model']} "
            f"at mix {info['opponent_mix']}"
        )
    if info.get("judgment_filter"):
        opponent += ", judgment road filter"
    if info.get("robber_filter"):
        opponent += ", robber own-pips filter"
    return (
        f"net {info['net']}{epoch}, {info['simulations']} simulations{opponent}, "
        f"{mode}"
    )


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
    parser.add_argument(
        "--opponent-model",
        metavar="PATH",
        help="a net trained on people's moves (opponent_model.py), mixed into "
        "the search's prior at the opponent's decisions",
    )
    parser.add_argument(
        "--opponent-mix",
        type=float,
        default=0.0,
        help="the opponent model's weight in that prior, 0 to 1 (default 0: off)",
    )
    parser.add_argument(
        "--judgment-filter",
        action="store_true",
        help="experimental last-road root filter; default off",
    )
    parser.add_argument(
        "--endgame-filter",
        action="store_true",
        help="experimental root filter toward a YoP/trade-funded build near the "
        "target; default off",
    )
    parser.add_argument(
        "--robber-filter",
        action="store_true",
        help="experimental root filter: no robber on your own pips when a clean "
        "hex blocks as much of the opponent's (or ROBBER_FILTER=1); default off",
    )
    args = parser.parse_args(argv)
    # One account's trial (E78) from its env file, like THINK_BUDGETS.
    args.robber_filter = args.robber_filter or os.environ.get("ROBBER_FILTER") == "1"
    if not 0 <= args.opponent_mix <= 1:
        parser.error("--opponent-mix must be between 0 and 1")
    if args.opponent_mix > 0 and not args.opponent_model:
        parser.error("--opponent-mix needs --opponent-model")
    if args.judgment_filter and args.simulations < 1:
        parser.error("--judgment-filter requires --simulations of at least 1")
    if args.endgame_filter and args.simulations < 1:
        parser.error("--endgame-filter requires --simulations of at least 1")
    if args.robber_filter and args.simulations < 1:
        parser.error("--robber-filter requires --simulations of at least 1")
    if (
        not args.spectator_state
        and args.save
        and os.path.basename(os.path.normpath(args.save)) == "ranked"
    ):
        # Also covers an already-running ranked runner when it starts a fresh
        # bridge for a newly promoted model.
        args.spectator_state = os.path.join("data", "ranked", "spectator-state.json")
    try:
        overrides = budgets_from_env(os.environ.get("THINK_BUDGETS"))
    except ValueError as error:
        parser.error(str(error))
    if overrides:
        BUDGETS.update(overrides)
        print(f"think budgets from THINK_BUDGETS: {overrides}", flush=True)
    from catanatron.colonist.net import Net

    net = Net.load(args.net)
    opponent_model = None
    if args.opponent_model and args.opponent_mix > 0:
        opponent_model = Net.load(args.opponent_model)
    info = net_info(
        args.net,
        args.simulations,
        args.play,
        args.label,
        args.opponent_model,
        args.opponent_mix,
        args.judgment_filter,
        args.robber_filter,
    )
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
                    opponent_model=opponent_model,
                    opponent_mix=args.opponent_mix if opponent_model else 0.0,
                    judgment_filter=args.judgment_filter,
                    endgame_filter=args.endgame_filter,
                    robber_filter=args.robber_filter,
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
