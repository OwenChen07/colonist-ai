"""Run one self-play round across the coordinator and private SSH workers.

Set COLONIST_WORKER_HOSTS to enable this from colonist.loop, as a comma-
separated list of `user@host:processes` (COLONIST_WORKER_HOST, one host, still
works), or point COLONIST_WORKERS_FILE at a file with a COLONIST_WORKER_HOSTS=
line, which is read again every round, so workers can be added or removed
while the loop runs. Each round also copies this checkout's code to every
worker first. The round's games are a queue of small parts that every machine,
the coordinator included, draws from as its processes free up. Workers write
to their own disks; the coordinator copies each part back as it is reported,
merges them into shards, and plays any shard still missing before training
may start. A worker can be any machine reachable over SSH with the
repository at COLONIST_WORKER_ROOT (the same path on every worker) and its
virtualenv, e.g. a GCP or an Azure VM.
"""

import argparse
import collections
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from catanatron.colonist import selfplay
from catanatron.colonist.datagen import shard_path

SHARD_SIZE = 64
# Games in a queued part: a machine's last part ends about this many games
# after the others (8 s each on a t2d VM, about 30 s on a 2-core AWS one, and
# another machine also plays it once the queue is empty). Parts of 16 were
# no slower per game than whole shards (round 82, 10.142.0.8).
PART_SIZE = 8
# Parts a machine holds per self-play process: enough for its
# selfplay.STREAM_GAMES games in play (four), and one to start.
STREAM_PARTS = 5
STOP_GRACE = 20  # seconds a self-play worker gets to end before it is killed
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
    # The coordinator's own command line, with its own paths.
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

    # The round is a queue of PART_SIZE-game parts that every machine draws
    # from, one per process at a time, so all of them finish within about a
    # part of each other. Split by measured speed, round 82's identical t2d
    # VMs still ranged 7.0-9.2 s a game, and the fleet waited 2.3 minutes on
    # the last ones. A game is a pure function of its seed, so where it is
    # played changes nothing.
    parts_dir = os.path.join(out_dir, "parts")
    shards = {}  # shard -> its parts, for the shards not yet on disk
    for first in range(seed, seed + games, SHARD_SIZE):
        shard = (first, min(SHARD_SIZE, seed + games - first))
        if not os.path.isfile(shard_path(out_dir, *shard)):
            shards[shard] = selfplay.parts_of(*shard, shard[1] // PART_SIZE)
    every = [part for parts in shards.values() for part in parts]
    names = {os.path.basename(shard_path(parts_dir, *p)): p for p in every}
    # Parts in parts_dir (or merged into their shard); some may be left from
    # a restarted loop.
    arrived = {p for p in every if os.path.isfile(shard_path(parts_dir, *p))}
    queue = collections.deque(p for p in every if p not in arrived)
    total = len(every)
    playing = collections.Counter()  # part -> machines playing it
    cond = threading.Condition()
    started = time.monotonic()

    def take(state):
        """The next part for a machine: one nobody has started, else, once
        it has less than a part per process, one a single other machine is
        playing (the copy that lands first is kept), else None once all are
        here. Waits while others might fail."""
        with cond:
            while not state["dead"] and len(arrived) < total:
                while queue:
                    part = queue.popleft()
                    if part not in arrived:
                        break
                else:
                    # A busy machine's copies play beside its own parts: in
                    # round 96 (5 parts per process held) the queue was empty
                    # a third of the way in, and 6,640 games were played for
                    # 5,000.
                    part = None
                    if len(state["sent"]) < state["processes"]:
                        part = next(
                            (
                                p
                                for p, n in playing.items()
                                if n == 1
                                and p not in state["sent"]
                                and p not in arrived
                            ),
                            None,
                        )
                if part is not None:
                    playing[part] += 1
                    state["sent"].add(part)
                    return part
                cond.wait(5)
            return None

    def serve(host, processes, argv, state):
        log_path = Path(out_dir).parent / (
            "here.log" if host == HERE else f"remote-{_safe(host)}.log"
        )
        with open(log_path, "a", 1) as stream:
            if host == HERE:
                process = subprocess.Popen(
                    _local([str(a) for a in argv]),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stream,
                    text=True,
                    start_new_session=True,  # to stop its pool with it
                )
            else:
                process = subprocess.Popen(
                    _ssh(host, key, argv),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stream,
                    text=True,
                )
            state["process"] = process
            # Enough parts for each process to keep selfplay.STREAM_GAMES
            # games in play: it starts the next part as games end.
            slots = threading.Semaphore(processes * STREAM_PARTS)

            def feed():
                try:
                    while True:
                        slots.acquire()
                        part = take(state)
                        if part is None:
                            break
                        process.stdin.write(f"{part[0]} {part[1]}\n")
                        process.stdin.flush()
                    process.stdin.close()
                except (OSError, ValueError):
                    pass  # it died; serve gives its parts back

            threading.Thread(target=feed, daemon=True).start()
            try:
                for line in process.stdout:
                    part = names.get(line.strip())
                    if part is None:
                        break
                    if host != HERE and part not in arrived:
                        try:
                            name = line.strip()
                            _rsync(key, f"{host}:{remote_data}/parts/{name}", parts_dir)
                        except subprocess.CalledProcessError:
                            break
                    with cond:
                        state["sent"].discard(part)
                        playing[part] -= 1
                        if part not in arrived:
                            arrived.add(part)
                            state["games"] += part[1]
                        state["seconds"] = time.monotonic() - started
                        cond.notify_all()
                    slots.release()
            except OSError:
                pass
            with cond:  # still-running machines take them; the rest are played here
                state["dead"] = True
                for part in state["sent"]:
                    playing[part] -= 1
                    if part not in arrived and not playing[part]:
                        queue.append(part)
                state["failed"] = any(p not in arrived for p in state["sent"])
                cond.notify_all()
            for _ in range(processes):
                slots.release()
            if process.poll() is None:
                stop(host, state)
            try:
                process.wait(STOP_GRACE)
            except subprocess.TimeoutExpired:
                # A worker that outlives its stop held the round up 8 minutes
                # and stayed behind with its pool (round 85).
                if host == HERE:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait()

    def stop(host, state):
        """Ends a machine's self-play, once: its pool here, or over SSH."""
        process = state["process"]
        if state.get("stopped") or process.poll() is not None:
            return
        state["stopped"] = True
        # Its pool's task thread waits on stdin, and SIGTERM's exit waits
        # on that thread: a stopped worker hung until its input ended.
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
        if host == HERE:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
            # Ending ssh leaves the remote self-play running, and the match
            # that follows needs its cores.
            subprocess.run(
                _ssh(host, key, ["pkill", "-f", "[c]atanatron.colonist.selfplay"]),
                check=False,
            )

    def merge_ready():
        for shard, parts in list(shards.items()):
            if all(p in arrived for p in parts):
                paths = [shard_path(parts_dir, *p) for p in parts]
                selfplay.merge_parts(shard_path(out_dir, *shard), paths)
                del shards[shard]

    machines = []  # (host, thread, state)

    def launch(host, processes, argv):
        argv = argv + ["--parts-stream", "--workers", processes]
        state = {"sent": set(), "dead": False, "failed": False, "games": 0}
        state["processes"] = processes
        state["seconds"] = 0.0
        thread = threading.Thread(
            target=serve, args=(host, processes, argv, state), daemon=True
        )
        thread.start()
        machines.append((host, thread, state))

    try:
        if queue:
            os.makedirs(parts_dir, exist_ok=True)
            log(f"self-play: {total} parts of {PART_SIZE} games in a queue")
            # This machine plays while the hosts are prepared.
            launch(HERE, local_processes, here + local_flags)
            # All hosts at once: one after another took 3.3 minutes for 22
            # (round 77).
            for host, argv in _each_host(prepare, [host for host, _ in hosts]):
                if argv is None:
                    log(f"could not prepare {host}; the others play its share")
                else:
                    launch(host, dict(hosts)[host], argv + flags)
        while any(thread.is_alive() for _, thread, _ in machines):
            merge_ready()
            if len(arrived) == total:
                # Every part is here: stop the machines still playing a copy.
                for host, thread, state in machines:
                    process = state.get("process")
                    if process and not state.get("stopped") and process.poll() is None:
                        log(f"self-play: stopping {host}, its parts are here")
                        stop(host, state)
            time.sleep(1)
    except BaseException:
        for host, _, state in machines:
            if state.get("process"):
                stop(host, state)
        raise
    for _, thread, _ in machines:
        thread.join()
    merge_ready()
    if machines:
        log(
            "self-play games by host: "
            + ", ".join(
                f"{host} {state['games']} in {state['seconds']:.0f} s"
                for host, _, state in machines
            )
        )
    for host, _, state in machines:
        if state["failed"]:
            log(f"{host} stopped with parts unfinished; they were played elsewhere")

    for shard in list(shards):
        log(f"self-play: {shard[1]} additional games here")
        selfplay.run(net_path, out_dir, games=shard[1], seed=shard[0], **options)
    if not _complete(out_dir, seed, games):
        raise RuntimeError("self-play round has missing shards; training cannot start")
    worked = sum(1 for _, _, state in machines if state["games"])
    log(f"self-play complete across {worked} machines: {games} games")


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
