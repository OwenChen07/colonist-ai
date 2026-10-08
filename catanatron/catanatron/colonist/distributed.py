"""Run one self-play round across the coordinator and a private SSH worker.

Set COLONIST_WORKER_HOST to enable this from colonist.loop. Each host gets a
disjoint, shard-aligned seed range. The worker writes to its own disk; the
coordinator copies complete shards back before allowing training to start.
"""

import os
import shlex
import subprocess
import argparse
from pathlib import Path

from catanatron.colonist import selfplay
from catanatron.colonist.datagen import shard_path


SHARD_SIZE = 64


def _command(argv):
    return " ".join(shlex.quote(str(arg)) for arg in argv)


def _ssh(host, key, argv):
    return ["ssh", "-i", key, "-o", "BatchMode=yes", host, _command(argv)]


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
):
    """Generate all shards locally and remotely, then verify the whole round."""
    host = os.environ["COLONIST_WORKER_HOST"]
    key = os.path.expanduser(
        os.environ.get("COLONIST_WORKER_SSH_KEY", "~/.ssh/colonist_worker_ed25519")
    )
    root = Path(os.environ.get("COLONIST_WORKER_ROOT", "/home/owen/colonist-ai"))
    remote_workers = int(os.environ.get("COLONIST_REMOTE_WORKERS", "2"))
    if setup_noise_scale != 1.0:
        raise ValueError("distributed self-play requires the default setup noise scale")
    if games <= SHARD_SIZE or remote_workers < 1:
        raise ValueError(
            "distributed self-play needs multiple shards and a remote worker"
        )

    # Keep roughly two thirds on the 8-vCPU coordinator and one third on the
    # 4-vCPU worker. The boundary is a whole shard so no seed is run twice.
    local_games = max(SHARD_SIZE, ((games * 2 // 3) // SHARD_SIZE) * SHARD_SIZE)
    remote_seed = seed + local_games
    remote_games = games - local_games
    round_name = Path(out_dir).parent.name
    run_name = Path(out_dir).parent.parent.name
    remote = root / "distributed" / run_name / round_name
    remote_data = remote / "data"
    remote_nets = remote / "nets"
    subprocess.run(
        _ssh(host, key, ["mkdir", "-p", remote_data, remote_nets]), check=True
    )

    remote_net = remote_nets / "best.npz"
    _rsync(key, net_path, f"{host}:{remote_net}")
    remote_opponents = []
    for index, opponent in enumerate(opponents):
        if os.path.isfile(opponent):
            target = remote_nets / f"opponent_{index}.npz"
            _rsync(key, opponent, f"{host}:{target}")
            remote_opponents.append(str(target))
        else:
            remote_opponents.append(opponent)

    remote_argv = [
        root / ".venv/bin/python",
        "-m",
        "catanatron.colonist.selfplay",
        remote_net,
        remote_data,
        "--games",
        remote_games,
        "--seed",
        remote_seed,
        "--simulations",
        simulations,
        "--workers",
        remote_workers,
        "--opponents",
        ",".join(remote_opponents),
        "--opponent-rate",
        opponent_rate,
    ]
    log(f"self-play split: {local_games} games here, {remote_games} on {host}")
    os.makedirs(out_dir, exist_ok=True)
    remote_log = Path(out_dir).parent / "remote-selfplay.log"
    with open(remote_log, "a", buffering=1) as stream:
        process = subprocess.Popen(
            _ssh(host, key, remote_argv), stdout=stream, stderr=subprocess.STDOUT
        )
        try:
            selfplay.run(
                net_path,
                out_dir,
                games=local_games,
                seed=seed,
                simulations=simulations,
                workers=workers,
                opponents=opponents,
                opponent_rate=opponent_rate,
                log=log,
            )
        except BaseException:
            process.terminate()
            process.wait()
            raise
        remote_code = process.wait()

    try:
        _rsync(key, f"{host}:{remote_data}/", f"{out_dir}/")
    except subprocess.CalledProcessError:
        log("remote shard transfer failed; completing its range on the coordinator")
    if remote_code:
        log(
            f"remote worker exited with status {remote_code}; completing missing shards locally"
        )
    if not _complete(out_dir, remote_seed, remote_games):
        selfplay.run(
            net_path,
            out_dir,
            games=remote_games,
            seed=remote_seed,
            simulations=simulations,
            workers=workers,
            opponents=opponents,
            opponent_rate=opponent_rate,
            log=log,
        )
    if not _complete(out_dir, seed, games):
        raise RuntimeError("self-play round has missing shards; training cannot start")
    log(f"self-play complete across both VMs: {games} games")


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
