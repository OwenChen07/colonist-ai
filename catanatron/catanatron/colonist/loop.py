"""The self-play training loop (PLAN.md, Phase 3: How it learns).

    python -m catanatron.colonist.loop runs/r1 --init models/ab2/net.npz --rounds 20
    python -m catanatron.colonist.loop runs/r1 --rounds 20        # resumes

Each round:

  1. Self-play: the current best net plays --games games against itself
     (and, in --opponent-rate of them, against an older version or a
     reference bot), recording its search's move odds and value.
  2. Train: a new net starts from the best one (or, with --train-from
     latest, from the previous round's new net, so small gains that no single
     match can prove still add up round after round) and trains on the last
     --window rounds of self-play data (plus --extra-data, e.g. the bot games
     Phase 2 learned from).
  3. Promotion match: new net against best on paired boards. With --gate
     progress, play --max-games games and pass at --promote-at (e.g. 51%); a
     near miss (--extend-below/--extend-above) plays --extend-games more on
     other boards and is judged on all of them. With --gate
     confidence, play a fixed --max-games games and require the unrounded
     95% lower score bound to reach 50%, plus paired game-level human and
     tactical checks. With --gate strict, play at least
     --min-games (default 1000) and at most --max-games in an SPRT of 50%
     against 54%. With --gate loose (AlphaZero-like: the data keeps moving instead of
     coming from one net for many rounds), an SPRT of 46% against 50%: the
     new net passes unless it is clearly worse. Either way, a net that
     passes is promoted in step 4 only if its guardrails hold: its
     benchmark and its live value error (--live-data) are not clearly worse
     than the best's, and, with --anchor, its score against that fixed net
     isn't either (a match against last round's net can't see a slow slide,
     or tell 1% gains from noise; the same anchor every round can).
  4. When the best net changed (or, with --gate loose, before a net that
     passed is promoted), it plays --benchmark (default AlphaBeta) on
     a fixed set of boards, to track progress toward the 65% gate, and each
     of --gauntlet (e.g. earlier rounds' nets), so a gain against one bot
     isn't mistaken for a gain overall. Every round's new net is also scored
     on --live-data, real games' value shards (calibrate.py), which shows
     whether what it learns in self-play carries over to people.

Everything lives in the run directory, and the loop can be stopped at any
time and rerun with the same directory to continue where it stopped:

  state.json            round, step, current best, history
  best.npz              the current best net
  nets/round_NNN.npz    every net trained (promoted or not)
  round_NNN/data/       that round's self-play shards
  round_NNN/train/      that round's training output
  log.csv               one row per round: promoted?, match score, benchmark

A warning is printed when nothing has been promoted for 5 rounds (PLAN.md's
sign that self-play has stalled).
"""

import argparse
import csv
import glob
import json
import math
import os
import shutil
import signal
import sys
import time

STEPS = ("selfplay", "train", "match", "benchmark", "done")
STALL_ROUNDS = 5


class Run:
    def __init__(self, root):
        self.root = root
        self.state_path = os.path.join(root, "state.json")
        self.best = os.path.join(root, "best.npz")
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                self.state = json.load(f)
        else:
            self.state = None

    def start(self, init):
        os.makedirs(os.path.join(self.root, "nets"), exist_ok=True)
        shutil.copyfile(init, self.best)
        shutil.copyfile(init, self.net(0))
        self.state = {
            "round": 1,
            "step": "selfplay",
            "best_round": 0,
            "since_promotion": 0,
            "history": [],
        }
        self.save()

    def save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp, self.state_path)

    def dir(self, n, *parts):
        return os.path.join(self.root, f"round_{n:03d}", *parts)

    def net(self, n):
        return os.path.join(self.root, "nets", f"round_{n:03d}.npz")

    def log_row(self, row):
        path = os.path.join(self.root, "log.csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)


def say(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def before_round(n, args):
    """--before-round's command (e.g. refresh the human-game data), run before
    each round's self-play. A failure is logged, not fatal: the round plays on
    with the data it has."""
    if not args.before_round:
        return
    import subprocess

    say(f"round {n}: before the round: {args.before_round}")
    try:
        subprocess.run(args.before_round, shell=True, check=True, timeout=3600)
    except (subprocess.SubprocessError, OSError) as error:
        say(f"  it failed ({error}); playing on")


def play(run, n, args):
    from catanatron.colonist import selfplay

    from catanatron.colonist import distributed

    before_round(n, args)
    runner = selfplay.run
    if distributed.worker_hosts():  # read again every round
        runner = distributed.run

    # Opponents: up to 3 recent earlier nets, plus the reference bots.
    best_round = run.state["best_round"]
    older = [run.net(k) for k in range(max(0, best_round - 3), best_round)]
    opponents = [p for p in older if os.path.exists(p)] + args.reference
    say(
        f"round {n}: self-play, {args.games} games, opponents {opponents or 'none'}, "
        f"setup noise scale {args.setup_noise_scale:g}"
    )
    runner(
        run.best,
        run.dir(n, "data"),
        games=args.games,
        seed=args.seed + n * 1_000_000,  # fresh boards every round
        simulations=args.simulations,
        workers=args.workers,
        opponents=opponents,
        opponent_rate=args.opponent_rate if opponents else 0.0,
        setup_noise_scale=args.setup_noise_scale,
        full_rate=args.full_rate,
        fast_simulations=args.fast_simulations,
        human_starts=args.human_starts or None,
        human_start_rate=args.human_start_rate if args.human_starts else 0.0,
        log=lambda m: say("  " + m),
    )


def train(run, n, args):
    import torch

    from catanatron.colonist import train as T

    # main() sets one thread for the self-play workers; training runs alone
    # here and should use the machine (bar a core for the ranked bot): with one
    # thread the graph net's epoch took 57 minutes on the cloud VM.
    torch.set_num_threads(args.train_threads)

    data = [run.dir(k, "data") for k in range(max(1, n - args.window + 1), n + 1)]
    data = [d for d in data if os.path.isdir(d)] + args.extra_data
    init, source = train_init(run, n, args)
    say(f"round {n}: training on {len(data)} data folders, from {source}")
    argv = [
        *data,
        "--out",
        run.dir(n, "train"),
        "--init",
        init,
        "--epochs",
        str(args.epochs),
        "--lr",
        str(args.lr),
    ]
    if args.device:
        argv += ["--device", args.device]
    if args.train_sample:
        argv += ["--sample", str(args.train_sample)]
    if args.train_host:
        try:
            train_remote(args, argv, data, run.dir(n, "train"), init, n)
            shutil.copyfile(run.dir(n, "train", "net.npz"), run.net(n))
            return
        except Exception as error:
            say(f"round {n}: training on {args.train_host} failed ({error}); here")
    if T.main(argv) != 0:
        raise SystemExit("training failed")
    shutil.copyfile(run.dir(n, "train", "net.npz"), run.net(n))


def train_remote(args, argv, data, out, init, n):
    """The training step on a GPU machine (--train-host, a self-play worker's
    setup: same paths under COLONIST_WORKER_ROOT): copy the code, the data
    folders and the starting net there, train with CUDA, copy the result
    back. A loaded subset (--train-max-positions) keeps it within the
    machine's memory. Any failure raises, and training happens here."""
    import subprocess

    from catanatron.colonist import distributed as D

    key, root = D._settings()
    host = args.train_host

    def remote(path):
        rel = os.path.relpath(path)
        if rel.startswith(".."):
            raise ValueError(f"{path} is outside this checkout")
        return f"{root}/{rel}"

    subprocess.run(
        [
            "ssh",
            "-i",
            key,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            host,
            "true",
        ],
        check=True,
        timeout=60,
    )
    # A long job on the GPU (e.g. growing a bigger net) holds gpu.lock there:
    # train here rather than share its memory.
    busy = subprocess.run(
        D._ssh(host, key, ["test", "-e", f"{root}/gpu.lock"]), timeout=60
    )
    if busy.returncode == 0:
        raise RuntimeError("its GPU is busy (gpu.lock)")
    D._sync_code(host, key, root)
    for folder in sorted(set(data)) + [os.path.dirname(init), out]:
        subprocess.run(D._ssh(host, key, ["mkdir", "-p", remote(folder)]), check=True)
    for folder in sorted(set(data)):
        D._rsync(key, f"{folder}/", f"{host}:{remote(folder)}/")
    D._rsync(key, init, f"{host}:{remote(init)}")
    command = [
        f"{root}/.venv/bin/python",
        "-m",
        "catanatron.colonist.train",
        *[remote(a) if a in data or a in (out, init) else a for a in argv],
        "--device",
        args.train_device,
        "--data-device",
        "cpu",
        "--max-positions",
        str(args.train_max_positions),
        "--seed",
        str(n),
    ]
    say(f"round {n}: training on {host} ({args.train_device})")
    inner = f"cd {D._command([root])} && {D._command(command)}"
    subprocess.run(D._ssh(host, key, ["bash", "-c", inner]), check=True)
    os.makedirs(out, exist_ok=True)
    D._rsync(key, f"{host}:{remote(out)}/", f"{out}/")
    if not os.path.exists(os.path.join(out, "net.npz")):
        raise RuntimeError("no net.npz came back")


def _round_setup_scale(run, n):
    path = run.dir(n, "data", "setup-exploration.json")
    if not os.path.exists(path):
        return 1.0  # data from before setup exploration was configurable
    with open(path) as stream:
        return json.load(stream)["setup_noise_scale"]


def _eval_hosts(args):
    """The self-play workers, read again now, when --distribute-eval is on."""
    if not getattr(args, "distribute_eval", False):
        return None
    from catanatron.colonist import distributed

    return distributed.worker_hosts() or None


def train_init(run, n, args):
    """(net file, description) to start round n's training from: the best
    net, or with --train-from latest the previous round's new net, unless it
    was clearly worse than the best (below --reset-below in its match) or is
    missing, so a bad round can't drag the next ones down with it."""
    if getattr(args, "train_from", "best") == "latest" and n > 1:
        previous = run.net(n - 1)
        record = next(
            (r for r in reversed(run.state["history"]) if r.get("round") == n - 1),
            None,
        )
        score = record.get("score") if record else None
        if os.path.exists(previous) and score is not None:
            if score >= args.reset_below:
                return previous, f"round {n - 1}'s net (it scored {score:.1%})"
            say(
                f"  round {n - 1} scored {score:.1%} (below {args.reset_below:.0%}); "
                "training from the best net instead"
            )
    return run.best, "the best net"


def match(run, n, args):
    from catanatron.colonist.evaluate import match as fixed_match, sprt

    # The best round's own file, not best.npz: rerunning this step after a
    # crash then still compares against the same net.
    new = f"Z:{run.net(n)}:{args.match_simulations}"
    best = f"Z:{run.net(run.state['best_round'])}:{args.match_simulations}"
    loose = args.gate == "loose"
    say(
        f"round {n}: promotion match ({args.gate} gate), new vs best "
        f"(at most {args.max_games} games)"
    )
    match_options = {
        "seed": args.seed + n * 1_000_000 + 500_000,
        "workers": args.workers,
        "hosts": _eval_hosts(args),
        "progress": lambda r: say(f"  {r.n} games: {r.score:.1%} ± {r.margin:.1%}"),
    }
    if args.gate == "confidence":
        result = fixed_match(new, best, games=args.max_games, **match_options)
        lower = result.score - result.margin
        passed = lower >= 0.50
        say(f"  unrounded 95% lower bound: {lower:.5%} (need at least 50%)")
    elif args.gate == "progress":
        result = fixed_match(new, best, games=args.max_games, **match_options)
        if args.extend_games and args.extend_below <= result.score < args.extend_above:
            # A near miss: settle it on more boards rather than one 2000-game
            # coin. The extra games use other seeds, so no board repeats.
            say(
                f"  {result.score:.1%} after {result.n} games is a near miss; "
                f"playing {args.extend_games} more"
            )
            more = fixed_match(
                new,
                best,
                games=args.extend_games,
                **{**match_options, "seed": match_options["seed"] + 250_000},
            )
            result.games.extend(more.games)
        passed = result.score >= args.promote_at
        say(
            f"  {result.score:.2%} ± {result.margin:.2%} over {result.n} games "
            f"(passes at {args.promote_at:.0%})"
        )
    else:
        result = sprt(
            new,
            best,
            p0=0.46 if loose else 0.50,
            p1=0.50 if loose else 0.54,
            max_games=args.max_games,
            min_games=args.min_games,
            batch_pairs=args.batch_pairs,
            **match_options,
        )
        passed = result.decision != "keep" if loose else result.decision == "promote"
    if passed and result.score < args.min_score:
        # Not "clearly worse" yet below the bar: e.g. 47.95% over 2000 games
        # ended undecided and was promoted, and the run slipped back.
        passed = False
        say(f"  below the minimum score {args.min_score:.0%} against the best")
    say(f"  {result.summary()}")
    record = {
        "round": n,
        "games": result.n,
        "score": round(result.score, 4),
        "decision": (
            ("promote" if passed else "keep")
            if args.gate in ("confidence", "progress")
            else (result.decision or "undecided")
        ),
        "gate": args.gate,
        "promoted": False,
        "setup_noise_scale": _round_setup_scale(run, n),
    }
    if args.gate == "confidence":
        record["margin"] = result.margin
        record["lower_bound"] = lower
    if passed:
        # Promoted in the benchmark step if the guardrails hold, so nothing
        # (such as the ranked bot) switches to a net that is then undone.
        record["passed"] = True
        what = "is not clearly worse" if loose else "met the match gate"
        say(f"  passed: round {n} {what}; checking its guardrails")
        run.state["history"].append(record)
        return
    run.state["since_promotion"] += 1
    say(f"  kept round {run.state['best_round']} as best")
    _stall_warning(run)
    run.state["history"].append(record)


def _stall_warning(run):
    if run.state["since_promotion"] >= STALL_ROUNDS:
        say(
            f"  WARNING: no promotion for {run.state['since_promotion']} rounds; "
            "PLAN.md's fallbacks: more simulations, more opponents, a bigger net"
        )


def live_calibration(run, n, args):
    """The round's new net's value against real games (cheap: no games)."""
    if not args.live_data:
        return
    from catanatron.colonist.calibrate import calibrate, describe, load_value_shards
    from catanatron.colonist.net import Net

    result = calibrate(Net.load(run.net(n)), load_value_shards(args.live_data))
    run.state["history"][-1]["live"] = result
    say(f"round {n}: {describe(result)}")


def gauntlet(run, n, args):
    from catanatron.colonist.evaluate import match as play_match

    results = {}
    for spec in args.gauntlet:
        opponent = (
            f"Z:{spec}:{args.match_simulations}" if spec.endswith(".npz") else spec
        )
        result = play_match(
            f"Z:{run.best}:{args.match_simulations}",
            opponent,
            games=args.gauntlet_games,
            seed=args.seed + 800_000_000,
            workers=args.workers,
            hosts=_eval_hosts(args),
        )
        say(f"  gauntlet: {result.summary()}")
        results[spec] = round(result.score, 4)
    run.state["history"][-1]["gauntlet"] = results


def _reference(run, key):
    """The current best net's recorded benchmark or live value error, or else
    the latest promoted net's that has one."""
    best = run.state["best_round"]
    rows = [r for r in run.state["history"] if r.get(key) not in (None, "")]
    rows.sort(key=lambda r: (r["round"] == best, r.get("promoted", False)))
    return rows[-1][key] if rows else None


def _anchor_match(run, net, args):
    from catanatron.colonist.evaluate import match as play_match

    result = play_match(
        f"Z:{net}:{args.match_simulations}",
        f"Z:{args.anchor}:{args.match_simulations}",
        games=args.anchor_games,
        seed=args.seed + 700_000_000,  # the same boards every time
        workers=args.workers,
        hosts=_eval_hosts(args),
    )
    say(f"  anchor: {result.summary()}")
    return round(result.score, 4)


def anchor_reference(run, args):
    """The current best net's score against --anchor, played once per best
    net (and again if the anchor changes)."""
    best = run.state["best_round"]
    known = run.state.get("anchor") or {}
    if known.get("round") != best or known.get("net") != args.anchor:
        say(f"  anchor: scoring the best net (round {best}) first")
        known = {
            "round": best,
            "net": args.anchor,
            "score": _anchor_match(run, run.net(best), args),
        }
        run.state["anchor"] = known
        run.save()
    return known["score"]


def guardrails(run, n, args, record):
    """For a net that passed its match: (ok, reasons). Its benchmark, its
    live value error and its score against --anchor must not be clearly
    worse than the current best's."""
    reasons = []
    if args.anchor:
        reference = anchor_reference(run, args)
        record["anchor"] = _anchor_match(run, run.net(n), args)
        record["best_anchor"] = reference
        if record["anchor"] < reference - args.guard_anchor:
            reasons.append(f"anchor {record['anchor']} vs best's {reference}")
    bench = _reference(run, "benchmark")
    if args.gate == "confidence" and args.benchmark and bench is None:
        reasons.append("best model's benchmark reference unavailable")
    if args.benchmark and bench and record.get("benchmark"):
        if float(record["benchmark"]) < float(bench) - args.guard_benchmark:
            reasons.append(f"benchmark {record['benchmark']} vs best's {bench}")
    if args.gate == "confidence" and not args.live_data:
        reasons.append("held-out human games are required for the confidence gate")
    if args.live_data:
        from catanatron.colonist.calibrate import calibrate, load_value_shards
        from catanatron.colonist.net import Net

        data = load_value_shards(args.live_data)
        if data and record.get("live", {}).get("mse") is not None:
            best = calibrate(Net.load(run.net(run.state["best_round"])), data)
            record["best_live_mse"] = best.get("mse")
            record["best_live_accuracy"] = best.get("accuracy")
            if args.gate == "confidence":
                from catanatron.colonist.calibrate import paired_game_comparison

                new_net = Net.load(run.net(n))
                best_net = Net.load(run.net(run.state["best_round"]))
                cursor = run.state["human_confirmation_after_ns"]
                historical = [
                    path
                    for path in human_value_paths(args.live_data)
                    if os.stat(path).st_mtime_ns <= cursor
                ]
                paired = paired_game_comparison(
                    new_net, best_net, load_value_shards_from_paths(historical)
                )
                record["human_paired"] = paired
                if paired["games"] < args.min_human_games:
                    reasons.append("too few independent held-out human games")
                elif (
                    paired["mse"]["improvement"] <= 0
                    or paired["accuracy"]["improvement"] <= 0
                    or max(paired["mse"]["lower"], paired["accuracy"]["lower"]) <= 0
                ):
                    reasons.append(
                        "no game-level evidence of better human loss and accuracy"
                    )
                fresh_paths = fresh_human_paths(args.live_data, cursor)
                record["fresh_human_games"] = len(fresh_paths)
                if len(fresh_paths) < args.min_fresh_human_games:
                    reasons.append("awaiting newly collected human confirmation games")
                else:
                    fresh = paired_game_comparison(
                        new_net, best_net, load_value_shards_from_paths(fresh_paths)
                    )
                    record["fresh_human_paired"] = fresh
                    # Consume this prospective set once: later candidates
                    # need newly arriving games, not the same confirmation.
                    run.state["human_confirmation_after_ns"] = max(
                        os.stat(path).st_mtime_ns for path in fresh_paths
                    )
                    if (
                        fresh["mse"]["lower"] < -0.01
                        or fresh["accuracy"]["lower"] < -0.03
                    ):
                        reasons.append("fresh human games show a possible regression")
            elif record["live"]["mse"] > best["mse"] + args.guard_live:
                reasons.append(
                    f"live value MSE {record['live']['mse']} vs best's {best['mse']}"
                )
        elif args.gate == "confidence":
            reasons.append("held-out human scores are unavailable")
    if args.gate == "confidence":
        if args.anchor and "anchor" not in record:
            reasons.append("anchor score unavailable")
        if args.benchmark and not record.get("benchmark"):
            reasons.append("benchmark score unavailable")
        from catanatron.colonist.bonus_audit import indexed_paths
        from catanatron.colonist.failure_set import compare

        if not os.path.isfile(args.tactical_index):
            reasons.append("private tactical index unavailable")
        else:
            paths = indexed_paths(
                args.tactical_index, args.tactical_captures, "holdout", 400
            )
            tactical = compare(
                run.net(run.state["best_round"]),
                run.net(n),
                paths,
                simulations=args.match_simulations,
                max_positions=700,
            )
            record["tactical"] = tactical
            if tactical.get("positions", 0) < args.min_tactical_positions:
                reasons.append("too few verified tactical decisions")
            for key in ("immediate_win_missed", "last_road_no_immediate_gain_chosen"):
                if tactical.get("candidate_" + key, 0) > tactical.get(
                    "baseline_" + key, 0
                ):
                    reasons.append("tactical regression: " + key)
    return not reasons, reasons


def fresh_human_paths(folders, after_ns):
    """Human value shards exported after this gate was enabled/last checked."""
    return [
        path
        for path in human_value_paths(folders)
        if os.stat(path).st_mtime_ns > after_ns
    ]


def human_value_paths(folders):
    return sorted(
        path for folder in folders for path in glob.glob(os.path.join(folder, "*.npz"))
    )


def load_value_shards_from_paths(paths):
    # The existing loader accepts folders; a temporary link tree would make
    # this less transparent, so load the same five arrays directly instead.
    import numpy as np

    parts = []
    for path in paths:
        with np.load(path) as d:
            parts.append(
                tuple(
                    d[name].astype(dtype)
                    for name, dtype in (
                        ("static", np.float32),
                        ("dynamic", np.float32),
                        ("game", np.int64),
                        ("z", np.float32),
                        ("turn", np.int64),
                    )
                )
            )
    return parts


def benchmark(run, n, args):
    record = run.state["history"][-1]
    record.setdefault("benchmark", "")
    live_calibration(run, n, args)
    candidate = record.get("passed", False)
    # Only when the best net changed, a net is a candidate, or the first
    # round: otherwise the same net would replay the same boards.
    if (
        args.benchmark
        and not n % args.benchmark_every
        and (record["promoted"] or candidate or n == 1)
    ):
        _benchmark(run, n, args, record, run.net(n) if candidate else run.best)
    if candidate:
        ok, reasons = guardrails(run, n, args, record)
        if ok:
            shutil.copyfile(run.net(n), run.best)
            run.state["best_round"] = n
            run.state["since_promotion"] = 0
            record["promoted"] = True
            if "anchor" in record:
                run.state["anchor"] = {
                    "round": n,
                    "net": args.anchor,
                    "score": record["anchor"],
                }
            say(f"  promoted: round {n} is the new best (guardrails held)")
        else:
            record["guardrails"] = reasons
            run.state["since_promotion"] += 1
            say(
                f"  kept round {run.state['best_round']} as best: " + "; ".join(reasons)
            )
            _stall_warning(run)
    if record["promoted"] and args.gauntlet:
        gauntlet(run, n, args)


def _benchmark(run, n, args, record, net):
    from catanatron.colonist.evaluate import match as play_match

    say(
        f"round {n}: benchmark, {net} vs {args.benchmark}, {args.benchmark_games} games"
    )
    result = play_match(
        f"Z:{net}:{args.match_simulations}",
        args.benchmark,
        games=args.benchmark_games,
        seed=args.seed + 900_000_000,  # the same boards every time, to compare rounds
        workers=args.workers,
        hosts=_eval_hosts(args),
        diagnostics=True,
    )
    say(f"  {result.summary()}")
    record["benchmark"] = f"{result.score:.4f}"
    from catanatron.colonist.diagnostics import summary

    rows = [
        {"color": "RED", "score": game.score, **game.diagnostics["RED"]}
        for game in result.games
    ]
    diagnostic_summary = summary(rows)
    record["diagnostics"] = diagnostic_summary
    path = run.dir(n, "benchmark-diagnostics.json")
    with open(path, "w") as stream:
        json.dump(diagnostic_summary, stream, indent=2)
    say(f"  diagnostics: {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dir")
    parser.add_argument("--init", help="starting net (Phase 2's), for a new run")
    parser.add_argument("--rounds", type=int, default=10, help="stop after this round")
    parser.add_argument(
        "--games", type=int, default=5000, help="self-play games per round"
    )
    parser.add_argument(
        "--simulations", type=int, default=50, help="per move in self-play"
    )
    parser.add_argument(
        "--setup-noise-scale",
        type=float,
        default=1.0,
        help="self-play Gumbel noise scale for initial settlements (default 1)",
    )
    parser.add_argument(
        "--full-rate",
        type=float,
        default=1.0,
        help="share of self-play moves with the full search (playout cap "
        "randomization; the rest get --fast-simulations and no policy target)",
    )
    parser.add_argument("--fast-simulations", type=int, default=None)
    parser.add_argument("--opponent-rate", type=float, default=0.2)
    parser.add_argument(
        "--reference",
        default="AB:2",
        help="comma-separated bot specs used as opponents",
    )
    parser.add_argument(
        "--window", type=int, default=10, help="rounds of data to train on"
    )
    parser.add_argument("--extra-data", default="", help="comma-separated data folders")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument(
        "--train-threads",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 1),
        help="PyTorch threads for training (default: all cores but one)",
    )
    parser.add_argument(
        "--train-sample",
        type=int,
        default=0,
        help="positions drawn per epoch from the window (default: all of them)",
    )
    parser.add_argument(
        "--train-host",
        default=os.environ.get("COLONIST_TRAIN_HOST", ""),
        help="user@host with a GPU to run the training step on (a self-play "
        "worker's setup); training falls back to here if it fails",
    )
    parser.add_argument("--train-device", default="cuda")
    parser.add_argument(
        "--train-max-positions",
        type=int,
        default=4_000_000,
        help="positions --train-host loads (its memory limit)",
    )
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--device", default=None)
    parser.add_argument("--match-simulations", type=int, default=100)
    parser.add_argument("--max-games", type=int, default=2000)
    parser.add_argument(
        "--promote-at",
        type=float,
        default=0.51,
        help="progress gate: the score against the best that passes the match",
    )
    parser.add_argument(
        "--extend-games",
        type=int,
        default=0,
        help="progress gate: extra games for a near miss (0: none)",
    )
    parser.add_argument("--extend-below", type=float, default=0.505)
    parser.add_argument("--extend-above", type=float, default=0.525)
    parser.add_argument(
        "--train-from",
        choices=("best", "latest"),
        default="best",
        help="start each round's training from the best net, or from the "
        "previous round's new net (gains add up without promotions)",
    )
    parser.add_argument(
        "--reset-below",
        type=float,
        default=0.48,
        help="--train-from latest: go back to the best net after a round "
        "that scored below this against it",
    )
    parser.add_argument(
        "--min-games",
        type=int,
        default=1000,
        help="minimum games in each promotion match (default: 1000)",
    )
    parser.add_argument(
        "--distribute-eval",
        action="store_true",
        help="play promotion matches and benchmarks on the self-play workers too",
    )
    parser.add_argument(
        "--gate",
        choices=("strict", "loose", "confidence", "progress"),
        default="strict",
        help="strict: SPRT clearly better; loose: SPRT not clearly worse; "
        "confidence: fixed paired match with unrounded 95%% lower bound >=50%% "
        "and human, anchor, benchmark, tactical guardrails",
    )
    parser.add_argument("--min-human-games", type=int, default=100)
    parser.add_argument("--min-fresh-human-games", type=int, default=30)
    parser.add_argument("--min-tactical-positions", type=int, default=200)
    parser.add_argument("--tactical-index", default="data/human/index.jsonl")
    parser.add_argument("--tactical-captures", default="captures")
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="a new net must also score at least this against the best "
        "(e.g. 0.5) to be promoted",
    )
    parser.add_argument(
        "--guard-benchmark",
        type=float,
        default=0.03,
        help="largest benchmark drop allowed below the best's",
    )
    parser.add_argument(
        "--guard-live",
        type=float,
        default=0.02,
        help="largest live value MSE rise allowed over the best's",
    )
    parser.add_argument(
        "--anchor",
        default="",
        help="a fixed net (.npz) every net that passes its match also plays; "
        "it must not score clearly worse against it than the best did",
    )
    parser.add_argument("--anchor-games", type=int, default=2000)
    parser.add_argument(
        "--guard-anchor",
        type=float,
        default=0.02,
        help="largest drop allowed below the best's score against --anchor",
    )
    parser.add_argument("--batch-pairs", type=int, default=50)
    parser.add_argument("--benchmark", default="AB:2")
    parser.add_argument("--benchmark-every", type=int, default=1)
    parser.add_argument("--benchmark-games", type=int, default=400)
    parser.add_argument(
        "--gauntlet",
        default="",
        help="comma-separated nets (.npz) or bot specs the new best also plays",
    )
    parser.add_argument("--gauntlet-games", type=int, default=200)
    parser.add_argument(
        "--live-data",
        default="",
        help="comma-separated folders of live value shards, to score each net on",
    )
    parser.add_argument(
        "--before-round",
        default="",
        help="a shell command run before each round's self-play (failures are "
        "logged, not fatal), e.g. scripts/vm/human_dataset.sh",
    )
    parser.add_argument(
        "--human-starts",
        default="",
        help="a starts.py file: some self-play games start from real games",
    )
    parser.add_argument(
        "--human-start-rate",
        type=float,
        default=0.25,
        help="share of self-play games from --human-starts",
    )
    parser.add_argument("--seed", type=int, default=3_000_000_000)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args(argv)
    if not 0 <= args.human_start_rate <= 1:
        parser.error("--human-start-rate must be in [0, 1]")
    if not math.isfinite(args.setup_noise_scale) or args.setup_noise_scale <= 0:
        parser.error("--setup-noise-scale must be positive and finite")
    if not 0 < args.full_rate <= 1:
        parser.error("--full-rate must be in (0, 1]")
    if args.full_rate < 1 and not args.fast_simulations:
        parser.error("--full-rate below 1 needs --fast-simulations")
    args.reference = [r for r in args.reference.split(",") if r]
    args.extra_data = [d for d in args.extra_data.split(",") if d]
    args.gauntlet = [g for g in args.gauntlet.split(",") if g]
    args.live_data = [d for d in args.live_data.split(",") if d]
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(var, "1")

    run = Run(args.run_dir)
    if run.state is None:
        if not args.init:
            parser.error("a new run needs --init <net.npz>")
        os.makedirs(args.run_dir, exist_ok=True)
        run.start(args.init)
        say(f"new run in {args.run_dir} from {args.init}")
    else:
        s = run.state
        say(
            f"resuming {args.run_dir}: round {s['round']}, step {s['step']}, "
            f"best from round {s['best_round']}"
        )

    if args.gate == "confidence" and "human_confirmation_after_ns" not in run.state:
        # Start a prospective set at rollout. Existing games have already
        # influenced past model selection and cannot serve as fresh evidence.
        paths = [
            path
            for folder in args.live_data
            for path in glob.glob(os.path.join(folder, "*.npz"))
        ]
        run.state["human_confirmation_after_ns"] = max(
            (os.stat(path).st_mtime_ns for path in paths), default=time.time_ns()
        )
        run.save()

    actions = {"selfplay": play, "train": train, "match": match, "benchmark": benchmark}
    while run.state["round"] <= args.rounds:
        n = run.state["round"]
        step = run.state["step"]
        if step == "done":
            record = run.state["history"][-1]
            run.log_row(
                {
                    "round": n,
                    "promoted": record["promoted"],
                    "match_games": record["games"],
                    "match_score": record["score"],
                    "best_round": run.state["best_round"],
                    "benchmark": record.get("benchmark", ""),
                }
            )
            run.state["round"] = n + 1
            run.state["step"] = "selfplay"
            run.save()
            continue
        actions[step](run, n, args)
        run.state["step"] = STEPS[STEPS.index(step) + 1]
        run.save()
    say(
        f"finished round {args.rounds}; best net: {run.best} (round {run.state['best_round']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
