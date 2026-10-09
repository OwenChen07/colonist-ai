import os

import pytest

from catanatron.colonist.distributed import SHARD_SIZE, split, worker_hosts


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
