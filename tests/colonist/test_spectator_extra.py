import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from catanatron.colonist import spectator_extra
from catanatron.colonist.spectator_publish import snapshot

SITE = Path(__file__).resolve().parents[2] / "spectator-site"
NOW = dt.datetime(2026, 10, 21, 1, 30, tzinfo=dt.timezone.utc)


def _verdict(at, replaced, new, current):
    return {
        "at": at,
        "init": "runs/g1/best.npz",
        "replaced": replaced,
        "scores": {
            "new": {"rows": 35786, "accuracy": new[0], "cross_entropy": new[1]},
            "current": {"rows": 35786, "accuracy": current[0], "cross_entropy": current[1]},
        },
    }


def _human(folder, history, latest):
    folder.mkdir(parents=True)
    (folder / "refresh-history.jsonl").write_text(
        "".join(json.dumps(v) + "\n" for v in history) + "not json\n"
    )
    (folder / "refresh.json").write_text(json.dumps(latest))


def _e54(folder):
    run = folder / "run1"
    run.mkdir(parents=True)
    (run / "loop.txt").write_text(
        "[Oct 19, 4:24 PM] hosts free: out of workers.env and idle\n"
        "[Oct 19, 4:28 PM] iter 1: played 20000 games in 242 s\n"
        "[Oct 19, 4:38 PM] iter 1: trained in 605 s\n"
        "[Oct 20, 3:27 PM] iter 2: played 20000 games in 517 s\n"
    )
    (run / "eval.txt").write_text(
        "10\tZ:/x/eval.npz:0 vs Z:/x/pool/_runs_g1_nets_round_073.npz:0: 55.6% ± 3.0% "
        "for Z:/x/eval.npz:0 (556 wins, 444 losses, 0 no winner; 1000 games, 44 s; "
        "first seat won 54.2%); thinking 1.7 vs 1.7 ms per move\n"
    )
    (folder / "searched.txt").write_text(
        "[Oct 20, 3:43 PM] e54i30-vs-r152: Z:/x/run1/iter-30/net.npz:200 vs "
        "Z:/x/runs/g1/nets/round_152.npz:200, 2000 games, seed 7470000000\n"
        "[Oct 20, 4:00 PM]   e54i30-vs-r152 100 games: 62.00% ± 9.10%\n"
        "[Oct 20, 4:00 PM] e54i30-vs-r152: Z:/x/run1/iter-30/net.npz:200 vs "
        "Z:/x/runs/g1/nets/round_152.npz:200: 56.1% ± 2.0% for Z:/x/run1/iter-30/"
        "net.npz:200 (1122 wins, 878 losses, 0 no winner; 2000 games, 998 s; first "
        "seat won 52.0%); thinking 258.5 vs 257.2 ms per move\n"
        "[Oct 20, 5:35 PM] POOLED e54i30 vs r152 @200: 4462-3538 over 8000 (seeds "
        "7470000000 x2000, 7480000000 x3000) = 55.8% ± 1.0%; first seat 52.2%\n"
    )


def test_people_model_keeps_the_last_refreshes_and_adds_the_latest(tmp_path):
    old = [
        _verdict(f"2026-10-20T{h:02d}:46:00+00:00", h % 2 == 0, (0.7, 0.82), (0.69, 0.83))
        for h in range(3)
    ]
    latest = _verdict("2026-10-20T23:46:41+00:00", False, (0.705, 0.8178), (0.706, 0.8177))
    _human(tmp_path / "human", old, latest)
    rows = spectator_extra.people_model(tmp_path / "human", limit=3)["refreshes"]
    assert [r["at"][11:13] for r in rows] == ["01", "02", "23"]
    assert rows[-1] == {
        "at": "2026-10-20T23:46:41+00:00",
        "replaced": False,
        "rows": 35786,
        "newAccuracy": 0.705,
        "newCrossEntropy": 0.8178,
        "currentAccuracy": 0.706,
        "currentCrossEntropy": 0.8177,
    }
    # The latest verdict is in the history once it was appended there.
    _human(tmp_path / "again", [latest], latest)
    assert len(spectator_extra.people_model(tmp_path / "again")["refreshes"]) == 1
    assert spectator_extra.people_model(tmp_path / "none") is None


def test_e54_iterations_evaluations_and_gates(tmp_path):
    _e54(tmp_path / "e54")
    run = spectator_extra.e54(tmp_path / "e54", now=NOW)
    assert run["iterations"] == [
        {"iter": 1, "games": 20000, "at": "2026-10-19T20:28:00+00:00"},
        {"iter": 2, "games": 20000, "at": "2026-10-20T19:27:00+00:00"},
    ]
    assert run["evals"] == [
        {"iter": 10, "opponent": 73, "games": 1000, "score": 0.556, "margin": 0.03}
    ]
    single, pooled = run["gates"]
    assert single == {
        "name": "e54i30-vs-r152",
        "iter": 30,
        "opponent": 152,
        "games": 2000,
        "score": 0.561,
        "margin": 0.02,
        "pooled": False,
        "at": "2026-10-20T20:00:00+00:00",
    }
    assert pooled["pooled"] and pooled["games"] == 8000 and pooled["score"] == 0.558
    assert spectator_extra.e54(tmp_path / "missing", now=NOW) is None


def _body(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _human(tmp_path / "human", [], _verdict("2026-10-20T23:46:41+00:00", True, (0.7, 0.8), (0.7, 0.8)))
    _e54(tmp_path / "e54")
    spectator_extra.main(
        [
            "--out",
            str(data / "spectator-extra.json"),
            "--human",
            str(tmp_path / "human"),
            "--e54",
            str(tmp_path / "e54"),
        ]
    )
    return snapshot(data, {}, "vm1", run=tmp_path / "no-run")


def test_snapshot_sends_the_side_runs_only_when_present(tmp_path):
    body = _body(tmp_path)
    assert body["peopleModel"]["refreshes"][0]["replaced"] is True
    assert body["e54"]["gates"][-1]["pooled"] is True
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not {"peopleModel", "e54"} & set(snapshot(empty, {}, "vm1"))


def test_an_older_site_still_gets_the_bots(tmp_path, monkeypatch):
    import urllib.error

    from catanatron.colonist import spectator_publish

    _body(tmp_path)
    (tmp_path / "key").write_text("k")
    sent = []

    def publish(site, key, body):
        sent.append(sorted(body))
        if "e54" in body:
            raise urllib.error.HTTPError(site, 400, "Invalid status", {}, None)

    monkeypatch.setattr(spectator_publish, "publish", publish)
    spectator_publish.main(
        ["--data", str(tmp_path / "data"), "--key-file", str(tmp_path / "key")]
        + ["--site", "https://x.example", "--run", str(tmp_path / "no-run"), "--once"]
    )
    assert len(sent) == 2 and "e54" in sent[0]
    assert not {"e54", "peopleModel"} & set(sent[1]) and "bots" in sent[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_site_accepts_the_snapshot(tmp_path):
    """The site's own validBody (api/ingest.js, with Redis stubbed out) on a
    whole snapshot: a rejected snapshot would show every bot offline."""
    site = tmp_path / "site"
    site.mkdir()
    shutil.copy(SITE / "api" / "ingest.js", site / "ingest.js")
    (site / "_store.js").write_text("export const KEY = 'k'; export async function redis() {}\n")
    (site / "package.json").write_text('{"type": "module"}')
    body = _body(tmp_path)
    bad = json.loads(json.dumps(body))
    bad["e54"]["gates"][0]["extra"] = 1
    script = (
        f"import {{ validBody }} from '{(site / 'ingest.js').as_uri()}';\n"
        "const bodies = JSON.parse(process.argv[1]);\n"
        "console.log(JSON.stringify(bodies.map(validBody)));\n"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps([body, bad])],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout) == [True, False]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
@pytest.mark.parametrize("name", ["public/app.js", "api/live.js", "api/ingest.js"])
def test_the_site_scripts_parse(name):
    subprocess.run(["node", "--check", str(SITE / name)], check=True)
