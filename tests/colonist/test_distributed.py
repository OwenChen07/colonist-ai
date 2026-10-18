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
    tasks = list(evaluate.tasks_for(f"Z:{net}:2", f"ZE:{net}:2", range(5, 8), {}))
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

    def local(argv):
        argv = [str(a) for a in argv]
        selfplay_run(
            None,
            None,
            int(argv[argv.index("--games") + 1]),
            int(argv[argv.index("--seed") + 1]),
        )
        return ["true"]

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
    monkeypatch.setattr(distributed, "_local", local)
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


def test_idle_machines_take_a_straggler_s_unstarted_shards(tmp_path, monkeypatch):
    from catanatron.colonist import distributed
    from catanatron.colonist.datagen import shard_path

    out = tmp_path / "run" / "round" / "data"
    played = {}  # where each seed was played
    assigned = {}  # host -> the ranges it was given

    def play(where, first, games):
        for start in range(first, first + games, distributed.SHARD_SIZE):
            path = shard_path(
                out, start, min(distributed.SHARD_SIZE, first + games - start)
            )
            if not os.path.isfile(path):
                played.setdefault(start, []).append(where)
                open(path, "w").close()

    def seed_range(argv):
        argv = [str(a) for a in argv]
        return int(argv[argv.index("--seed") + 1]), int(argv[argv.index("--games") + 1])

    def ssh(host, key, argv):
        if "--seed" not in map(str, argv):
            return ["true"]  # the pkill that stops the straggler
        assigned.setdefault(host, []).append(seed_range(argv))
        return ["sleep", "60"] if host == "w@slow" else ["true"]

    def local(argv):
        play("here", *seed_range(argv))
        return ["true"]

    def rsync(key, source, destination):
        host = str(source).split(":")[0]
        first, games = assigned[host][0]
        if host == "w@fast":
            for first, games in assigned[host]:
                play(host, first, games)
        elif all(
            os.path.isfile(shard_path(out, s, 64))
            for s in range(first + 4 * 64, first + games, 64)
        ):
            play(host, first, 4 * 64)  # its 4 processes' first shards end

    monkeypatch.setenv("COLONIST_WORKER_HOSTS", "w@slow:4,w@fast:2")
    monkeypatch.delenv("COLONIST_WORKERS_FILE", raising=False)
    monkeypatch.delenv("COLONIST_SELFPLAY_CAPACITIES", raising=False)
    monkeypatch.setattr(distributed, "STEAL_POLL", 0)
    monkeypatch.setattr(distributed, "_prepare", lambda *a: ["selfplay"])
    monkeypatch.setattr(distributed, "_remote_opponents", lambda *a: [])
    monkeypatch.setattr(distributed, "_ssh", ssh)
    monkeypatch.setattr(distributed, "_local", local)
    monkeypatch.setattr(distributed, "_rsync", rsync)
    monkeypatch.setattr(
        distributed.selfplay,
        "run",
        lambda net, out_dir, games, seed, **o: play("here", seed, games),
    )
    logs = []
    distributed.run("net.npz", out, games=16 * 64, seed=0, workers=2, log=logs.append)

    assert distributed._complete(out, 0, 16 * 64)
    slow_first, slow_games = assigned["w@slow"][0]
    assert slow_games == 8 * 64
    tail = range(slow_first + 4 * 64, slow_first + slow_games, 64)
    assert all(len(where) == 1 for where in played.values())
    assert {played[s][0] for s in tail} == {"here", "w@fast"}
    assert any("stopping w@slow" in line for line in logs)


def test_a_fast_idle_host_also_plays_a_slow_host_s_started_shard(tmp_path, monkeypatch):
    from catanatron.colonist import distributed
    from catanatron.colonist.datagen import shard_path

    out = tmp_path / "run" / "round" / "data"
    played = {}
    assigned = {}

    def play(where, first, games):
        for start in range(first, first + games, distributed.SHARD_SIZE):
            path = shard_path(
                out, start, min(distributed.SHARD_SIZE, first + games - start)
            )
            if not os.path.isfile(path):
                played.setdefault(start, []).append(where)
                open(path, "w").close()

    def seed_range(argv):
        argv = [str(a) for a in argv]
        return int(argv[argv.index("--seed") + 1]), int(argv[argv.index("--games") + 1])

    def ssh(host, key, argv):
        if "--seed" not in map(str, argv):
            return ["true"]
        assigned.setdefault(host, []).append(seed_range(argv))
        return ["sleep", "60"] if host == "w@slow" else ["true"]

    def rsync(key, source, destination):
        host = str(source).split(":")[0]
        if host == "w@fast":  # the slow host's one shard never ends
            for first, games in assigned[host]:
                play(host, first, games)

    monkeypatch.setenv("COLONIST_WORKER_HOSTS", "w@slow:2,w@fast:4")
    monkeypatch.setenv("COLONIST_SELFPLAY_CAPACITIES", "local:10,w@slow:5,w@fast:40")
    monkeypatch.delenv("COLONIST_WORKERS_FILE", raising=False)
    monkeypatch.setattr(distributed, "STEAL_POLL", 0)
    monkeypatch.setattr(distributed, "_prepare", lambda *a: ["selfplay"])
    monkeypatch.setattr(distributed, "_remote_opponents", lambda *a: [])
    monkeypatch.setattr(distributed, "_ssh", ssh)
    monkeypatch.setattr(
        distributed, "_local", lambda argv: play("here", *seed_range(argv)) or ["true"]
    )
    monkeypatch.setattr(distributed, "_rsync", rsync)
    monkeypatch.setattr(
        distributed.selfplay,
        "run",
        lambda net, out_dir, games, seed, **o: play("here", seed, games),
    )
    logs = []
    distributed.run("net.npz", out, games=6 * 64, seed=0, workers=2, log=logs.append)

    assert distributed._complete(out, 0, 6 * 64)
    (slow_first, slow_games), *_ = assigned["w@slow"]
    assert slow_games == 64
    assert played[slow_first] == ["w@fast"]
    assert assigned["w@fast"][-1] == (slow_first, 64)
    assert any("w@fast also plays 64 games" in line for line in logs)
    assert not any("here also plays" in line for line in logs)
    assert any("stopping w@slow" in line for line in logs)


def test_an_unprepared_host_s_range_is_taken_while_a_straggler_plays(
    tmp_path, monkeypatch
):
    # Round 71: a preempted spot VM's 9 shards waited 16 min for the
    # slowest host, then were played in a second wave.
    import subprocess
    import time

    from catanatron.colonist import distributed
    from catanatron.colonist.datagen import shard_path

    out = tmp_path / "run" / "round" / "data"
    played = {}  # where each seed was played
    assigned = {}  # host -> the ranges it was given

    def play(where, first, games):
        for start in range(first, first + games, distributed.SHARD_SIZE):
            path = shard_path(
                out, start, min(distributed.SHARD_SIZE, first + games - start)
            )
            if not os.path.isfile(path):
                played.setdefault(start, []).append(where)
                open(path, "w").close()

    def seed_range(argv):
        argv = [str(a) for a in argv]
        return int(argv[argv.index("--seed") + 1]), int(argv[argv.index("--games") + 1])

    def prepare(host, *a):
        if host == "w@dead":
            raise subprocess.CalledProcessError(255, "ssh")
        return ["selfplay"]

    def ssh(host, key, argv):
        if "--seed" not in map(str, argv):
            return ["true"]  # the pkill that stops the straggler
        assigned.setdefault(host, []).append(seed_range(argv))
        return ["sleep", "60"]  # still playing its last games

    def local(argv):
        play("here", *seed_range(argv))
        return ["true"]

    def rsync(key, source, destination):
        host = str(source).split(":")[0]
        if host == "w@slow":
            play(host, *assigned[host][0])

    monkeypatch.setenv("COLONIST_WORKER_HOSTS", "w@dead:4,w@slow:4")
    monkeypatch.delenv("COLONIST_WORKERS_FILE", raising=False)
    monkeypatch.delenv("COLONIST_SELFPLAY_CAPACITIES", raising=False)
    monkeypatch.setattr(distributed, "STEAL_POLL", 0)
    monkeypatch.setattr(distributed, "_prepare", prepare)
    monkeypatch.setattr(distributed, "_remote_opponents", lambda *a: [])
    monkeypatch.setattr(distributed, "_ssh", ssh)
    monkeypatch.setattr(distributed, "_local", local)
    monkeypatch.setattr(distributed, "_rsync", rsync)
    monkeypatch.setattr(
        distributed.selfplay,
        "run",
        lambda net, out_dir, games, seed, **o: play("here", seed, games),
    )
    logs = []
    started = time.monotonic()
    distributed.run("net.npz", out, games=12 * 64, seed=0, workers=2, log=logs.append)

    assert distributed._complete(out, 0, 12 * 64)
    assert time.monotonic() - started < 30  # not after the straggler's 60 s
    dead_first, dead_games = distributed.split(12 * 64, 0, 2, [("a", 4), ("b", 4)])[1]
    lost = range(dead_first, dead_first + dead_games, 64)
    assert all(len(where) == 1 for where in played.values())
    assert {played[s][0] for s in lost} == {"here"}
    assert any("takes" in line and "from w@dead" in line for line in logs)
    assert any("stopping w@slow" in line for line in logs)
    assert not any("lost shards" in line for line in logs)


def test_idle_hosts_take_orphans_while_the_coordinator_plays(tmp_path, monkeypatch):
    # Round 72: six VMs sat idle 25 min while the loaded coordinator played.
    import subprocess
    import time

    from catanatron.colonist import distributed
    from catanatron.colonist.datagen import shard_path

    out = tmp_path / "run" / "round" / "data"
    played = {}  # where each seed was played
    assigned = {}  # host -> the ranges it was given

    def play(where, first, games):
        for start in range(first, first + games, distributed.SHARD_SIZE):
            path = shard_path(
                out, start, min(distributed.SHARD_SIZE, first + games - start)
            )
            if not os.path.isfile(path):
                played.setdefault(start, []).append(where)
                open(path, "w").close()

    def seed_range(argv):
        argv = [str(a) for a in argv]
        return int(argv[argv.index("--seed") + 1]), int(argv[argv.index("--games") + 1])

    def prepare(host, *a):
        if host == "w@dead":
            raise subprocess.CalledProcessError(255, "ssh")
        return ["selfplay"]

    def ssh(host, key, argv):
        if "--seed" in map(str, argv):
            assigned.setdefault(host, []).append(seed_range(argv))
        return ["true"]

    def local(argv):
        play("here", *seed_range(argv))
        return ["sleep", "60"]  # its shards are on disk, its process still runs

    def rsync(key, source, destination):
        host = str(source).split(":")[0]
        for first, games in assigned.get(host, []):
            play(host, first, games)

    monkeypatch.setenv("COLONIST_WORKER_HOSTS", "w@dead:4,w@fast:4")
    monkeypatch.delenv("COLONIST_WORKERS_FILE", raising=False)
    monkeypatch.delenv("COLONIST_SELFPLAY_CAPACITIES", raising=False)
    monkeypatch.setattr(distributed, "STEAL_POLL", 0)
    monkeypatch.setattr(distributed, "_prepare", prepare)
    monkeypatch.setattr(distributed, "_remote_opponents", lambda *a: [])
    monkeypatch.setattr(distributed, "_ssh", ssh)
    monkeypatch.setattr(distributed, "_local", local)
    monkeypatch.setattr(distributed, "_rsync", rsync)
    logs = []
    started = time.monotonic()
    distributed.run("net.npz", out, games=12 * 64, seed=0, workers=4, log=logs.append)

    assert distributed._complete(out, 0, 12 * 64)
    assert time.monotonic() - started < 30  # not after the coordinator's 60 s
    dead_first, dead_games = distributed.split(12 * 64, 0, 4, [("a", 4), ("b", 4)])[1]
    lost = range(dead_first, dead_first + dead_games, 64)
    assert all(len(where) == 1 for where in played.values())
    assert {played[s][0] for s in lost} == {"w@fast"}
    assert any("w@fast takes" in line and "from w@dead" in line for line in logs)
    assert any("stopping here" in line for line in logs)


def test_human_opponent_reaches_local_and_remote_self_play(tmp_path, monkeypatch):
    from catanatron.colonist import distributed

    out = tmp_path / "run" / "round" / "data"
    local, remote, copied = [], [], []

    def local_argv(argv):
        local.extend(str(a) for a in argv)
        return ["true"]

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
    monkeypatch.setattr(distributed, "_local", local_argv)
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

    assert local[local.index("--human-opponent") + 1] == "human.npz"
    assert local[local.index("--human-opponent-rate") + 1] == "0.15"
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
