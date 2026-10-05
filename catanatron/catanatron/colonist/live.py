"""Follow a colonist game as it happens (PLAN.md, Phase 4).

The bridge feeds every message colonist sends into a LiveGame. It runs them
through the same converter that turns recorded games into records (convert.py)
and keeps a simulator position that matches colonist's after every update:

  - The position is rebuilt from the game log after each update (a few
    milliseconds), with the opponent's hidden development card draws filled
    in the way the replay does. Their exact identity doesn't matter to the AI:
    its search deals the opponent's hidden cards again anyway.
  - colonist's reported card counts and points are checked against the
    simulator after every update. On any disagreement, or anything the
    converter can't map, the game is marked out of sync and the bot must stop:
    it never plays on a position it isn't sure of.

It also translates simulator moves back into colonist's terms (corner, edge
and tile indexes, resource numbers), for whatever makes the move on the page.
"""

from types import SimpleNamespace
from typing import Optional

from catanatron.colonist import convert as C
from catanatron.colonist.record import resolve_hidden_draws, snapshot
from catanatron.colonist.replay import decision_of, game_for_record
from catanatron.models.enums import ActionRecord, ActionType
from catanatron.models.player import Color
from catanatron.serialization import _tupled, action_from_json

RESOURCE_ID = {name: i for i, name in C.RESOURCE.items()}  # sim name -> colonist id


class OutOfSync(Exception):
    """The simulator and colonist disagree; the bot must stop."""


class LiveGame:
    def __init__(self, start_payload, t=0):
        self.conv = C._Converter(start_payload, SimpleNamespace(t=t))
        self.record = self.conv.record
        you = self.conv.you
        self.bot = Color[self.conv.seat[you]] if you in self.conv.seat else None
        self.error: Optional[str] = None
        self.over = False
        self.game = None
        self._index_board()
        self._rebuild()

    # ----- updates -----
    def apply(self, frame):
        """Feed one decoded message (capture.Frame). Returns True if the
        position may have changed."""
        if self.error or frame.dir != "in":
            return False
        try:
            if frame.type == C.DIFF:
                self.conv.apply(frame.data["data"]["payload"].get("diff", {}))
            elif frame.type == C.GAME_OVER:
                self.over = True
                return True
            else:
                return False
        except C._Unsupported as e:
            self.error = f"colonist did something the converter can't map: {e}"
            return True
        except Exception as e:  # a converter bug: stop rather than guess
            self.error = f"converter failed: {e!r}"
            return True
        self._rebuild()
        return True

    def _rebuild(self):
        try:
            self.game = self._replay()
        except OutOfSync as e:
            self.error = str(e)

    def _replay(self):
        log = resolve_hidden_draws(self.record)
        checks = {}
        for check in self.record.get("checks", []):
            checks.setdefault(check["at"], []).append(check)
        game = game_for_record(self.record)
        self._check(game, checks.get(0, []))
        for i, (payload, result) in enumerate(log):
            action = action_from_json(payload)
            decision = decision_of(action, game.playable_actions)
            if decision not in game.playable_actions:
                raise OutOfSync(
                    f"log entry {i} ({payload}) isn't legal in the simulator"
                )
            game.execute(
                decision,
                validate_action=False,
                action_record=ActionRecord(action, _tupled(result)),
            )
            self._check(game, checks.get(i + 1, []))
        return game

    def _check(self, game, checks):
        for check in checks:
            actual = snapshot(game.state, Color[check["color"]], check["at"])
            for key, expected in check.items():
                if key not in ("at", "color") and actual.get(key) != expected:
                    raise OutOfSync(
                        f"after {check['at']} log entries, {check['color']} {key}: "
                        f"colonist says {expected}, simulator has {actual.get(key)}"
                    )

    # ----- whose move -----
    @property
    def bot_to_move(self) -> bool:
        """The bot has a decision to make right now, as far as the simulator
        knows (colonist may still be animating the last move)."""
        if self.error or self.over or self.game is None or self.bot is None:
            return False
        if self.game.winning_color() is not None:
            return False
        return self.game.state.current_color() == self.bot

    @property
    def position_key(self):
        """Changes whenever the position does (to avoid thinking twice)."""
        return len(self.record["log"])

    # ----- moves in colonist's terms -----
    def _index_board(self):
        m = self.conv.catan_map
        self.corner_of_node = {
            C._corner_node(m, c): int(i) for i, c in self.conv.corners.items()
        }
        self.edge_of_nodes = {
            frozenset(C._edge_nodes(m, e)): int(i) for i, e in self.conv.edges.items()
        }
        self.tile_of_cube = {
            C.cube(h["x"], h["y"]): int(i) for i, h in self.conv.hexes.items()
        }
        self.colonist_color = {Color[v]: k for k, v in self.conv.seat.items()}

    def describe(self, action):
        """A simulator move as colonist's UI sees it: a dict with "type" and
        corner/edge/tile indexes and resource ids, plus a readable "label"."""
        t, v = action.action_type, action.value
        out = {"type": t.value}
        if t in (ActionType.BUILD_SETTLEMENT, ActionType.BUILD_CITY):
            out["corner"] = self.corner_of_node[v]
            label = f"{'settlement' if t == ActionType.BUILD_SETTLEMENT else 'city'} at corner {out['corner']}"
        elif t == ActionType.BUILD_ROAD:
            out["edge"] = self.edge_of_nodes[frozenset(v)]
            label = f"road on edge {out['edge']}"
        elif t == ActionType.MOVE_ROBBER:
            coordinate, victim = v
            out["tile"] = self.tile_of_cube[coordinate]
            out["victim"] = self.colonist_color.get(victim) if victim else None
            label = f"robber to tile {out['tile']}" + (
                f", rob {victim.value}" if victim else ""
            )
        elif t == ActionType.DISCARD_RESOURCE:
            out["resource"] = RESOURCE_ID[v]
            label = f"discard {v}"
        elif t == ActionType.MARITIME_TRADE:
            give = [r for r in v[:4] if r]
            out["give"] = RESOURCE_ID[give[0]]
            out["give_count"] = len(give)
            out["get"] = RESOURCE_ID[v[4]]
            label = f"trade {len(give)} {give[0]} for 1 {v[4]}"
        elif t == ActionType.PLAY_YEAR_OF_PLENTY:
            out["resources"] = [RESOURCE_ID[r] for r in v]
            label = "year of plenty: " + " + ".join(v)
        elif t == ActionType.PLAY_MONOPOLY:
            out["resource"] = RESOURCE_ID[v]
            label = f"monopoly on {v}"
        else:
            label = t.value.lower().replace("_", " ")
        out["label"] = label
        return out
