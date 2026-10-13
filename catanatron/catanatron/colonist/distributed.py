"""Run one self-play round across the coordinator and private SSH workers.

Set COLONIST_WORKER_HOSTS to enable this from colonist.loop, as a comma-
separated list of `user@host:processes` (COLONIST_WORKER_HOST, one host, still
works), or point COLONIST_WORKERS_FILE at a file with a COLONIST_WORKER_HOSTS=
line, which is read again every round, so workers can be added or removed
while the loop runs. Each round also copies this checkout's code to every
worker first. Each host gets a disjoint, shard-aligned seed range in proportion to
its processes. Workers write to their own disks; the coordinator copies their
complete shards back, and plays any range a worker failed to finish, before
training may start. A worker can be any machine reachable over SSH with the
repository at COLONIST_WORKER_ROOT (the same path on every worker) and its
virtualenv, e.g. a GCP or an Azure VM.
"""

import argparse
import os
import shlex
import subprocess
from pathlib import Path

from catanatron.colonist import selfplay
from catanatron.colonist.datagen import shard_path

SHARD_SIZE = 64


def _command(argv):
    return " ".join(shlex.quote(str(arg)) for arg in argv)


def _ssh(host, key, argv):
    return [
        "ssh",
        "-i",
        key,
        "-o",
        "BatchMode=yes",
        "-o",
        "ServerAliveInterval=60",
        host,
        _command(argv),
    ]


def _rsync(key, source, destination):
    subprocess.run(
        [
            "rsync",
            "-a",
            "-e",
            f"ssh -i {shlex.quote(key)} -o BatchMode=yes",
            str(source),
            str(destination),
        ],
        check=True,
    )


def _complete(out_dir, seed, games):
    return all(
        os.path.isfile(
            shard_path(out_dir, first, min(SHARD_SIZE, seed + games - first))
        )
        for first in range(seed, seed + games, SHARD_SIZE)
    )


def worker_hosts(environ=os.environ):
    """[(host, processes)] from COLONIST_WORKERS_FILE, if set, or else the
    environment."""
    spec = environ.get("COLONIST_WORKER_HOSTS")
    path = environ.get("COLONIST_WORKERS_FILE")
    if path and os.path.isfile(path):
        with open(path) as stream:
            for line in stream:
                name, _, value = line.strip().partition("=")
                if name == "COLONIST_WORKER_HOSTS":
                    spec = value
    if not spec and environ.get("COLONIST_WORKER_HOST"):
        spec = (
            f"{environ['COLONIST_WORKER_HOST']}:"
            f"{environ.get('COLONIST_REMOTE_WORKERS', '2')}"
        )
    hosts = []
    for item in (spec or "").split(","):
        item = item.strip()
        if not item:
            continue
        host, _, processes = item.rpartition(":")
        if not host or not processes.isdigit() or int(processes) < 1:
            raise ValueError(f"worker {item!r} is not user@host:processes")
        hosts.append((host, int(processes)))
    return hosts


def split(games, seed, local_processes, hosts):
    """Shard-aligned (seed, games) ranges: the coordinator's first, then one
    per host, in proportion to processes. Every range but the last is whole
    shards, so no seed is played twice."""
    shards = (games + SHARD_SIZE - 1) // SHARD_SIZE
    weights = [local_processes] + [p for _, p in hosts]
    total = sum(weights)
    counts = [shards * w // total for w in weights]
    for i in range(shards - sum(counts)):  # the remainder, coordinator first
        counts[i % len(counts)] += 1
    ranges, first = [], seed
    for count in counts:
        size = min(count * SHARD_SIZE, seed + games - first)
        ranges.append((first, max(0, size)))
        first += max(0, size)
    return ranges


def run(
    net_path,
    out_dir,
    games=5000,
    seed=2_000_000,
    simulations=50,
    workers=None,
    opponents=(),
    opponent_rate=0.0,
    log=print,
    setup_noise_scale=1.0,
    full_rate=1.0,
    fast_simulations=None,
    human_starts=None,
    human_start_rate=0.0,
):
    """Generate all shards locally and remotely, then verify the whole round."""
    hosts = worker_hosts()
    key = os.path.expanduser(
        os.environ.get("COLONIST_WORKER_SSH_KEY", "~/.ssh/colonist_worker_ed25519")
    )
    root = Path(os.environ.get("COLONIST_WORKER_ROOT", "/home/owen/colonist-ai"))
    if not hosts:
        raise ValueError("distributed self-play needs COLONIST_WORKER_HOSTS")
    local_processes = workers or os.cpu_count() or 1
    ranges = split(games, seed, local_processes, hosts)
    (local_seed, local_games), remote_ranges = ranges[0], ranges[1:]
    round_name = Path(out_dir).parent.name
    run_name = Path(out_dir).parent.parent.name
    remote = root / "distributed" / run_name / round_name
    remote_data, remote_nets = remote / "data", remote / "nets"
    options = dict(
        simulations=simulations,
        workers=workers,
        opponents=opponents,
        opponent_rate=opponent_rate,
        log=log,
        setup_noise_scale=setup_noise_scale,
        full_rate=full_rate,
        fast_simulations=fast_simulations,
        human_starts=human_starts,
        human_start_rate=human_start_rate,
    )
    os.makedirs(out_dir, exist_ok=True)

    processes = []
    for (host, count), (first, size) in zip(hosts, remote_ranges):
        if not size:
            continue
        try:
            argv = _prepare(host, key, root, remote_data, remote_nets, net_path)
            argv += _remote_opponents(host, key, remote_nets, opponents)
            argv += [
                "--games",
                size,
                "--seed",
                first,
                "--simulations",
                simulations,
                "--workers",
                count,
                "--opponent-rate",
                opponent_rate,
                "--setup-noise-scale",
                setup_noise_scale,
                "--full-rate",
                full_rate,
            ]
            if fast_simulations:
                argv += ["--fast-simulations", fast_simulations]
            if human_starts and human_start_rate > 0:
                remote_starts = remote_nets / "starts.jsonl.gz"
                _rsync(key, human_starts, f"{host}:{remote_starts}")
                argv += [
                    "--human-starts",
                    remote_starts,
                    "--human-start-rate",
                    human_start_rate,
                ]
        except subprocess.CalledProcessError:
            log(f"could not prepare {host}; its range runs here")
            continue
        stream = open(Path(out_dir).parent / f"remote-{_safe(host)}.log", "a", 1)
        process = subprocess.Popen(
            _ssh(host, key, argv), stdout=stream, stderr=subprocess.STDOUT
        )
        processes.append((host, process, stream))
        log(f"self-play: {size} games (seeds {first}+) on {host}")
    log(f"self-play: {local_games} games here")

    try:
        if local_games:
            selfplay.run(
                net_path, out_dir, games=local_games, seed=local_seed, **options
            )
    except BaseException:
        for _, process, _ in processes:
            process.terminate()
            process.wait()
        raise
    for host, process, stream in processes:
        code = process.wait()
        stream.close()
        if code:
            log(f"{host} exited with status {code}; finishing its range here")
        try:
            _rsync(key, f"{host}:{remote_data}/", f"{out_dir}/")
        except subprocess.CalledProcessError:
            log(f"copying shards from {host} failed; finishing its range here")
    for first, size in remote_ranges:
        if size and not _complete(out_dir, first, size):
            selfplay.run(net_path, out_dir, games=size, seed=first, **options)
    if not _complete(out_dir, seed, games):
        raise RuntimeError("self-play round has missing shards; training cannot start")
    log(f"self-play complete across {1 + len(processes)} machines: {games} games")


def _safe(host):
    return "".join(c if c.isalnum() or c in "-." else "_" for c in host)


def _prepare(host, key, root, remote_data, remote_nets, net_path):
    """Makes the worker's folders and copies the net; returns the start of
    its self-play command."""
    subprocess.run(
        _ssh(host, key, ["mkdir", "-p", remote_data, remote_nets]), check=True
    )
    # The same rules and search everywhere.
    _sync_code(host, key, root)
    remote_net = remote_nets / "best.npz"
    _rsync(key, net_path, f"{host}:{remote_net}")
    return [
        root / ".venv/bin/python",
        "-m",
        "catanatron.colonist.selfplay",
        remote_net,
        remote_data,
    ]


def _remote_opponents(host, key, remote_nets, opponents):
    names = []
    for index, opponent in enumerate(opponents):
        if os.path.isfile(opponent):
            target = remote_nets / f"opponent_{index}.npz"
            _rsync(key, opponent, f"{host}:{target}")
            names.append(str(target))
        else:
            names.append(opponent)
    return ["--opponents", ",".join(names)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("net")
    parser.add_argument("out_dir")
    parser.add_argument("--games", type=int, default=128)
    parser.add_argument("--seed", type=int, default=4_000_000_000)
    parser.add_argument("--simulations", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--opponents", default="")
    parser.add_argument("--opponent-rate", type=float, default=0.0)
    args = parser.parse_args(argv)
    run(
        args.net,
        args.out_dir,
        games=args.games,
        seed=args.seed,
        simulations=args.simulations,
        workers=args.workers,
        opponents=[item for item in args.opponents.split(",") if item],
        opponent_rate=args.opponent_rate,
    )


if __name__ == "__main__":
    main()


# ----- evaluation games (promotion matches, benchmarks) on the workers -----
def _settings():
    key = os.path.expanduser(
        os.environ.get("COLONIST_WORKER_SSH_KEY", "~/.ssh/colonist_worker_ed25519")
    )
    root = Path(os.environ.get("COLONIST_WORKER_ROOT", "/home/owen/colonist-ai"))
    return key, root


def _sync_code(host, key, root):
    """This checkout's code, into the worker's editable install."""
    source = Path(__file__).resolve().parents[2]  # the catanatron/ folder
    subprocess.run(
        [
            "rsync",
            "-a",
            "--exclude",
            "*.egg-info",
            "--exclude",
            "__pycache__",
            "-e",
            f"ssh -i {shlex.quote(key)} -o BatchMode=yes",
            str(source),
            f"{host}:{root}/",
        ],
        check=True,
    )


def _remote_specs(host, key, root, tasks):
    """Copies the nets the tasks name (Z:<net.npz>:<simulations>) to the
    worker, and returns the tasks naming the worker's copies."""
    import hashlib

    folder = root / "eval-nets"
    subprocess.run(_ssh(host, key, ["mkdir", "-p", folder]), check=True)
    copies = {}

    def remote(spec):
        if not (isinstance(spec, str) and spec.startswith("Z:")):
            return spec
        _, path, *rest = spec.split(":")
        if path not in copies:
            with open(path, "rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()[:12]
            target = folder / f"{digest}-{Path(path).name}"
            _rsync(key, path, f"{host}:{target}")
            copies[path] = str(target)
        return ":".join(["Z", copies[path], *rest])

    return [(remote(t[0]), remote(t[1]), *t[2:]) for t in tasks]


def run_tasks(tasks, workers=None, hosts=None, log=print):
    """Plays evaluate.py's game tasks here and on the worker hosts, in
    proportion to their processes, and returns the results in task order. A
    game is a pure function of its task, so where it is played changes
    nothing. A worker's games that don't come back are played here."""
    import json
    import threading

    from catanatron.colonist import evaluate

    tasks = list(tasks)
    hosts = worker_hosts() if hosts is None else hosts
    key, root = _settings()
    local = workers or os.cpu_count() or 1
    weights = [local] + [p for _, p in hosts]
    cuts = [
        round(len(tasks) * sum(weights[:i]) / sum(weights))
        for i in range(len(weights) + 1)
    ]
    chunks = list(zip(cuts, cuts[1:]))
    remote = []
    for (host, processes), (a, b) in zip(hosts, chunks[1:]):
        if a == b:
            continue
        try:
            _sync_code(host, key, root)
            lines = "".join(
                json.dumps(list(t)) + "\n"
                for t in _remote_specs(host, key, root, tasks[a:b])
            )
        except (subprocess.CalledProcessError, OSError):
            log(f"could not prepare {host}; its games run here")
            continue
        argv = [
            root / ".venv/bin/python",
            "-m",
            "catanatron.colonist.evaluate",
            "--tasks-stdin",
            "--workers",
            processes,
        ]
        process = subprocess.Popen(
            _ssh(host, key, argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        out = {}
        thread = threading.Thread(
            target=lambda p=process, o=out, s=lines: o.update(
                zip(("stdout", "stderr"), p.communicate(s))
            )
        )
        thread.start()
        remote.append((host, (a, b), process, thread, out))
    results = [None] * len(tasks)
    a, b = chunks[0]
    for i, game in zip(range(a, b), evaluate._run(tasks[a:b], local)):
        results[i] = game
    for host, (a, b), process, thread, out in remote:
        thread.join()
        games = []
        for line in (out.get("stdout") or "").splitlines():
            try:
                games.append(evaluate.GameResult(**json.loads(line)))
            except (ValueError, TypeError):
                break
        if process.returncode or len(games) != b - a:
            log(f"{host} returned {len(games)} of {b - a} games; playing the rest here")
        for i, game in zip(range(a, b), games):
            results[i] = game
    missing = [i for i, game in enumerate(results) if game is None]
    for i, game in zip(missing, evaluate._run([tasks[i] for i in missing], local)):
        results[i] = game
    return results
