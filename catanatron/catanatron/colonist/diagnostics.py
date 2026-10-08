"""Measure resource access and missed building opportunities in 1v1 games.

An opportunity is counted once per action, immediately before that action.
"One short" means exactly one card below the settlement cost, with an
unoccupied connected settlement site and a settlement piece available.
"Missing opening resource" means neither starting settlement touches a tile
producing it; a separate count excludes openings with any starting port.
"""

import argparse
import glob
import hashlib
import json

from catanatron.models.enums import (
    BRICK,
    ORE,
    RESOURCES,
    SHEEP,
    WHEAT,
    WOOD,
    ActionPrompt,
    ActionType,
    SETTLEMENT,
)
from catanatron.observer import GameObserver
from catanatron.state_functions import get_player_freqdeck, player_key


def empty_counts():
    return {
        "opening_missing_tile": 0,
        "opening_missing_tile_or_port": 0,
        "opening_missing_sheep": 0,
        "roads_one_short": 0,
        "wood_trades_one_short": 0,
        "ore_for_wood_missing_sheep": 0,
        "ore_for_wood_without_sheep": 0,
        "end_turn_city_available": 0,
        "end_turn_settlement_available": 0,
        "dev_buys": 0,
        "dev_buys_city_available": 0,
        "dev_buys_one_ore_short_city": 0,
        "roads": 0,
        "settlements": 0,
        "cities": 0,
    }


class Diagnostics(GameObserver):
    def before(self, game):
        self.counts = {color: empty_counts() for color in game.state.colors}
        self.opening_done = False

    def _opening(self, game):
        state = game.state
        for color in state.colors:
            nodes = state.buildings_by_color[color][SETTLEMENT]
            produced = {
                tile.resource
                for node in nodes
                for tile in state.board.map.adjacent_tiles[node]
                if tile.resource is not None
            }
            missing = set(RESOURCES) - produced
            ports = state.board.get_player_port_resources(color)
            row = self.counts[color]
            row["opening_missing_tile"] = int(bool(missing))
            row["opening_missing_tile_or_port"] = int(bool(missing) and not ports)
            row["opening_missing_sheep"] = int(SHEEP in missing)
        self.opening_done = True

    def step(self, game, action):
        state = game.state
        if not self.opening_done and state.current_prompt == ActionPrompt.PLAY_TURN:
            self._opening(game)
        color = action.color
        row = self.counts[color]
        kind = action.action_type
        if kind == ActionType.BUILD_ROAD:
            row["roads"] += 1
        elif kind == ActionType.BUILD_SETTLEMENT:
            row["settlements"] += 1
        elif kind == ActionType.BUILD_CITY:
            row["cities"] += 1
        elif kind == ActionType.BUY_DEVELOPMENT_CARD:
            row["dev_buys"] += 1
            legal = {a.action_type for a in game.playable_actions}
            if ActionType.BUILD_CITY in legal:
                row["dev_buys_city_available"] += 1
            else:
                key = player_key(state, color)
                hand = dict(zip(RESOURCES, get_player_freqdeck(state, color)))
                if (
                    state.current_prompt == ActionPrompt.PLAY_TURN
                    and state.player_state[f"{key}_CITIES_AVAILABLE"] > 0
                    and state.buildings_by_color[color][SETTLEMENT]
                    and hand[ORE] == 2
                    and hand[WHEAT] >= 2
                ):
                    row["dev_buys_one_ore_short_city"] += 1

        if state.current_prompt != ActionPrompt.PLAY_TURN or state.is_road_building:
            return
        if kind in (ActionType.BUILD_ROAD, ActionType.MARITIME_TRADE):
            key = player_key(state, color)
            hand = dict(zip(RESOURCES, get_player_freqdeck(state, color)))
            missing = [r for r in (WOOD, BRICK, SHEEP, WHEAT) if hand[r] < 1]
            settlement_site = bool(state.board.buildable_node_ids(color))
            one_short = (
                state.player_state[f"{key}_SETTLEMENTS_AVAILABLE"] > 0
                and settlement_site
                and len(missing) == 1
            )
            ore_for_wood = (
                kind == ActionType.MARITIME_TRADE
                and action.value[-1] == WOOD
                and tuple(action.value[:4]) == (ORE,) * 4
            )
            if ore_for_wood and SHEEP in missing and settlement_site:
                row["ore_for_wood_without_sheep"] += 1
            if one_short and kind == ActionType.BUILD_ROAD:
                row["roads_one_short"] += 1
            if (
                one_short
                and kind == ActionType.MARITIME_TRADE
                and action.value[-1] == WOOD
            ):
                row["wood_trades_one_short"] += 1
                if missing[0] == SHEEP and ore_for_wood:
                    row["ore_for_wood_missing_sheep"] += 1
        if kind == ActionType.END_TURN:
            legal = {a.action_type for a in game.playable_actions}
            for building, field in (
                (ActionType.BUILD_CITY, "end_turn_city_available"),
                (ActionType.BUILD_SETTLEMENT, "end_turn_settlement_available"),
            ):
                if building in legal:
                    row[field] += 1

    def after(self, game):
        if (
            not self.opening_done
            and game.state.current_prompt == ActionPrompt.PLAY_TURN
        ):
            self._opening(game)

    def rows(self, winner=None, source="simulated"):
        return [
            {
                "source": source,
                "color": color.value,
                "score": None if winner is None else float(winner == color),
                **self.counts[color],
            }
            for color in self.counts
        ]


def summary(rows):
    """Aggregate per-seat counts and conditional win rates."""
    out = {"seats": len(rows)}
    if not rows:
        return out
    fields = empty_counts()
    for field in fields:
        exposed = [r for r in rows if r[field]]
        scored = [r["score"] for r in exposed if r["score"] is not None]
        unexposed = [
            r["score"] for r in rows if not r[field] and r["score"] is not None
        ]
        out[field] = {
            "events": sum(r[field] for r in rows),
            "seats": len(exposed),
            "wins_per_scored_seat": sum(scored) / len(scored) if scored else None,
            "scored_seats": len(scored),
            "wins_without_event": (
                sum(unexposed) / len(unexposed) if unexposed else None
            ),
            "scored_seats_without_event": len(unexposed),
        }
    out["overall_win_rate"] = (
        sum(r["score"] for r in rows if r["score"] is not None)
        / sum(r["score"] is not None for r in rows)
        if any(r["score"] is not None for r in rows)
        else None
    )
    return out


def record_rows(record, name="record"):
    from catanatron.colonist.replay import replay

    observer = Diagnostics()
    report = replay(record, name=name, observer=observer)
    if not report.ok:
        return report, []
    from catanatron.models.player import Color

    winner = Color[report.winner] if report.winner else None
    return report, observer.rows(winner, "live")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--a", default=None, help="bot A, e.g. Z:model.npz:100")
    parser.add_argument("--b", default="AB:2", help="opponent; set equal to A for self")
    parser.add_argument("--games", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument(
        "--captures", nargs="*", default=[], help="capture or record globs"
    )
    parser.add_argument("--json", dest="json_path", help="write aggregate results")
    args = parser.parse_args(argv)
    if args.a is None and not args.captures:
        parser.error("provide --a and/or --captures")
    results = {}
    if args.a:
        from catanatron.colonist.evaluate import match

        result = match(
            args.a,
            args.b,
            games=args.games,
            seed=args.seed,
            workers=args.workers,
            diagnostics=True,
        )
        rows = []
        for game in result.games:
            for color, counts in game.diagnostics.items():
                score = game.score if color == "RED" else 1 - game.score
                rows.append(
                    {"source": "simulated", "color": color, "score": score, **counts}
                )
        results["simulated"] = {
            "match": result.summary(),
            "a": summary([r for r in rows if r["color"] == "RED"]),
            "b": summary([r for r in rows if r["color"] == "BLUE"]),
        }
    paths = sorted({p for pattern in args.captures for p in glob.glob(pattern)})
    if args.captures and not paths:
        parser.error("no captures matched")
    if paths:
        from catanatron.colonist.capture import load as load_capture
        from catanatron.colonist.convert import convert
        from catanatron.colonist.record import load as load_record

        rows, skipped = [], []
        candidates = {}
        duplicates = 0
        for path in paths:
            try:
                with open(path) as stream:
                    file_format = json.load(stream).get("format")
                if file_format == "colonist-ai.game-record":
                    record = load_record(path)
                elif file_format == "colonist-ai.capture":
                    record = convert(load_capture(path)).record
                else:
                    raise ValueError(f"unsupported format {file_format!r}")
                identity = record.get("source", {}).get("colonist_game")
                if identity is None:
                    opening = [entry[0] for entry in record["log"][:8]]
                    identity = hashlib.sha256(
                        json.dumps([record["map"], opening], sort_keys=True).encode()
                    ).hexdigest()
                report, found = record_rows(record, path)
                if not report.ok:
                    skipped.append(
                        {"path": path, "reason": "replay verification failed"}
                    )
                    continue
                if identity in candidates:
                    duplicates += 1
                old = candidates.get(identity)
                if old is None or (bool(record.get("winner")), len(record["log"])) > (
                    bool(old[1].get("winner")),
                    len(old[1]["log"]),
                ):
                    candidates[identity] = (path, record, report, found)
            except (ValueError, KeyError, StopIteration, TypeError) as exc:
                skipped.append({"path": path, "reason": type(exc).__name__})
        completed = 0
        for path, record, report, found in candidates.values():
            completed += int(report.winner is not None)
            for row in found:
                row["you"] = bool(
                    record.get("players", {}).get(row["color"], {}).get("you")
                )
            rows.extend(found)
        results["live"] = {
            "all_seats": summary(rows),
            "recording_account": summary([r for r in rows if r["you"]]),
            "opponents": summary([r for r in rows if not r["you"]]),
            "completed_games": completed,
            "duplicate_captures": duplicates,
            "skipped": skipped,
        }
    output = json.dumps(results, indent=2)
    print(output)
    if args.json_path:
        with open(args.json_path, "w") as stream:
            stream.write(output + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
