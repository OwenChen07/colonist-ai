import os

import pytest

from catanatron.colonist.distributed import (
    SHARD_SIZE,
    selfplay_capacities,
    split,
    worker_hosts,
)


def test_worker_hosts_from_the_environment():
    env = {"COLONIST_WORKER_HOSTS": "owen@10.0.0.4:4, azureuser@1.2.3.4:2"}
    assert worker_hosts(env) == [("owen@10.0.0.4", 4), ("azureuser@1.2.3.4", 2)]
    old = {"COLONIST_WORKER_HOST": "owen@10.0.0.4", "COLONIST_REMOTE_WORKERS": "3"}
    assert worker_hosts(old) == [("owen@10.0.0.4", 3)]
    assert worker_hosts({}) == []
    with pytest.raises(ValueError):
        worker_hosts({"COLONIST_WORKER_HOSTS": "owen@10.0.0.4"})


@pytest.mark.parametrize("games", [5000, 640, 65, 3])
def test_split_covers_every_seed_once_in_whole_shards(games):
    ranges = split(games, 1000, 8, [("a", 4), ("b", 4)])
    assert len(ranges) == 3
    seeds = [s for first, size in ranges for s in range(first, first + size)]
    assert seeds == list(range(1000, 1000 + games))
    for first, size in ranges[:-1]:
        assert size % SHARD_SIZE == 0 or first + size == 1000 + games


def test_split_is_proportional_to_processes():
    (_, here), (_, a), (_, b) = split(64 * 16, 0, 8, [("a", 4), ("b", 4)])
    assert (here, a, b) == (64 * 8, 64 * 4, 64 * 4)


def test_calibrated_split_balances_slow_pc_without_changing_processes():
    hosts = [
        ("gcp-a", 4),
        ("gcp-b", 4),
        *[(f"small-{i}", 2) for i in range(4)],
        ("pc", 10),
    ]
    weights = [31, 18, 18, 9, 9, 9, 9, 19]
    ranges = split(3500, 1000, 8, hosts, weights)
    assert [size for _, size in ranges] == [896, 512, 512, 256, 256, 256, 256, 556]
    assert [
        seed for first, size in ranges for seed in range(first, first + size)
    ] == list(range(1000, 4500))


def test_capacities_are_keyed_by_host_and_read_each_round(tmp_path):
    path = tmp_path / "workers.env"
    hosts = [("gcp", 4), ("pc", 10)]
    env = {"COLONIST_WORKERS_FILE": str(path)}
    assert selfplay_capacities(8, hosts, env) == [8, 4, 10]
    path.write_text("COLONIST_SELFPLAY_CAPACITIES=local:31,pc:19,gcp:18\n")
    assert selfplay_capacities(8, hosts, env) == [31, 18, 19]
    assert selfplay_capacities(8, list(reversed(hosts)), env) == [31, 19, 18]
    with pytest.raises(ValueError, match="invalid self-play capacity"):
        selfplay_capacities(8, hosts, {"COLONIST_SELFPLAY_CAPACITIES": "pc:0"})


def test_workers_file_is_read_each_time(tmp_path):
    path = tmp_path / "workers.env"
    env = {"COLONIST_WORKERS_FILE": str(path), "COLONIST_WORKER_HOSTS": "a@x:2"}
    assert worker_hosts(env) == [("a@x", 2)]  # no file yet: the environment
    path.write_text("# workers\nCOLONIST_WORKER_HOSTS=a@x:2,b@y:4\nOTHER=1\n")
    assert worker_hosts(env) == [("a@x", 2), ("b@y", 4)]
    path.write_text("COLONIST_WORKER_HOSTS=\n")
    assert worker_hosts(env) == []


def test_evaluation_games_on_workers_match_local_play(tmp_path, monkeypatch):
    """Games sent to a worker (here: a local process standing in for SSH) come
    back identical to playing them here, in task order, with nets copied."""
    import shutil
    import sys

    from catanatron.colonist import distributed, evaluate
    from catanatron.colonist.net import random_net

    net = tmp_path / "net.npz"
    random_net(hidden=(8,)).save(str(net))
    monkeypatch.setenv("COLONIST_WORKER_ROOT", str(tmp_path / "worker"))
    monkeypatch.setattr(
        distributed,
        "_ssh",
        lambda host, key, argv: (
            [sys.executable, *map(str, argv[1:])]
            if str(argv[0]).endswith("python")
            else ["true"]
        ),
    )
    monkeypatch.setattr(
        distributed,
        "_rsync",
        lambda key, src, dst: (
            os.makedirs(os.path.dirname(dst.split(":", 1)[1]), exist_ok=True),
            shutil.copyfile(src, dst.split(":", 1)[1]),
        ),
    )
    monkeypatch.setattr(distributed, "_sync_code", lambda *a: None)
    tasks = list(evaluate.tasks_for(f"Z:{net}:2", "R", range(5, 8), {}))
    here = list(evaluate._run(tasks, 1))
    spread = distributed.run_tasks(tasks, 1, [("w@x", 1), ("w@y", 1)], log=print)
    assert spread == here


def test_a_failed_worker_s_games_are_played_here(tmp_path, monkeypatch):
    from catanatron.colonist import distributed, evaluate

    monkeypatch.setattr(distributed, "_sync_code", lambda *a: None)
    monkeypatch.setattr(distributed, "_ssh", lambda host, key, argv: ["false"])
    tasks = list(evaluate.tasks_for("R", "W", range(2), {}))
    logs = []
    games = distributed.run_tasks(tasks, 1, [("w@x", 2)], log=logs.append)
    assert games == list(evaluate._run(tasks, 1))
    assert logs


def test_a_vanished_host_s_range_is_shared_with_the_hosts_that_finished(
    tmp_path, monkeypatch
):
    import subprocess

    from catanatron.colonist import distributed
    from catanatron.colonist.datagen import shard_path

    out = tmp_path / "run" / "round" / "data"
    played = {}  # where each seed was played: "here", or a host

    def play(where, first, games):
        # Like selfplay.run: shards already here are skipped.
        for start in range(first, first + games, distributed.SHARD_SIZE):
            path = shard_path(
                out, start, min(distributed.SHARD_SIZE, first + games - start)
            )
            if not os.path.isfile(path):
                played.setdefault(start, []).append(where)
                open(path, "w").close()

    def selfplay_run(net, out_dir, games, seed, **options):
        play("here", seed, games)

    remote = {}  # host -> the range it last played

    def ssh(host, key, argv):
        argv = [str(a) for a in argv]
        first = int(argv[argv.index("--seed") + 1])
        games = int(argv[argv.index("--games") + 1])
        remote[host] = (first, games)
        return ["false" if host == "w@dead" else "true"]

    def rsync(key, source, destination):
        host = str(source).split(":")[0]
        if host == "w@dead":
            raise subprocess.CalledProcessError(255, "rsync")
        play(host, *remote[host])

    monkeypatch.setenv("COLONIST_WORKER_HOSTS", "w@dead:4,w@live:4")
    monkeypatch.delenv("COLONIST_WORKERS_FILE", raising=False)
    monkeypatch.delenv("COLONIST_SELFPLAY_CAPACITIES", raising=False)
    monkeypatch.setattr(distributed, "_prepare", lambda *a: ["selfplay"])
    monkeypatch.setattr(distributed, "_remote_opponents", lambda *a: [])
    monkeypatch.setattr(distributed, "_ssh", ssh)
    monkeypatch.setattr(distributed, "_rsync", rsync)
    monkeypatch.setattr(distributed.selfplay, "run", selfplay_run)
    logs = []
    distributed.run(
        "net.npz", out, games=12 * 64 + 5, seed=0, workers=4, log=logs.append
    )

    assert distributed._complete(out, 0, 12 * 64 + 5)
    dead_first, dead_games = distributed.split(12 * 64 + 5, 0, 4, [("a", 4), ("b", 4)])[
        1
    ]
    lost = range(dead_first, dead_first + dead_games, 64)
    # The dead host's shards were played once, part here and part on w@live.
    assert all(len(played[s]) == 1 for s in played)
    assert {played[s][0] for s in lost} == {"here", "w@live"}
    assert any("lost shards" in line for line in logs)


def test_human_opponent_reaches_local_and_remote_self_play(tmp_path, monkeypatch):
    from catanatron.colonist import distributed

    out = tmp_path / "run" / "round" / "data"
    local, remote, copied = {}, [], []

    def selfplay_run(net, out_dir, games, seed, **options):
        local.update(options)

    def ssh(host, key, argv):
        remote.append([str(a) for a in argv])
        return ["true"]

    monkeypatch.setenv("COLONIST_WORKER_HOSTS", "w@x:4")
    monkeypatch.delenv("COLONIST_WORKERS_FILE", raising=False)
    monkeypatch.delenv("COLONIST_SELFPLAY_CAPACITIES", raising=False)
    monkeypatch.setattr(distributed, "_prepare", lambda *a: ["selfplay"])
    monkeypatch.setattr(distributed, "_remote_opponents", lambda *a: [])
    monkeypatch.setattr(distributed, "_ssh", ssh)
    monkeypatch.setattr(distributed, "_rsync", lambda k, s, d: copied.append(str(s)))
    monkeypatch.setattr(distributed, "_complete", lambda *a: True)
    monkeypatch.setattr(distributed.selfplay, "run", selfplay_run)
    distributed.run(
        "net.npz",
        out,
        games=128,
        seed=0,
        workers=4,
        log=lambda m: None,
        human_opponent="human.npz",
        human_opponent_rate=0.15,
    )

    assert local["human_opponent"] == "human.npz"
    assert local["human_opponent_rate"] == 0.15
    argv = remote[0]
    assert argv[argv.index("--human-opponent-rate") + 1] == "0.15"
    assert argv[argv.index("--human-opponent") + 1].endswith("human_opponent.npz")
    assert "human.npz" in copied


def test_excluded_hosts_are_left_out():
    from catanatron.colonist.distributed import worker_hosts

    env = {
        "COLONIST_WORKER_HOSTS": "a@x:4,b@pc:10,c@y:2",
        "COLONIST_EXCLUDE_HOSTS": "b@pc",
    }
    assert worker_hosts(env) == [("a@x", 4), ("c@y", 2)]


def test_a_worker_s_leftover_self_play_is_cleared_before_a_new_job(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from catanatron.colonist import distributed

    commands = []
    monkeypatch.setattr(
        distributed.subprocess,
        "run",
        lambda argv, **k: commands.append(argv[-1]),
    )
    monkeypatch.setattr(distributed, "_sync_code", lambda *a: None)
    monkeypatch.setattr(distributed, "_rsync", lambda *a: None)
    root = Path("/home/w/colonist-ai")
    argv = distributed._prepare(
        "w@host", "key", root, root / "data", root / "nets", tmp_path / "n.npz"
    )
    assert any("pkill" in c and "atanatron.colonist.selfplay" in c for c in commands)
    assert commands.index(next(c for c in commands if "pkill" in c)) == 1  # after mkdir
    assert "catanatron.colonist.selfplay" in [str(a) for a in argv]
