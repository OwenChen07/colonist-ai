import base64
import json

import msgpack

from catanatron.colonist.capture import load, main


def write_capture(path, frames):
    messages = [{"seq": 0, "t": 1000, "dir": "open", "socket": 1, "text": "wss://x"}]
    for i, (direction, obj) in enumerate(frames, start=1):
        raw = msgpack.packb(obj)
        messages.append(
            {
                "seq": i,
                "t": 1000 + i,
                "dir": direction,
                "socket": 1,
                "b64": base64.b64encode(raw).decode(),
            }
        )
    path.write_text(
        json.dumps(
            {
                "format": "colonist-ai.capture",
                "version": 1,
                "started": 1000,
                "url": "https://colonist.io/",
                "messages": messages,
            }
        )
    )


def test_load_decodes_frames(tmp_path):
    path = tmp_path / "c.json"
    write_capture(
        path,
        [
            ("in", {"id": "130", "data": {"type": 91, "payload": {"diff": {}}}}),
            ("out", {"id": "1", "data": {"type": 5}}),
        ],
    )
    frames = load(path)
    assert [f.dir for f in frames] == ["open", "in", "out"]
    assert frames[1].type == 91
    assert frames[1].data["data"]["payload"] == {"diff": {}}


def test_cli_dump(tmp_path, capsys):
    path, out = tmp_path / "c.json", tmp_path / "out.jsonl"
    write_capture(path, [("in", {"id": "130", "data": {"type": 4}})])
    assert main([str(path), "--dump", str(out)]) == 0
    assert "socket 1: wss://x" in capsys.readouterr().out
    lines = [json.loads(l) for l in out.read_text().splitlines()]
    assert lines[1]["type"] == 4
