"""A human opponent model: a net that predicts the moves people make.

The live search otherwise expects the opponent to play like the bot itself,
which a person who has met the bot before can exploit. This net is the main
net fine-tuned on the moves of the people it played (human_dataset.py's
`opponent` shards: their decisions while holding no unplayed development card,
so everything they knew is in the capture). The bridge mixes its policy into
the prior at the opponent's nodes of the search (bridge --opponent-model,
--opponent-mix; mcts.mix_logits). Values still come from the main net, so the
model's own value head barely matters: it trains with a low value weight.

Train, starting from the net the bot plays (its arch and size come from --init):

    python -m catanatron.colonist.opponent_model train \\
        data/human/train/opponent --init runs/g1/net.npz --out models/human

which runs train.py with policy-focused settings (value weight 0.1, no
auxiliary targets, a lower learning rate); extra train.py flags can follow
`--`, e.g. `-- --device cpu`. models/human/net.npz is the epoch with the best
validation loss on the games train.py sets aside.

Score both nets on the games training never reads:

    python -m catanatron.colonist.opponent_model score \\
        data/human/holdout/opponent --net runs/g1/net.npz \\
        --model models/human/net.npz --mix 0.25,0.5,0.75

which prints, for the main net, the human model and their mixtures at each
--mix: top-1 accuracy at predicting the person's move and the cross-entropy
of the move (nats, over the legal moves; lower is better), next to a uniform
guess among the legal moves. The model is worth using live when it predicts
people better than the main net does; then play with it:

    python -m catanatron.colonist.bridge runs/g1/net.npz --play \\
        --opponent-model models/human/net.npz --opponent-mix 0.5

(or OPPONENT_MODEL and OPPONENT_MIX in the bot's environment file, read by
ranked_runner.py).

Keep it current as games arrive (human_dataset.sh runs this before every
training round, in the background):

    python -m catanatron.colonist.opponent_model refresh \
        data/human/train/opponent --holdout data/human/holdout/opponent \
        --init runs/g1/best.npz --model models/human/net.npz

trains a fresh model from the current best net on all the people's moves so
far, scores it and the model in use on the same held-out moves, and replaces
the model only if the new one predicts them at least as well (cross-entropy
within --tolerance). The verdict goes to models/human/refresh.json. The
bots' runners reload a replaced model between games.
"""

import argparse
import datetime as dt
import glob
import json
import os
import shutil
import sys

import numpy as np

from catanatron.colonist import encode as E

TRAIN_DEFAULTS = {
    "--value-weight": "0.1",
    "--aux-weight": "0",
    "--lr": "3e-4",
    "--epochs": "20",
    "--batch": "256",
    "--val-fraction": "0.1",
}


def train_args(data, init, out, extra=()):
    """train.py's arguments for the opponent model; `extra` (train.py flags)
    override the defaults."""
    given = {a for a in extra if a.startswith("--")}
    args = [*data, "--out", out, "--init", init]  # --init decides the arch
    for flag, value in TRAIN_DEFAULTS.items():
        if flag not in given:
            args += [flag, value]
    return args + list(extra)


def train(data, init, out, extra=()):
    from catanatron.colonist import train as T

    return T.main(train_args(data, init, out, extra))


def shard_paths(sources):
    """Shard files from directories (their shard_*.npz) or file paths."""
    paths = []
    for source in sources:
        if os.path.isdir(source):
            paths += glob.glob(os.path.join(source, "shard_*.npz"))
        else:
            paths += glob.glob(source)
    return sorted(set(paths))


def labelled_rows(paths):
    """Yields (static, dynamic rows, legal masks, actions) per shard, for the
    rows with a move label."""
    for path in paths:
        with np.load(path) as shard:
            action = shard["action"].astype(np.int64)
            keep = action >= 0
            if not keep.any():
                continue
            static = shard["static"].astype(np.float32)
            game = shard["game"][keep]
            dynamic = shard["dynamic"][keep].astype(np.float32)
            legal = np.unpackbits(shard["legal"][keep], axis=1, count=E.NUM_ACTIONS)
        for g in np.unique(game):
            rows = game == g
            yield static[g], dynamic[rows], legal[rows].astype(bool), action[keep][rows]


def _log_softmax_legal(logits, legal):
    x = np.where(legal, logits.astype(np.float64), -np.inf)
    return x - np.logaddexp.reduce(x, axis=1, keepdims=True)


def score(paths, nets, mixes=(), batch=512):
    """{name: {"rows", "accuracy", "cross_entropy"}} for each net in `nets`
    ({name: net}), for "uniform" (a guess among the legal moves), and, when
    nets has "main" and "human", for their mixture at each of `mixes` (as
    the search uses it: (1 - mix) main + mix human)."""
    totals = {}

    def add(name, log_p, action):
        rows = np.arange(len(action))
        t = totals.setdefault(name, [0, 0, 0.0])
        t[0] += len(action)
        t[1] += int((log_p.argmax(1) == action).sum())
        t[2] += float(-log_p[rows, action].sum())

    for static, dynamic, legal, action in labelled_rows(paths):
        for start in range(0, len(action), batch):
            end = start + batch
            d, l, a = dynamic[start:end], legal[start:end], action[start:end]
            if not l[np.arange(len(a)), a].all():
                raise ValueError("a labelled move is outside its legal moves")
            add("uniform", _log_softmax_legal(np.zeros(l.shape), l), a)
            log_ps = {}
            for name, net in nets.items():
                logits, _ = net.forward(static, d)
                log_ps[name] = _log_softmax_legal(logits, l)
                add(name, log_ps[name], a)
            if "main" in log_ps and "human" in log_ps:
                for mix in mixes:
                    mixed = np.logaddexp(
                        np.log1p(-mix) + log_ps["main"], np.log(mix) + log_ps["human"]
                    )
                    add(f"mix {mix:g}", mixed, a)
    return {
        name: {
            "rows": n,
            "accuracy": correct / max(n, 1),
            "cross_entropy": loss / max(n, 1),
        }
        for name, (n, correct, loss) in totals.items()
    }


def report(scores):
    lines = [f"{'':>10}  {'moves':>7}  {'top-1':>7}  {'cross-entropy':>13}"]
    for name, s in scores.items():
        lines.append(
            f"{name:>10}  {s['rows']:>7}  {s['accuracy']:>7.1%}  "
            f"{s['cross_entropy']:>13.3f}"
        )
    return "\n".join(lines)


def refresh(data, holdout, init, model, tolerance=0.005, extra=()):
    """Train a new model and replace `model` with it unless it predicts the
    held-out moves worse. Returns the verdict (also saved next to `model`)."""
    from catanatron.colonist.net import Net

    folder = os.path.dirname(os.path.abspath(model))
    work = os.path.join(folder, "next")
    shutil.rmtree(work, ignore_errors=True)  # fresh, not resumed
    if train(data, init, work, extra):
        raise RuntimeError("training the opponent model failed")
    candidate = os.path.join(work, "net.npz")
    paths = shard_paths(holdout)
    if not paths:
        raise ValueError("no held-out opponent shards")
    nets = {"new": Net.load(candidate)}
    if os.path.exists(model):
        nets["current"] = Net.load(model)
    scores = score(paths, nets)
    new = scores["new"]["cross_entropy"]
    current = scores.get("current", {}).get("cross_entropy")
    replace = current is None or new <= current + tolerance
    if replace:
        tmp = model + ".tmp.npz"
        shutil.copyfile(candidate, tmp)
        os.replace(tmp, model)  # the bots' sync never sees half a file
    verdict = {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "init": init,
        "replaced": replace,
        "scores": {k: v for k, v in scores.items() if k != "uniform"},
    }
    with open(os.path.join(folder, "refresh.json"), "w") as stream:
        json.dump(verdict, stream, indent=2)
    return verdict


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("train", help="fine-tune the main net on people's moves")
    t.add_argument("data", nargs="+", help="opponent shard directories")
    t.add_argument("--init", required=True, help="the main net to start from")
    t.add_argument("--out", required=True)
    s = sub.add_parser("score", help="predict held-out people's moves")
    s.add_argument("data", nargs="+", help="opponent shard directories or files")
    s.add_argument("--net", required=True, help="the main net")
    s.add_argument("--model", help="the human opponent model")
    s.add_argument(
        "--mix",
        default="",
        help="comma-separated mixture weights to score too (needs --model)",
    )
    r = sub.add_parser("refresh", help="retrain; replace the model if not worse")
    r.add_argument("data", nargs="+", help="opponent shard directories")
    r.add_argument("--holdout", nargs="+", required=True)
    r.add_argument("--init", required=True, help="the main net to start from")
    r.add_argument("--model", required=True, help="the model in use")
    r.add_argument("--tolerance", type=float, default=0.005)
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in argv:
        at = argv.index("--")
        argv, extra = argv[:at], argv[at + 1 :]
    args = parser.parse_args(argv)
    if args.command == "train":
        return train(args.data, args.init, args.out, extra)
    if args.command == "refresh":
        verdict = refresh(
            args.data, args.holdout, args.init, args.model, args.tolerance, extra
        )
        new = verdict["scores"]["new"]
        print(
            f"{'replaced' if verdict['replaced'] else 'kept'} {args.model}: new "
            f"top-1 {new['accuracy']:.1%}, cross-entropy {new['cross_entropy']:.3f}"
            + (
                f" (in use: {verdict['scores']['current']['cross_entropy']:.3f})"
                if "current" in verdict["scores"]
                else ""
            )
        )
        return 0

    from catanatron.colonist.net import Net

    mixes = [float(m) for m in args.mix.split(",") if m]
    if any(not 0 < m < 1 for m in mixes):
        parser.error("--mix weights must be between 0 and 1")
    if mixes and not args.model:
        parser.error("--mix needs --model")
    paths = shard_paths(args.data)
    if not paths:
        parser.error("no shards found")
    nets = {"main": Net.load(args.net)}
    if args.model:
        nets["human"] = Net.load(args.model)
    scores = score(paths, nets, mixes)
    print(f"{len(paths)} shards")
    print(report(scores))
    return 0


if __name__ == "__main__":
    sys.exit(main())
