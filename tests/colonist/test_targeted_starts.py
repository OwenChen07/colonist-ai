"""Paired tactical starts retain source ordering and only narrow valid cuts."""

import gzip
import json

from catanatron.colonist import targeted_starts as T
from catanatron.colonist.starts import load, start_game

from .test_convert import FULL_GAME
from catanatron.colonist.capture import load as load_capture
from catanatron.colonist.convert import convert
from catanatron.colonist.starts import start_entry


def test_selected_is_repeatable_and_bounded():
    assert all(not T.selected(i, 0, "x") for i in range(20))
    assert all(T.selected(i, 1, "x") for i in range(20))
    assert [T.selected(i, 0.4, "x") for i in range(20)] == [
        T.selected(i, 0.4, "x") for i in range(20)
    ]


def test_build_keeps_same_entries_and_replayable_cuts(tmp_path):
    entry = start_entry(convert(load_capture(FULL_GAME)).record)
    assert entry is not None
    source = tmp_path / "source.gz"
    target = tmp_path / "target.gz"
    with gzip.open(source, "wt") as stream:
        for _ in range(3):
            stream.write(json.dumps(entry) + "\n")
    counts = T.build(str(source), str(target), fraction=1)
    original = load(source)
    changed = load(target)
    assert counts["entries"] == 3
    assert len(changed) == len(original)
    for before, after in zip(original, changed):
        assert {k: v for k, v in before.items() if k != "cuts"} == {
            k: v for k, v in after.items() if k not in ("cuts", "source_cut_count")
        }
        assert after["cuts"]
        assert set(after["cuts"]) <= set(before["cuts"])
        assert after.get("source_cut_count") == (
            len(before["cuts"]) if after["cuts"] != before["cuts"] else None
        )
        assert start_game(after, 7).state.num_turns >= 16


def test_narrowed_cuts_preserve_game_random_stream_for_same_position():
    entry = start_entry(convert(load_capture(FULL_GAME)).record)
    assert entry is not None and len(entry["cuts"]) > 1
    narrowed = {
        **entry,
        "cuts": [entry["cuts"][0]],
        "source_cut_count": len(entry["cuts"]),
    }
    for seed in range(100):
        original_game = start_game(entry, seed)
        target_game = start_game(narrowed, seed)
        if original_game.state.num_turns == target_game.state.num_turns:
            assert original_game.random.getstate() == target_game.random.getstate()
            return
    assert False, "no seed selected the common cut"
