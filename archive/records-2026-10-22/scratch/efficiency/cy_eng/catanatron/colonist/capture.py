"""Captures from the recorder extension (recorder/), decoded.

A capture is every WebSocket frame colonist.io sent and received on one page
load, stored raw. colonist's frames are MessagePack; this turns them back into
Python objects. Turning those into game records comes next, once the message
format is mapped from real games.

    python -m catanatron.colonist.capture capture.json            # summary
    python -m catanatron.colonist.capture capture.json --dump out.jsonl

Requires the `colonist` extra (msgpack).
"""

import argparse
import base64
import json
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any, List, Optional

import msgpack

FORMAT = "colonist-ai.capture"


@dataclass
class Frame:
    seq: int
    t: int  # ms since epoch
    dir: str  # "in" | "out" | "open" | "close"
    socket: int
    data: Any  # decoded MessagePack, text, or raw bytes if undecodable
    raw_size: int

    @property
    def type(self) -> Optional[Any]:
        """colonist's message type: data.type, when the frame has one."""
        if isinstance(self.data, dict) and isinstance(self.data.get("data"), dict):
            return self.data["data"].get("type")
        return None


def load(path) -> List[Frame]:
    with open(path) as f:
        doc = json.load(f)
    if doc.get("format") != FORMAT:
        raise ValueError(f"not a capture (format={doc.get('format')!r})")
    return [decode(m) for m in doc["messages"]]


def unpack_client(raw):
    """A message colonist's client sends: two header bytes (kind, subkind), a
    length-prefixed channel name (the game's id, "lobby", or empty), then
    MessagePack. Returns {"header": [kind, subkind], "channel": ..., "body": ...},
    or None if the bytes aren't in that layout."""
    if len(raw) < 3 or len(raw) < 3 + raw[2]:
        return None
    n = raw[2]
    try:
        channel = raw[3 : 3 + n].decode()
        body = msgpack.unpackb(raw[3 + n :], raw=False, strict_map_key=False)
    except Exception:
        return None
    return {"header": [raw[0], raw[1]], "channel": channel, "body": body}


def pack_client(header, channel, body):
    """The inverse of unpack_client."""
    name = channel.encode()
    return bytes([header[0], header[1], len(name)]) + name + msgpack.packb(body)


def decode(message) -> Frame:
    if "b64" in message:
        raw = base64.b64decode(message["b64"])
        try:
            data = msgpack.unpackb(raw, raw=False, strict_map_key=False)
        except Exception:
            # Messages the client sends carry a short header before the
            # MessagePack (see unpack_client).
            data = unpack_client(raw) if message.get("dir") == "out" else None
            if data is None:
                data = raw
        size = len(raw)
    else:
        data = message.get("text")
        size = len(data or "")
    return Frame(
        message["seq"], message["t"], message["dir"], message["socket"], data, size
    )


def summary(frames: List[Frame]) -> str:
    lines = []
    sockets = {f.socket: f.data for f in frames if f.dir == "open"}
    for sid, url in sockets.items():
        n = sum(1 for f in frames if f.socket == sid and f.dir in ("in", "out"))
        lines.append(f"socket {sid}: {url} ({n} frames)")
    by_type = Counter((f.dir, f.type) for f in frames if f.dir in ("in", "out"))
    sizes = Counter()
    for f in frames:
        if f.dir in ("in", "out"):
            sizes[(f.dir, f.type)] += f.raw_size
    lines.append("")
    lines.append(f"{'dir':4} {'type':>6} {'count':>6} {'bytes':>9}")
    for (d, t), n in sorted(by_type.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        lines.append(f"{d:4} {str(t):>6} {n:6} {sizes[(d, t)]:9}")
    if frames:
        secs = (frames[-1].t - frames[0].t) / 1000
        lines.append(f"\n{len(frames)} frames over {secs:.0f} s")
    return "\n".join(lines)


def _jsonable(x):
    if isinstance(x, msgpack.Timestamp):
        return (
            x.to_unix_nano() // 1_000_000
        )  # ms since epoch, like the capture's own times
    if isinstance(x, bytes):
        return {"bytes": base64.b64encode(x).decode()}
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path")
    parser.add_argument("--dump", help="write every decoded frame as JSON lines")
    args = parser.parse_args(argv)
    frames = load(args.path)
    print(summary(frames))
    if args.dump:
        with open(args.dump, "w") as f:
            for fr in frames:
                f.write(
                    json.dumps(
                        {
                            "seq": fr.seq,
                            "t": fr.t,
                            "dir": fr.dir,
                            "socket": fr.socket,
                            "type": fr.type,
                            "data": _jsonable(fr.data),
                        }
                    )
                    + "\n"
                )
        print(f"wrote {args.dump}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
