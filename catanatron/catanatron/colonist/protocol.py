"""What colonist's client sends for each move (PLAN.md, Phase 4).

    python -m catanatron.colonist.protocol captures/*.json -o protocol.json

The bot plays by sending colonist the same messages its own web client sends
(colonist allows bots). Captures from the recorder hold both directions, so
this pairs every message the recording player sent with the move it caused:
the next log entry, by that player, that the converter emitted after it.

Writes a summary per move type: how often it was seen, the shapes of the
messages sent just before it with how many instances each appeared in (the
move's own message is the shape found every time), and a few examples. Names,
chat and long strings that could be tokens are replaced before anything is
written, so the file can be shared.
"""

import argparse
import glob
import json
import sys
from collections import defaultdict

from catanatron.colonist import convert as C
from catanatron.colonist.capture import load

SCRUB_KEYS = (
    "username",
    "name",
    "message",
    "text",
    "token",
    "auth",
    "session",
    "email",
)


def scrub(x):
    """Replace anything that could identify a person; keep the structure."""
    if isinstance(x, dict):
        return {
            k: (
                "<scrubbed>"
                if any(s in str(k).lower() for s in SCRUB_KEYS)
                else scrub(v)
            )
            for k, v in x.items()
        }
    if isinstance(x, list):
        return [scrub(v) for v in x]
    if isinstance(x, str) and len(x) > 24:
        return "<long string>"
    if isinstance(x, (bytes, bytearray)):
        return f"<{len(x)} bytes>"
    return x


def shape(x):
    if isinstance(x, dict):
        return {k: shape(v) for k, v in x.items()}
    if isinstance(x, list):
        return [shape(x[0])] if x else []
    return type(x).__name__


def pair(frames):
    """[(messages sent since the previous update, [log entries by the sender
    in this update])]. The move's own message is among the candidates; which
    one shows up in the summary, as the shape every instance shares."""
    start = next((f for f in frames if f.dir == "in" and f.type == C.GAME_STATE), None)
    if start is None:
        return []
    conv = C._Converter(start.data["data"]["payload"], start)
    me = conv.seat.get(conv.you)
    log = conv.record["log"]
    pairs, waiting = [], []
    for f in frames:
        if f.seq < start.seq:
            continue
        if f.dir == "out":
            data = f.data
            if isinstance(data, dict) and "header" in data:
                # Only game messages (header 3, 1) can make a move; heartbeats
                # and lobby messages are left out. Keep the body.
                if data["header"] != [3, 1]:
                    continue
                data = data["body"]
            waiting.append(data)
            continue
        if f.dir != "in" or f.type != C.DIFF:
            continue
        before = len(log)
        try:
            conv.apply(f.data["data"]["payload"].get("diff", {}))
        except Exception:
            break
        mine = [entry for entry in log[before:] if entry[0][0] == me]
        if mine and waiting:
            pairs.append((waiting, mine))
            waiting = []
    if waiting:
        pairs.append((waiting, []))
    return pairs


def summarize(paths, examples=3):
    by_move = defaultdict(
        lambda: {"seen": 0, "shapes": defaultdict(int), "examples": []}
    )
    for path in paths:
        for sent, moves in pair(load(path)):
            kinds = "+".join(dict.fromkeys(m[0][1] for m in moves)) or "(no move)"
            entry = by_move[kinds]
            entry["seen"] += 1
            # in how many instances each shape appears (not how often)
            for key in {json.dumps(shape(m), sort_keys=True) for m in sent}:
                entry["shapes"][key] += 1
            if len(entry["examples"]) < examples:
                entry["examples"].append(
                    {"sent": [scrub(m) for m in sent], "caused": [m[0] for m in moves]}
                )
    out = {}
    for kinds, entry in sorted(by_move.items()):
        shapes = sorted(entry["shapes"].items(), key=lambda kv: -kv[1])
        out[kinds] = {
            "seen": entry["seen"],
            # A shape in (nearly) every instance is the move's own message.
            "message_shapes": [{"shape": json.loads(k), "in": n} for k, n in shapes],
            "examples": entry["examples"],
        }
    return {"moves": out}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", help="capture files or globs")
    parser.add_argument("-o", "--out", default="protocol.json")
    parser.add_argument("--examples", type=int, default=3)
    args = parser.parse_args(argv)
    paths = sorted(p for pattern in args.paths for p in glob.glob(pattern))
    if not paths:
        print("no captures found", file=sys.stderr)
        return 1
    summary = summarize(paths, args.examples)
    with open(args.out, "w") as f:
        json.dump(summary, f, indent=1, default=str)
    print(f"{len(paths)} captures -> {args.out}")
    for kinds, entry in summary["moves"].items():
        print(f"  {kinds}: {entry['seen']} sent")
    missing = [
        t
        for t in (
            "ROLL",
            "END_TURN",
            "BUILD_ROAD",
            "BUILD_SETTLEMENT",
            "BUILD_CITY",
            "BUY_DEVELOPMENT_CARD",
            "PLAY_KNIGHT_CARD",
            "PLAY_YEAR_OF_PLENTY",
            "PLAY_MONOPOLY",
            "PLAY_ROAD_BUILDING",
            "MOVE_ROBBER",
            "DISCARD_RESOURCE",
            "MARITIME_TRADE",
        )
        if not any(t in k.split("+") for k in summary["moves"])
    ]
    if missing:
        print("  not seen yet (play a game that has them): " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
