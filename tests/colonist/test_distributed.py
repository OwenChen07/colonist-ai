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


def test_hosts_are_prepared_at_once_in_order_with_failures_as_none():
    import subprocess
    import threading

    from catanatron.colonist.distributed import _each_host

    hosts = ["a", "b", "c", "d"]
    barrier = threading.Barrier(len(hosts), timeout=5)  # breaks if one at a time

    def prepare(host):
        barrier.wait()
        if host == "c":
            raise subprocess.CalledProcessError(1, "ssh")
        return host.upper()

    assert _each_host(prepare, hosts) == [
        ("a", "A"),
        ("b", "B"),
        ("c", None),
        ("d", "D"),
    ]
    assert _each_host(prepare, []) == []


def test_a_lost_host_starts_the_restart_script_once_per_host(tmp_path, monkeypatch):
    import time

    from catanatron.colonist import distributed

    monkeypatch.setattr(distributed, "_restarts", {})
    logs = []
    env = {"COLONIST_RESTART_SCRIPT": f"echo x >> {tmp_path}/ran"}
    assert not distributed._restart("w@a", logs.append, {})  # off unless set
    assert not distributed._restart(distributed.HERE, logs.append, env)
    assert distributed._restart("w@a", logs.append, env)
    assert not distributed._restart("w@a", logs.append, env)  # within 10 min
    assert distributed._restart("w@b", logs.append, env)
    monkeypatch.setitem(
        distributed._restarts, "w@a", time.monotonic() - distributed.RESTART_EVERY
    )
    assert distributed._restart("w@a", logs.append, env)
    assert logs == [
        f"{h} is down: starting stopped spot VMs in the background"
        for h in ["w@a", "w@b", "w@a"]
    ]
    for _ in range(100):  # it runs in the background
        if (tmp_path / "ran").exists() and len(
            (tmp_path / "ran").read_text().split()
        ) == 3:
            break
        time.sleep(0.05)
    assert (tmp_path / "ran").read_text().split() == ["x"] * 3


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
    spread = distributed.run_tasks(tasks, 1, [("w@x", 1), ("w@y", 2)], log=print)
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


def test_a_failed_worker_s_queued_games_go_to_the_other_workers(monkeypatch):
    import sys

    from catanatron.colonist import distributed, evaluate

    def ssh(host, key, argv):
        if host == "w@x":
            return ["false"]
        return [sys.executable, *map(str, argv[1:])]

    monkeypatch.setattr(distributed, "_sync_code", lambda *a: None)
    monkeypatch.setattr(distributed, "_remote_specs", lambda h, k, r, tasks: tasks)
    monkeypatch.setattr(distributed, "_ssh", ssh)
    monkeypatch.setattr(distributed, "_match_rates", {})
    monkeypatch.delenv("COLONIST_WORKERS_FILE", raising=False)
    monkeypatch.setenv("COLONIST_SELFPLAY_CAPACITIES", "local:1,w@x:1,w@y:8")
    tasks = list(evaluate.tasks_for("R", "W", range(4), {}))
    logs = []
    games = distributed.run_tasks(tasks, 1, [("w@x", 1), ("w@y", 1)], log=logs.append)
    assert games == list(evaluate._run(tasks, 1))
    assert any(line.startswith("w@x stopped") for line in logs)
    assert list(distributed._match_rates) == ["local", "w@y"]


def test_a_side_match_can_play_at_low_priority_on_the_hosts(monkeypatch):
    from catanatron.colonist import distributed

    monkeypatch.delenv("COLONIST_MATCH_NICE", raising=False)
    assert distributed._niced(["python", "-m", "x"]) == ["python", "-m", "x"]
    monkeypatch.setenv("COLONIST_MATCH_NICE", "19")
    assert distributed._niced(["python"]) == ["nice", "-n", "19", "python"]


def test_evaluation_games_are_split_by_measured_speed(tmp_path, monkeypatch):
    from catanatron.colonist import distributed, evaluate

    sent = {}

    def specs(host, key, root, tasks):
        sent[host] = len(tasks)
        return tasks

    monkeypatch.setattr(distributed, "_sync_code", lambda *a: None)
    monkeypatch.setattr(distributed, "_remote_specs", specs)
    monkeypatch.setattr(distributed, "_ssh", lambda host, key, argv: ["false"])
    monkeypatch.setattr(distributed, "_match_rates", {})
    monkeypatch.delenv("COLONIST_WORKERS_FILE", raising=False)
    monkeypatch.setenv("COLONIST_SELFPLAY_CAPACITIES", "local:1,w@x:1,w@y:8")
    tasks = list(evaluate.tasks_for("R", "W", range(5), {}))
    logs = []
    games = distributed.run_tasks(tasks, 4, [("w@x", 4), ("w@y", 4)], log=logs.append)
    assert sent == {"w@x": 10, "w@y": 10}  # the hosts draw from a queue
    assert games == list(evaluate._run(tasks, 1))
    assert any(line.startswith("evaluation games by host: here 1") for line in logs)
    # Only the hosts that returned all their games keep a measured speed.
    assert list(distributed._match_rates) == ["local"]


def test_later_evaluation_calls_split_by_the_measured_evaluation_speed(
    monkeypatch,
):
    from catanatron.colonist import distributed

    monkeypatch.setattr(distributed, "_match_rates", {})
    names = ["local", "w@x", "w@y"]
    assert distributed._match_weights(names, [10, 20, 40]) == [10, 20, 40]
    # Measured: x plays 1 game/s, y 4; local, unmeasured, gets its self-play
    # capacity times the median measured ratio (x: 1/20, y: 4/40, median 0.1).
    distributed._match_rates.update({"w@x": 1.0, "w@y": 4.0})
    assert distributed._match_weights(names, [10, 20, 40]) == [1.0, 1.0, 4.0]


# A stand-in for `selfplay --parts-stream`: plays each 'first count' line by
# writing the part's file (after `delay` seconds), logs "label first", and
# prints the file name; exits 1 after `limit` parts. Up to `width` parts are
# in play at once, as the real one keeps STREAM_GAMES games going.
FAKE_WORKER = """
import os, sys, threading, time
out, label, delay, limit, played, width = sys.argv[1:7]
os.makedirs(out, exist_ok=True)
room, lock = threading.Semaphore(int(width)), threading.Lock()

def play(first, count):
    time.sleep(float(delay))
    name = f"shard_{first:07d}_{count}.npz"
    open(os.path.join(out, name), "w").close()
    with lock:
        with open(played, "a") as stream:
            stream.write(f"{label} {first}\\n")
        print(name, flush=True)
    room.release()

threads = []
for n, line in enumerate(iter(sys.stdin.readline, "")):
    if n == int(limit):
        os._exit(1)
    room.acquire()
    threads.append(threading.Thread(target=play, args=tuple(map(int, line.split()))))
    threads[-1].start()
for thread in threads:
    thread.join()
"""


def _queue_fleet(tmp_path, monkeypatch, hosts, behaviour, lossy=()):
    """distributed.run's machines as FAKE_WORKERs: behaviour[host] is
    (seconds per part, parts before it exits[, parts in play at once]);
    hosts not in it fail to prepare. Returns out_dir, the run's log, the
    file of where each part was played, and the (seed, games) played here
    after the queue."""
    import shutil
    import subprocess
    import sys

    from catanatron.colonist import distributed

    out = tmp_path / "run" / "round" / "data"
    played = tmp_path / "played.txt"

    def fake(label, parts):
        delay, limit, width = (behaviour.get(label, (0, 10**6)) + (1,))[:3]
        return [
            sys.executable,
            "-c",
            FAKE_WORKER,
            parts,
            label,
            delay,
            limit,
            played,
            width,
        ]

    def prepare(host, *a):
        if host not in behaviour:
            raise subprocess.CalledProcessError(255, "ssh")
        return ["selfplay"]

    def ssh(host, key, argv):
        if "--parts-stream" not in map(str, argv):
            return ["true"]  # the pkill that stops it
        return [str(a) for a in fake(host, str(tmp_path / host / "parts"))]

    def rsync(key, source, destination):
        host, _, path = str(source).partition(":")
        if host in lossy:
            raise subprocess.CalledProcessError(255, "rsync")
        shutil.copy(tmp_path / host / "parts" / os.path.basename(path), destination)

    def merge(path, parts):
        open(path, "w").close()
        for part in parts:
            os.remove(part)

    monkeypatch.setenv("COLONIST_WORKER_HOSTS", ",".join(hosts))
    monkeypatch.delenv("COLONIST_WORKERS_FILE", raising=False)
    monkeypatch.setattr(distributed, "_prepare", prepare)
    monkeypatch.setattr(distributed, "_remote_opponents", lambda *a: [])
    monkeypatch.setattr(distributed, "_ssh", ssh)
    monkeypatch.setattr(distributed, "_rsync", rsync)
    monkeypatch.setattr(
        distributed,
        "_local",
        lambda argv: [str(a) for a in fake("here", argv[4] + "/parts")],
    )
    monkeypatch.setattr(distributed.selfplay, "merge_parts", merge)
    here = []
    monkeypatch.setattr(
        distributed.selfplay,
        "run",
        lambda net, out_dir, games, seed, **o: here.append((seed, games))
        or open(distributed.shard_path(out_dir, seed, games), "w").close(),
    )
    logs = []
    return out, logs, played, here


def _where(played):
    where = {}
    for line in played.read_text().split("\n"):
        if line:
            label, first = line.split()
            where.setdefault(int(first), []).append(label)
    return where


@pytest.mark.parametrize("scale", [None, 0.5])
def test_target_scale_reaches_every_worker_only_when_set(tmp_path, monkeypatch, scale):
    """E59: --target-scale goes to the coordinator's and the hosts' self-play
    command lines when set, and to neither when unset."""
    from catanatron.colonist import distributed

    out, logs, _, _ = _queue_fleet(
        tmp_path, monkeypatch, ["w@a:2"], {"here": (0, 10**6), "w@a": (0, 10**6)}
    )
    argvs = []
    for name in ("_ssh", "_local"):
        real = getattr(distributed, name)
        monkeypatch.setattr(
            distributed,
            name,
            lambda *a, real=real: argvs.append(list(map(str, a[-1]))) or real(*a),
        )
    distributed.run(
        "net.npz", out, games=64, seed=0, workers=1, log=logs.append, target_scale=scale
    )
    streams = [a for a in argvs if "--parts-stream" in a]
    assert len(streams) >= 2  # here and w@a
    for argv in streams:
        if scale is None:
            assert "--target-scale" not in argv
        else:
            assert argv[argv.index("--target-scale") + 1] == "0.5"


def test_every_machine_draws_parts_from_one_queue(tmp_path, monkeypatch):
    from catanatron.colonist import distributed

    behaviour = {"here": (0.02, 10**6), "w@fast": (0, 10**6), "w@slow": (0.3, 10**6)}
    out, logs, played, here = _queue_fleet(
        tmp_path, monkeypatch, ["w@fast:4", "w@slow:2"], behaviour
    )
    games = 4 * 64 + 5
    distributed.run("net.npz", out, games=games, seed=0, workers=2, log=logs.append)

    assert distributed._complete(out, 0, games)
    where = _where(played)
    parts = [
        p
        for s in range(0, games, 64)
        for p in distributed.selfplay.parts_of(
            s, min(64, games - s), min(64, games - s) // distributed.PART_SIZE
        )
    ]
    assert sorted(where) == sorted(first for first, _ in parts)
    assert len(parts) == 4 * 8 + 1
    counts = {}
    for labels in where.values():
        counts[labels[0]] = counts.get(labels[0], 0) + 1
    assert counts.get("w@fast", 0) > counts.get("w@slow", 0)
    assert not here
    assert not os.listdir(out / "parts")  # all merged
    assert any(line.startswith("self-play games by host: here") for line in logs)


def test_a_failed_or_unprepared_host_s_parts_are_played_by_the_others(
    tmp_path, monkeypatch
):
    # Round 71: a preempted spot VM's 9 shards waited 16 min for the
    # slowest host, then were played in a second wave.
    import time

    from catanatron.colonist import distributed

    behaviour = {"here": (0.01, 10**6), "w@live": (0, 10**6), "w@dies": (0, 3)}
    out, logs, played, here = _queue_fleet(
        tmp_path,
        monkeypatch,
        ["w@dead:4", "w@dies:4", "w@lossy:4", "w@live:4"],
        dict(behaviour, **{"w@lossy": (0, 10**6)}),
        lossy=("w@lossy",),
    )
    started = time.monotonic()
    distributed.run("net.npz", out, games=6 * 64, seed=0, workers=2, log=logs.append)

    assert distributed._complete(out, 0, 6 * 64)
    assert time.monotonic() - started < 30
    assert not here
    assert any("could not prepare w@dead" in line for line in logs)
    assert any("w@dies stopped with parts unfinished" in line for line in logs)
    where = _where(played)
    assert len(where) == 6 * 8
    # The parts w@lossy played but couldn't copy back were played again.
    assert all(set(labels) - {"w@lossy"} for labels in where.values())


def test_a_slow_machine_s_last_part_is_also_played_by_an_idle_one(
    tmp_path, monkeypatch
):
    # Round 76 waited 4 minutes on a 2-core AWS worker's last shard after
    # the fleet was done.
    import time

    from catanatron.colonist import distributed

    behaviour = {"here": (0, 10**6), "w@fast": (0, 10**6), "w@stuck": (60, 10**6)}
    out, logs, played, here = _queue_fleet(
        tmp_path, monkeypatch, ["w@stuck:1", "w@fast:4"], behaviour
    )
    started = time.monotonic()
    distributed.run("net.npz", out, games=2 * 64, seed=0, workers=2, log=logs.append)

    assert distributed._complete(out, 0, 2 * 64)
    assert time.monotonic() - started < 30  # not after the stuck host's 60 s
    assert len(_where(played)) == 2 * 8
    assert any("stopping w@stuck" in line for line in logs)
    assert "w@stuck 0 in" in next(l for l in logs if l.startswith("self-play games by"))


def test_busy_machines_do_not_copy_each_other_s_parts(tmp_path, monkeypatch):
    # Round 96: with 5 parts per process held, the queue was empty a third
    # of the way in, and every free slot took a copy of a part another busy
    # machine held: 6,640 games played for 5,000.
    from catanatron.colonist import distributed

    behaviour = {h: (0.2, 10**6, 4) for h in ("here", "w@a", "w@b")}
    out, logs, played, here = _queue_fleet(
        tmp_path, monkeypatch, ["w@a:1", "w@b:1"], behaviour
    )
    distributed.run("net.npz", out, games=3 * 64, seed=0, workers=1, log=logs.append)

    assert distributed._complete(out, 0, 3 * 64)
    where = _where(played)
    assert len(where) == 3 * 8
    # Only an idle machine copies: at most one copy each, at the end.
    assert sum(len(labels) for labels in where.values()) <= 3 * 8 + 3


def test_shards_and_parts_already_here_are_not_played_again(tmp_path, monkeypatch):
    from catanatron.colonist import distributed

    out, logs, played, here = _queue_fleet(
        tmp_path, monkeypatch, ["w@x:2"], {"here": (0, 10**6), "w@x": (0, 10**6)}
    )
    os.makedirs(out / "parts")
    open(distributed.shard_path(out, 0, 64), "w").close()
    open(distributed.shard_path(out / "parts", 64, 8), "w").close()
    distributed.run("net.npz", out, games=2 * 64, seed=0, workers=2, log=logs.append)

    assert distributed._complete(out, 0, 2 * 64)
    assert sorted(_where(played)) == list(range(72, 128, 8))


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
    monkeypatch.setattr(distributed.selfplay, "run", lambda *a, **k: None)
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
