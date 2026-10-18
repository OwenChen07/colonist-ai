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
import signal
import subprocess
import sys
import time
from pathlib import Path

from catanatron.colonist import selfplay
from catanatron.colonist.datagen import shard_path

SHARD_SIZE = 64
# Once its own range is done, a machine takes the unstarted shards of a
# host still playing: round 69's fleet waited over 30 minutes on one 2-core
# AWS worker that had slowed to 42 s a game from 26. Seconds between looks
# (each rsyncs the running hosts' shards here).
STEAL_POLL = 180
HERE = "here"  # the coordinator, as a job's host


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
        "-o",
        "ConnectTimeout=20",
        host,
        _command(argv),
    ]


def _local(argv):
    return argv


def _rsync(key, source, destination):
    subprocess.run(
        [
            "rsync",
            "-a",
            "-e",
            f"ssh -i {shlex.quote(key)} -o BatchMode=yes -o ConnectTimeout=20",
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
    # COLONIST_EXCLUDE_HOSTS: hosts to leave out here, e.g. the GPU host while
    # it trains (the loop's early self-play), which can't hold both at once.
    excluded = {h for h in environ.get("COLONIST_EXCLUDE_HOSTS", "").split(",") if h}
    hosts = []
    for item in (spec or "").split(","):
        item = item.strip()
        if not item:
            continue
        host, _, processes = item.rpartition(":")
        if not host or not processes.isdigit() or int(processes) < 1:
            raise ValueError(f"worker {item!r} is not user@host:processes")
        if host not in excluded:
            hosts.append((host, int(processes)))
    return hosts


def selfplay_capacities(local_processes, hosts, environ=os.environ):
    """Measured games/second in hundredths, keyed by host rather than worker
    order. Without a calibration, retain the old process-based allocation."""
    spec = environ.get("COLONIST_SELFPLAY_CAPACITIES", "")
    path = environ.get("COLONIST_WORKERS_FILE")
    if path and os.path.isfile(path):
        with open(path) as stream:
            for line in stream:
                name, _, value = line.strip().partition("=")
                if name == "COLONIST_SELFPLAY_CAPACITIES":
                    spec = value
    if not spec:
        return [local_processes] + [p for _, p in hosts]
    capacity = {}
    for item in spec.split(","):
        host, sep, rate = item.strip().rpartition(":")
        if not sep or not host or not rate.isdigit() or int(rate) < 1:
            raise ValueError(f"invalid self-play capacity {item!r}")
        capacity[host] = int(rate)
    # New workers without a measured rate get a conservative cloud-core estimate.
    return [capacity.get("local", 4 * local_processes)] + [
        capacity.get(host, 4 * p) for host, p in hosts
    ]


def split(games, seed, local_processes, hosts, weights=None):
    """Shard-aligned (seed, games) ranges: the coordinator's first, then one
    per host, in proportion to measured capacity (or processes by default).
    Every range but the last is whole shards, so no seed is played twice."""
    shards = (games + SHARD_SIZE - 1) // SHARD_SIZE
    if weights is None:
        weights = [local_processes] + [p for _, p in hosts]
    if len(weights) != len(hosts) + 1 or any(w <= 0 for w in weights):
        raise ValueError("self-play weights must be positive for every worker")
    total = sum(weights)
    counts = [shards * w // total for w in weights]
    # Give spare shards to the largest fractional quotas. Round-robin from the
    # coordinator can put a full extra shard on an already slow machine.
    order = sorted(
        range(len(weights)),
        key=lambda i: (shards * weights[i] % total, -i),
        reverse=True,
    )
    for i in order[: shards - sum(counts)]:
        counts[i] += 1
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
    human_opponent=None,
    human_opponent_rate=0.0,
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
    weights = selfplay_capacities(local_processes, hosts)
    ranges = split(games, seed, local_processes, hosts, weights)
    log(f"self-play allocation weights: {weights} (local, then configured hosts)")
    capacity = dict(zip([HERE] + [host for host, _ in hosts], weights))
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
        human_opponent=human_opponent,
        human_opponent_rate=human_opponent_rate,
    )
    os.makedirs(out_dir, exist_ok=True)
    flags = [
        "--simulations",
        simulations,
        "--opponent-rate",
        opponent_rate,
        "--setup-noise-scale",
        setup_noise_scale,
        "--full-rate",
        full_rate,
    ]
    if fast_simulations:
        flags += ["--fast-simulations", fast_simulations]
    # The coordinator's own command line, for the shards it takes over from
    # a straggler once its range is done.
    local_flags = flags + ["--opponents", ",".join(map(str, opponents))]
    remote_starts = remote_nets / "starts.jsonl.gz"
    if human_starts and human_start_rate > 0:
        flags += [
            "--human-starts",
            remote_starts,
            "--human-start-rate",
            human_start_rate,
        ]
        local_flags += [
            "--human-starts",
            human_starts,
            "--human-start-rate",
            human_start_rate,
        ]
    remote_human = remote_nets / "human_opponent.npz"
    if human_opponent and human_opponent_rate > 0:
        flags += [
            "--human-opponent",
            remote_human,
            "--human-opponent-rate",
            human_opponent_rate,
        ]
        local_flags += [
            "--human-opponent",
            human_opponent,
            "--human-opponent-rate",
            human_opponent_rate,
        ]
    here = [sys.executable, "-m", "catanatron.colonist.selfplay", net_path, out_dir]

    def prepare(host):
        argv = _prepare(host, key, root, remote_data, remote_nets, net_path)
        argv += _remote_opponents(host, key, remote_nets, opponents)
        if human_starts and human_start_rate > 0:
            _rsync(key, human_starts, f"{host}:{remote_starts}")
        if human_opponent and human_opponent_rate > 0:
            _rsync(key, human_opponent, f"{host}:{remote_human}")
        return argv

    prepared = {}  # host -> the start of its self-play command
    # All hosts at once: one after another took 3.3 minutes for 22 (round 77).
    for host, argv in _each_host(prepare, [host for host, _ in hosts]):
        if argv is None:
            log(f"could not prepare {host}; its range runs elsewhere")
            continue
        prepared[host] = argv

    def launch(host, count, first, size):
        """A self-play job: a host's over SSH, or HERE's as a local process."""
        if host == HERE:
            argv = here + ["--games", size, "--seed", first]
            argv = [str(a) for a in argv + ["--workers", count] + local_flags]
            stream = open(Path(out_dir).parent / "here.log", "a", 1)
            process = subprocess.Popen(
                _local(argv),
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # to stop its pool with it
            )
        else:
            argv = prepared[host] + ["--games", size, "--seed", first]
            argv += ["--workers", count] + flags
            stream = open(Path(out_dir).parent / f"remote-{_safe(host)}.log", "a", 1)
            process = subprocess.Popen(
                _ssh(host, key, argv), stdout=stream, stderr=subprocess.STDOUT
            )
        return [host, count, first, size, process, stream]

    def stop(job):
        host, process = job[0], job[4]
        if host == HERE:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
            # Ending ssh leaves the remote self-play running.
            subprocess.run(
                _ssh(host, key, ["pkill", "-f", "[c]atanatron.colonist.selfplay"]),
                check=False,
            )
        process.wait()
        job[5].close()

    def shards_of(first, size):
        return [
            (start, min(SHARD_SIZE, first + size - start))
            for start in range(first, first + size, SHARD_SIZE)
        ]

    def wave(local_range, jobs):
        """Plays local_range here while each (host, processes, first, size)
        job runs remotely; returns the hosts that finished and were copied."""
        running = []
        # Ranges nobody plays: a host that could not be prepared, or that
        # failed. Idle machines take them before any straggler's tail.
        orphans = []
        for host, count, first, size in jobs:
            if size and host not in prepared:
                orphans.append([host, 0, first, size])
            if not size or host not in prepared:
                continue
            running.append(launch(host, count, first, size))
            log(f"self-play: {size} games (seeds {first}+) on {host}")
        # The coordinator's range is a job like the others, so that idle
        # hosts take orphans and stragglers' tails while it plays: round 72's
        # six fastest VMs sat idle 25 minutes waiting on it (load 38 on 8
        # cores), and round 71's lost shards waited 38.
        first, size = local_range
        log(f"self-play: {size} games here")
        idle = [] if size else [HERE]
        if size:
            running.append(launch(HERE, local_processes, first, size))
        wanted = shards_of(*local_range) + [
            s for _, _, first, size in jobs for s in shards_of(first, size)
        ]
        finished, taken = [], set()
        looked = time.monotonic()
        try:
            while running:
                for job in [job for job in running if job[4].poll() is not None]:
                    running.remove(job)
                    host, code = job[0], job[4].returncode
                    job[5].close()
                    if code:
                        log(
                            f"{host} exited with status {code}; its range runs elsewhere"
                        )
                        orphans.append([host, 0, job[2], job[3]])
                    if host != HERE:
                        try:
                            _rsync(key, f"{host}:{remote_data}/", f"{out_dir}/")
                        except subprocess.CalledProcessError:
                            log(
                                f"copying shards from {host} failed; its range runs elsewhere"
                            )
                            if not code:
                                orphans.append([host, 0, job[2], job[3]])
                            continue
                    if not code:
                        finished.append(host)
                        idle.append(host)
                if not running:
                    break
                if all(os.path.isfile(shard_path(out_dir, *s)) for s in wanted):
                    for job in running:
                        log(f"self-play: stopping {job[0]}, its shards are here")
                        stop(job)
                        finished.append(job[0])
                    running = []
                    break
                if idle and time.monotonic() - looked >= STEAL_POLL:
                    looked = time.monotonic()
                    for host in list(idle):
                        job = steal(host, running, taken, orphans)
                        if job:
                            idle.remove(host)
                            running.append(job)
                time.sleep(1)
        except BaseException:
            for job in running:
                stop(job)
            raise
        return [host for host in dict.fromkeys(finished) if host != HERE]

    def steal(host, running, taken, orphans=()):
        """A job for an idle host: shards of a range nobody plays, else the
        unstarted tail of the slowest job's range. Its processes play a
        job's first missing shards, so those are under way; a later one may
        get played twice, which gives the same games, and the first copy
        here is kept."""
        count = local_processes if host == HERE else dict(hosts)[host]
        if host != HERE and host not in prepared:
            return None
        best = []
        # Orphans first, so that they win ties. Nothing of theirs is under
        # way (procs 0), and the host that lost them is not asked for shards.
        for job in list(orphans) + running:
            victim, procs, first, size = job[:4]
            if victim != HERE and procs:
                try:
                    _rsync(key, f"{victim}:{remote_data}/", f"{out_dir}/")
                except subprocess.CalledProcessError:
                    continue
            missing = [
                s
                for s in shards_of(first, size)
                if not os.path.isfile(shard_path(out_dir, *s))
            ]
            unstarted = [s for s in missing[procs:] if s[0] not in taken]
            # The contiguous tail, so that one --seed/--games range covers it.
            tail = []
            for s in reversed(unstarted):
                if tail and s[0] + SHARD_SIZE != tail[0][0]:
                    break
                tail.insert(0, s)
            share = (len(tail) + 1) // 2 if procs else len(tail)
            tail = tail[-min(count, share) :] if tail else []
            if len(tail) > len(best):
                best, source = tail, victim
        verb = "takes"
        if not best:
            best, source = duplicate(host, running, taken)
            verb = "also plays"
        if not best:
            return None
        taken.update(s[0] for s in best)
        first, size = best[0][0], sum(n for _, n in best)
        log(f"self-play: {host} {verb} {size} games (seeds {first}+) from {source}")
        return launch(host, count, first, size)

    def duplicate(host, running, taken):
        """With nothing unstarted left, a shard that a host under half this
        one's capacity is playing: round 76 waited 4 minutes on a 2-core AWS
        worker's last shard (30 s a game per process) after the fleet was
        done. The slowest such host's shard; the copy that lands first is
        kept, and wave() stops the other once every shard is here."""
        best, source, slowest = [], None, capacity[host] / 2
        for victim, _, first, size in (job[:4] for job in running):
            if victim == host or capacity[victim] >= slowest:
                continue
            missing = [
                s
                for s in shards_of(first, size)
                if s[0] not in taken and not os.path.isfile(shard_path(out_dir, *s))
            ]
            if missing:
                best, source, slowest = missing[-1:], victim, capacity[victim]
        return best, source

    jobs = [
        (host, count, first, size)
        for (host, count), (first, size) in zip(hosts, remote_ranges)
    ]
    finished = wave(ranges[0], jobs)
    machines = 1 + len(finished)

    # A host that vanished (a preempted spot VM) loses its range. Share it
    # among the coordinator and the hosts that finished, by capacity, rather
    # than playing it all here after everyone else is done.
    missing = [
        first
        for first in range(seed, seed + games, SHARD_SIZE)
        if not _complete(out_dir, first, min(SHARD_SIZE, seed + games - first))
    ]
    if missing and finished:
        log(f"self-play: {len(missing)} lost shards shared with {finished}")
        weight = dict(zip([host for host, _ in hosts], weights[1:]))
        alive = [(host, count) for host, count in hosts if host in finished]
        counts = [
            size // SHARD_SIZE + (size % SHARD_SIZE > 0)
            for _, size in split(
                len(missing) * SHARD_SIZE,
                0,
                local_processes,
                alive,
                [weights[0]] + [weight[host] for host, _ in alive],
            )
        ]
        chunks, start = [], 0
        for count in counts:
            shards = missing[start : start + count]
            start += count
            # One range per machine; it may span shards already here, which
            # replay to the same games (fixed seeds), and only when a host's
            # copy-back partly worked.
            end = min(shards[-1] + SHARD_SIZE, seed + games) if shards else 0
            chunks.append((shards[0], end - shards[0]) if shards else (0, 0))
        wave(
            chunks[0],
            [
                (host, count, first, size)
                for (host, count), (first, size) in zip(alive, chunks[1:])
            ],
        )

    for first, size in remote_ranges:
        if size and not _complete(out_dir, first, size):
            log(f"self-play: {size} additional games here")
            selfplay.run(net_path, out_dir, games=size, seed=first, **options)
    if not _complete(out_dir, seed, games):
        raise RuntimeError("self-play round has missing shards; training cannot start")
    log(f"self-play complete across {machines} machines: {games} games")


def _safe(host):
    return "".join(c if c.isalnum() or c in "-." else "_" for c in host)


def _each_host(prepare, hosts, errors=(subprocess.CalledProcessError,)):
    """(host, prepare(host)) for every host, in order, with all of them run
    at once; None for a host whose prepare raised one of `errors`."""
    from concurrent.futures import ThreadPoolExecutor

    def attempt(host):
        try:
            return prepare(host)
        except errors:
            return None

    if not hosts:
        return []
    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        return list(zip(hosts, pool.map(attempt, hosts)))


def _prepare(host, key, root, remote_data, remote_nets, net_path):
    """Makes the worker's folders and copies the net; returns the start of
    its self-play command."""
    subprocess.run(
        _ssh(host, key, ["mkdir", "-p", remote_data, remote_nets]), check=True
    )
    # A worker runs one self-play job at a time, so any self-play still
    # there was left behind: a stopped early self-play or a restarted loop
    # ends its ssh, not the remote processes. Left there, they filled the
    # 2-core AWS workers' memory until a new job's pool hung (round 64).
    subprocess.run(
        _ssh(host, key, ["pkill", "-f", "[c]atanatron.colonist.selfplay"]),
        check=False,
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
            f"ssh -i {shlex.quote(key)} -o BatchMode=yes -o ConnectTimeout=20",
            str(source),
            f"{host}:{root}/",
        ],
        check=True,
    )


def _remote_specs(host, key, root, tasks):
    """Copies the nets the tasks name (Z:<net.npz>:<simulations>, or ZE:...)
    to the worker, and returns the tasks naming the worker's copies."""
    import hashlib

    folder = root / "eval-nets"
    subprocess.run(_ssh(host, key, ["mkdir", "-p", folder]), check=True)
    copies = {}

    def remote(spec):
        if not (isinstance(spec, str) and spec.split(":")[0] in ("Z", "ZE")):
            return spec
        kind, path, *rest = spec.split(":")
        if path not in copies:
            with open(path, "rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()[:12]
            target = folder / f"{digest}-{Path(path).name}"
            _rsync(key, path, f"{host}:{target}")
            copies[path] = str(target)
        return ":".join([kind, copies[path], *rest])

    return [(remote(t[0]), remote(t[1]), *t[2:]) for t in tasks]


_match_rates = {}  # "local" or host: evaluation games/s in the last call


def _match_weights(names, selfplay):
    """The hosts' evaluation speeds from earlier calls, and for hosts without
    one, their self-play capacity scaled by the median measured ratio. Round
    79's match split by self-play speed: the GPU host, which trains during
    self-play, finished in 145-278 s, the 2-core hosts in 340-774 s."""
    ratios = sorted(
        _match_rates[n] / w for n, w in zip(names, selfplay) if n in _match_rates
    )
    if not ratios:
        return selfplay
    scale = ratios[len(ratios) // 2]
    return [_match_rates.get(n, w * scale) for n, w in zip(names, selfplay)]


def run_tasks(tasks, workers=None, hosts=None, log=print):
    """Plays evaluate.py's game tasks here and on the worker hosts, and returns
    the results in task order. This machine plays its share by measured speed;
    the rest are a queue the hosts draw from, one game per process at a time,
    so they all finish within about a game of each other. A game is a pure
    function of its task, so where it is played changes nothing. Games that
    don't come back are played here."""
    import collections
    import json
    import threading

    from catanatron.colonist import evaluate

    tasks = list(tasks)
    hosts = worker_hosts() if hosts is None else hosts
    key, root = _settings()
    local = workers or os.cpu_count() or 1
    # By processes, round 78's 1000-game halves took 14-16 min: the
    # coordinator (8 processes, 0.08 games/s) got 82 games, a t2d VM (4, 0.33)
    # 41, so the t2d VMs waited on it and on the 2-core hosts.
    names = ["local"] + [host for host, _ in hosts]
    weights = _match_weights(names, selfplay_capacities(local, hosts))
    started = time.monotonic()
    here = round(len(tasks) * weights[0] / sum(weights)) if hosts else len(tasks)
    # Split by speed, round 80's identical t2d VMs still took 283-372 s for
    # 57 games each, and the 2-core hosts up to 407 s against a median of 318.
    queue = collections.deque(range(here, len(tasks)))
    lock = threading.Lock()
    results = [None] * len(tasks)

    def prepare(host):
        _sync_code(host, key, root)
        return [json.dumps(list(t)) for t in _remote_specs(host, key, root, tasks)]

    def serve(host, processes, lines, out):
        argv = [
            root / ".venv/bin/python",
            "-m",
            "catanatron.colonist.evaluate",
            "--tasks-stream",
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
        sent = set()

        def send():
            if process.stdin.closed:
                return
            with lock:
                i = queue.popleft() if queue else None
            if i is None:
                process.stdin.close()
                return
            sent.add(i)
            process.stdin.write(f"[{i}, {lines[i]}]\n")
            process.stdin.flush()

        try:
            for _ in range(processes):
                send()
            for line in process.stdout:
                try:
                    game = json.loads(line)
                    i = game.pop("i")
                    results[i] = evaluate.GameResult(**game)
                except (ValueError, TypeError, KeyError, IndexError):
                    break
                sent.discard(i)
                out["games"] += 1
                send()
        except OSError:
            pass
        process.kill()
        process.wait()
        out["seconds"] = time.monotonic() - started
        out["failed"] = bool(sent)
        with lock:  # still-running hosts may take them; the rest are played here
            queue.extend(sorted(sent))

    threads = []
    # All hosts at once, so the first ones don't finish while the last start.
    for (host, processes), (_, lines) in zip(
        hosts,
        _each_host(
            prepare, [h for h, _ in hosts], (subprocess.CalledProcessError, OSError)
        ),
    ):
        if lines is None:
            log(f"could not prepare {host}; the others play its games")
            continue
        out = {"games": 0}
        thread = threading.Thread(target=serve, args=(host, processes, lines, out))
        thread.start()
        threads.append((host, thread, out))
    for i, game in zip(range(here), evaluate._run(tasks[:here], local)):
        results[i] = game
    seconds = time.monotonic() - started
    times = [f"here {here} in {seconds:.0f} s"]
    if here:
        _match_rates["local"] = here / max(seconds, 1)
    for host, thread, out in threads:
        thread.join()
        times.append(f"{host} {out['games']} in {out['seconds']:.0f} s")
        if out["failed"]:
            log(f"{host} stopped with games unfinished; they are played elsewhere")
            _match_rates.pop(host, None)
        elif out["games"]:
            _match_rates[host] = out["games"] / max(out["seconds"], 1)
    log("evaluation games by host: " + ", ".join(times))
    missing = [i for i, game in enumerate(results) if game is None]
    for i, game in zip(missing, evaluate._run([tasks[i] for i in missing], local)):
        results[i] = game
    return results
