"""Train the network on recorded positions (PLAN.md, Phase 2).

    python -m catanatron.colonist.train data/ab2 --out models/ab2
    python -m catanatron.colonist.train data/ab2 --out models/ab2   # resumes

Learns two things at once from each position: which move the bot chose (the
policy head, cross-entropy over the legal moves) and who won (the value head,
squared error against the result for the player to move). Games, not
positions, are split between training and validation, so the validation score
shows how well the net does on boards it has never seen.

Writes to --out:
  checkpoint.pt  everything needed to resume (weights, optimizer, epoch)
  net.npz        the weights for play (catanatron.colonist.net), from the
                 epoch with the lowest validation loss
  last.npz       the weights after the latest epoch
  log.csv        losses and accuracy after every epoch

Needs PyTorch (pip install -e ".[train]"). Uses the Apple GPU (MPS) or CUDA
when there is one.
"""

import argparse
import csv
import glob
import math
import os
import random
import sys
import time

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.graph import AUX_TARGETS


def build_model(hidden):
    import torch.nn as nn

    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            sizes = [E.INPUT_SIZE, *hidden]
            self.body = nn.ModuleList(nn.Linear(a, b) for a, b in zip(sizes, sizes[1:]))
            self.policy = nn.Linear(hidden[-1], E.NUM_ACTIONS)
            self.value = nn.Linear(hidden[-1], 1)

        def forward(self, x):
            for layer in self.body:
                x = layer(x).relu()
            return self.policy(x), self.value(x).squeeze(-1).tanh()

    return Model()


def is_graph_file(path):
    with np.load(path, allow_pickle=False) as z:
        return "arch" in z.files and str(z["arch"]) == "graph"


def export(model, path, meta=None):
    """Write the weights in the layout catanatron.colonist.net reads."""
    from catanatron.colonist.net import Net

    if hasattr(model, "config"):  # graph.torch_model
        from catanatron.colonist.graph import GraphNet

        params = {
            k: (
                model.w[k].detach().cpu().numpy(),
                model.b[k].detach().cpu().numpy(),
            )
            for k in model.w
        }
        GraphNet(params, *model.config, meta).save(path)
        return

    layers = []
    for lin in [*model.body, model.policy, model.value]:
        w = lin.weight.detach().cpu().numpy().T.astype(np.float32)
        b = lin.bias.detach().cpu().numpy().astype(np.float32)
        layers.append((w, b))
    Net(layers, meta).save(path)


def load_weights(model, path):
    """Start from the weights of a net.npz (the inverse of export)."""
    import torch

    from catanatron.colonist.net import Net

    net = Net.load(path)
    if hasattr(model, "config"):  # graph.torch_model
        if (net.hidden_size, net.glob, net.layers) != model.config:
            raise ValueError(f"{path} has another graph size")
        with torch.no_grad():
            for k, (w, b) in net.params.items():
                model.w[k].copy_(torch.from_numpy(w.copy()))
                model.b[k].copy_(torch.from_numpy(b.copy()))
        return
    layers = [*net.hidden, net.policy, net.value]
    targets = [*model.body, model.policy, model.value]
    if len(layers) != len(targets):
        raise ValueError(f"{path} has {len(net.hidden)} hidden layers")
    with torch.no_grad():
        for (w, b), lin in zip(layers, targets):
            if tuple(lin.weight.shape) != w.T.shape:
                raise ValueError(
                    f"{path}: layer shape {w.T.shape} != {tuple(lin.weight.shape)}"
                )
            lin.weight.copy_(torch.from_numpy(w.T.copy()))
            lin.bias.copy_(torch.from_numpy(b.copy()))


def hidden_of(path):
    from catanatron.colonist.net import Net

    return [w.shape[1] for w, _ in Net.load(path).hidden]


def pick_device(name=None):
    import torch

    if name:
        return torch.device(name)
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def split_by_game(seeds, val_fraction):
    """True for validation games, chosen by seed so the split never changes."""
    h = (seeds * 2654435761) % 1000
    return h < val_fraction * 1000


def select_shards(paths, max_positions, seed=0):
    """A random subset of shard files holding at most about `max_positions`
    positions (rounded up to a whole shard), for a machine whose memory can't
    hold every position. Each epoch draws only --sample of them anyway. A
    path listed twice (a folder given twice, to weigh it more) can be picked
    twice."""
    order = list(paths)
    random.Random(seed).shuffle(order)
    chosen, total = [], 0
    for path in order:
        if total >= max_positions:
            break
        with np.load(path) as shard:
            total += len(shard["game"])
        chosen.append(path)
    return sorted(chosen)


def load_split(paths, val_fraction, value_mix=0.0):
    """Reads the shards into compact training and validation sets, split by
    game, as (static, train, validation, games).

    Compact, so many rounds of self-play fit in memory: legal moves stay
    packed (8 per byte), and a search policy keeps only its legal moves'
    shares, in row order (`pi`, with `pi_start` per row). Data.batch expands
    them. The value target mixes the game result with the search's value:
    (1 - value_mix) z + value_mix v, which is just z for rows without a search
    and for quick searches (`full` false: no policy target either; see
    datagen.load).

    Two passes: the first counts rows and policy entries, the second fills
    arrays allocated once at their final size, so memory never holds a second
    copy of the positions."""
    paths = sorted(paths)
    plans, sizes, games = [], {False: [0, 0], True: [0, 0]}, 0
    for path in paths:
        with np.load(path) as shard:
            seeds, game, legal = shard["seed"], shard["game"], shard["legal"]
            soft = np.full(len(game), "pi" in shard.files)
            if "full" in shard.files:
                soft &= shard["full"].astype(bool)
        is_val = split_by_game(seeds, val_fraction)[game]
        count = np.zeros(len(game), np.int64)
        if soft.any():
            bits = np.unpackbits(legal[soft], axis=1, count=E.NUM_ACTIONS)
            count[soft] = bits.sum(1)
        for val in (False, True):
            sizes[val][0] += int((is_val == val).sum())
            sizes[val][1] += int(count[is_val == val].sum())
        plans.append((is_val, soft, count))
        games += len(seeds)

    static = np.empty((games, E.STATIC_SIZE), np.float32)
    splits = {}
    for val in (False, True):
        n, n_pi = sizes[val]
        splits[val] = {
            "game": np.empty(n, np.int64),
            "dynamic": np.empty((n, E.DYNAMIC_SIZE), np.float16),
            "legal": np.empty((n, (E.NUM_ACTIONS + 7) // 8), np.uint8),
            "action": np.empty(n, np.int16),
            "value": np.empty(n, np.float32),
            "soft": np.empty(n, bool),
            "pi_start": np.empty(n, np.int64),
            "pi": np.empty(n_pi, np.float16),
            "aux": np.full((n, AUX_TARGETS), np.nan, np.float16),
        }
    at = {False: [0, 0], True: [0, 0]}
    offset = 0
    for path, (is_val, soft, count) in zip(paths, plans):
        with np.load(path) as shard:
            d = {k: shard[k] for k in shard.files}
        z = d["z"].astype(np.float32)
        v = d["v"].astype(np.float32) if "v" in d else z
        if "full" in d:
            v = np.where(d["full"].astype(bool), v, z)
        value = (1 - value_mix) * z + value_mix * v
        static[offset : offset + len(d["static"])] = d["static"]
        for val in (False, True):
            rows = np.flatnonzero(is_val == val)
            if not len(rows):
                continue
            split, (r, q) = splits[val], at[val]
            end = r + len(rows)
            split["game"][r:end] = d["game"][rows] + offset
            split["dynamic"][r:end] = d["dynamic"][rows]
            split["legal"][r:end] = d["legal"][rows]
            split["action"][r:end] = d["action"][rows]
            split["value"][r:end] = value[rows]
            split["soft"][r:end] = soft[rows]
            if "aux" in d:
                split["aux"][r:end] = d["aux"][rows]
            counts = count[rows]
            split["pi_start"][r:end] = q + np.cumsum(counts) - counts
            soft_rows = rows[soft[rows]]
            if len(soft_rows):
                mask = np.unpackbits(
                    d["legal"][soft_rows], axis=1, count=E.NUM_ACTIONS
                ).astype(bool)
                n_pi = int(counts.sum())
                split["pi"][q : q + n_pi] = d["pi"][soft_rows][mask]
            at[val] = [end, q + int(counts.sum())]
        offset += len(d["static"])
        del d
    train, val = (splits[v] if sizes[v][0] else None for v in (False, True))
    return static, train, val, games


class Data:
    """Positions on the device, compact (load_split), expanded per batch."""

    def __init__(self, static, split, device):
        import torch

        t = lambda a: torch.from_numpy(np.ascontiguousarray(a)).to(device)
        self.n = 0 if split is None else len(split["value"])
        self.static = t(static)
        self.has_soft = bool(self.n) and bool(split["soft"].any())
        self.has_aux = False
        if not self.n:
            self.value = torch.zeros(0, device=device)
            return
        self.game = t(split["game"])
        self.dynamic = t(split["dynamic"])
        self.legal = t(split["legal"])
        self.action = t(split["action"].astype(np.int64))
        self.value = t(split["value"].astype(np.float32))
        self.soft = t(split["soft"])
        self.pi_start = t(split["pi_start"])
        self.pi = t(split["pi"])
        self.aux = t(split["aux"])
        self.has_aux = bool(np.isfinite(split["aux"][:, 0]).any())
        self.shifts = torch.arange(7, -1, -1, device=device, dtype=torch.uint8)

    def batch(self, idx):
        import torch

        x = torch.cat([self.static[self.game[idx]], self.dynamic[idx].float()], dim=1)
        packed = self.legal[idx]
        legal = (packed.unsqueeze(-1) >> self.shifts) & 1
        legal = legal.reshape(len(idx), -1)[:, : E.NUM_ACTIONS].bool()
        pi = None
        if self.has_soft:
            pi = torch.zeros(len(idx), E.NUM_ACTIONS, device=x.device)
            soft = self.soft[idx]
            if soft.any():
                mask = legal[soft]
                counts = mask.sum(1)
                firsts = torch.cumsum(counts, 0) - counts
                within = torch.arange(int(counts.sum()), device=x.device)
                within = within - torch.repeat_interleave(firsts, counts)
                flat = torch.repeat_interleave(self.pi_start[idx][soft], counts)
                block = torch.zeros(len(mask), E.NUM_ACTIONS, device=x.device)
                block[mask] = self.pi[flat + within].float()
                pi[soft] = block
        aux = self.aux[idx].float() if self.has_aux else None
        return x, legal, self.action[idx], pi, self.value[idx], aux


class Augment:
    """Applies a random board symmetry to each position of a batch."""

    def __init__(self, device, generator):
        import torch

        from catanatron.colonist.symmetry import symmetries

        syms = symmetries()
        self.inputs = torch.tensor(np.stack([s.inputs for s in syms]), device=device)
        self.moves = torch.tensor(np.stack([s.moves for s in syms]), device=device)
        self.move_image = torch.tensor(
            np.stack([s.move_image for s in syms]), device=device
        )
        self.generator = generator

    def __call__(self, x, legal, action, pi, value, aux=None):
        import torch

        sym = torch.randint(0, len(self.inputs), (len(x),), generator=self.generator)
        sym = sym.to(x.device)
        x = torch.gather(x, 1, self.inputs[sym])
        moves = self.moves[sym]
        legal = torch.gather(legal, 1, moves)
        if pi is not None:
            pi = torch.gather(pi, 1, moves)
        mapped = self.move_image[sym, action.clamp(min=0)]
        action = torch.where(action >= 0, mapped, action)
        return x, legal, action, pi, value, aux  # aux: the same in every symmetry


def losses(model, x, legal, action, pi, value, aux, value_weight, aux_weight=0.0):
    """Returns (total, policy loss, value loss, correct top moves, number of
    policy targets, auxiliary loss). The auxiliary loss is over the rows that
    have auxiliary targets (NaN where a shard had none), and only for a graph
    net, whose auxiliary head nothing else uses."""
    import torch
    import torch.nn.functional as F

    use_aux = aux is not None and hasattr(model, "config")
    if use_aux:
        logits, v, predicted = model(x, aux=True)
    else:
        logits, v = model(x)
    logits = logits.masked_fill(~legal, -1e9)
    log_p = F.log_softmax(logits, dim=1)
    hard = action >= 0
    target = F.one_hot(action.clamp(min=0), E.NUM_ACTIONS).float()
    target = target * hard[:, None]
    if pi is not None:
        target = target + pi
    has_target = target.sum(1) > 0
    n_target = has_target.sum().clamp(min=1)
    policy_loss = -(target * log_p.clamp(min=-1e4)).sum() / n_target
    correct = ((logits.argmax(1) == target.argmax(1)) & has_target).sum()
    value_loss = F.mse_loss(v, value)
    total = policy_loss + value_weight * value_loss
    aux_loss = torch.zeros((), device=x.device)
    if use_aux:
        known = torch.isfinite(aux[:, 0])
        if known.any():
            aux_loss = F.mse_loss(predicted[known], aux[known])
            total = total + aux_weight * aux_loss
    return total, policy_loss, value_loss, correct, n_target, aux_loss


def model_device(model):
    return next(model.parameters()).device


def _to(batch, device):
    """A batch from data kept on another device (e.g. in memory, for the GPU)."""
    return tuple(None if t is None else t.to(device) for t in batch)


def evaluate(model, data, batch_size, value_weight):
    """(policy loss, value loss, accuracy, auxiliary loss) on `data`."""
    import torch

    model.eval()
    sums = np.zeros(6)
    with torch.no_grad():
        for start in range(0, data.n, batch_size):
            idx = torch.arange(
                start, min(start + batch_size, data.n), device=data.value.device
            )
            batch = _to(data.batch(idx), model_device(model))
            _, pl, vl, correct, nt, al = losses(model, *batch, value_weight)
            known = 0
            if batch[5] is not None:
                known = int(torch.isfinite(batch[5][:, 0]).sum())
            sums += [
                pl.item() * nt.item(),
                vl.item() * len(idx),
                correct.item(),
                nt.item(),
                al.item() * known,
                known,
            ]
    model.train()
    return (
        sums[0] / max(sums[3], 1),
        sums[1] / max(data.n, 1),
        sums[2] / max(sums[3], 1),
        sums[4] / sums[5] if sums[5] else float("nan"),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("data", nargs="+", help="directories of datagen shards")
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--hidden", default=None, help="layer sizes (default 512,512,512, or --init's)"
    )
    parser.add_argument(
        "--init", default=None, help="start from this net.npz instead of random weights"
    )
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch", type=int, default=1024)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--value-weight", type=float, default=1.0)
    parser.add_argument(
        "--value-mix",
        type=float,
        default=0.5,
        help="weight of the search's value in the value target (self-play rows)",
    )
    parser.add_argument(
        "--aux-weight",
        type=float,
        default=0.1,
        help="weight of the auxiliary targets (longest road, largest army, "
        "final points) in the loss, for a graph net and shards that have them",
    )
    parser.add_argument("--val-fraction", type=float, default=0.05)
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        help="positions per epoch, drawn afresh each epoch (default: all)",
    )
    parser.add_argument(
        "--no-augment",
        action="store_true",
        help="train without the 12 board symmetries",
    )
    parser.add_argument("--device", default=None)
    parser.add_argument(
        "--data-device",
        default=None,
        help="where the positions stay (default: --device); 'cpu' keeps them in "
        "memory and moves each batch, when the GPU can't hold them all",
    )
    parser.add_argument(
        "--arch",
        choices=("mlp", "graph"),
        default="mlp",
        help="fully connected (net.py) or board-aware graph net (graph.py); "
        "--init's file decides when given",
    )
    parser.add_argument(
        "--graph",
        default="32,64,3",
        help="graph net size: per-piece features, global features, rounds",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=0,
        help="load a random subset of the shards with about this many positions "
        "(default: all), when memory can't hold them all",
    )
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    import torch

    torch.manual_seed(args.seed)
    device = pick_device(args.device)
    paths = sorted(
        p for d in args.data for p in glob.glob(os.path.join(d, "shard_*.npz"))
    )
    if not paths:
        print("no shards found", file=sys.stderr)
        return 1
    if args.max_positions:
        paths = select_shards(paths, args.max_positions, args.seed)
    static, train_split, val_split, games = load_split(
        paths, args.val_fraction, args.value_mix
    )
    data_device = pick_device(args.data_device) if args.data_device else device
    train = Data(static, train_split, data_device)
    val = Data(static, val_split, data_device)
    del train_split, val_split
    print(
        f"{games} games, {train.n} training and {val.n} validation positions, "
        f"on {device}",
        flush=True,
    )
    baseline = float((val.value**2).mean()) if val.n else float("nan")
    print(f"validation value loss of always guessing 0: {baseline:.3f}", flush=True)

    graph = args.arch == "graph" or (args.init and is_graph_file(args.init))
    if graph:
        from catanatron.colonist import graph as G
        from catanatron.colonist.net import Net

        if args.init:
            g = Net.load(args.init)
            hidden = [g.hidden_size, g.glob, g.layers]
        else:
            hidden = [int(h) for h in args.graph.split(",")]
        model = G.torch_model(*hidden)
    else:
        if args.hidden:
            hidden = [int(h) for h in args.hidden.split(",")]
        elif args.init:
            hidden = hidden_of(args.init)
        else:
            hidden = [512, 512, 512]
        model = build_model(hidden)
    if args.init:
        load_weights(model, args.init)
    model = model.to(device)
    params = sum(p.numel() for p in model.parameters())
    opt = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay
    )
    per_epoch = min(args.sample, train.n) if args.sample else train.n
    steps_per_epoch = math.ceil(per_epoch / args.batch)
    total_steps = steps_per_epoch * args.epochs
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: 0.5 * (1 + math.cos(math.pi * min(s, total_steps) / total_steps))
    )

    os.makedirs(args.out, exist_ok=True)
    ckpt_path = os.path.join(args.out, "checkpoint.pt")
    log_path = os.path.join(args.out, "log.csv")
    first_epoch, best = 0, float("inf")
    if os.path.exists(ckpt_path):
        ckpt = torch.load(ckpt_path, map_location=device)
        if ckpt["hidden"] != hidden:
            print(
                f"{ckpt_path} has hidden={ckpt['hidden']}; use another --out",
                file=sys.stderr,
            )
            return 1
        model.load_state_dict(ckpt["model"])
        opt.load_state_dict(ckpt["opt"])
        sched.load_state_dict(ckpt["sched"])
        first_epoch = ckpt["epoch"]
        best = ckpt.get("best", float("inf"))
        print(f"resuming after epoch {first_epoch}", flush=True)
    print(f"{params:,} parameters", flush=True)
    if first_epoch == 0:
        with open(log_path, "w", newline="") as f:
            csv.writer(f).writerow(
                [
                    "epoch",
                    "train_policy",
                    "train_value",
                    "val_policy",
                    "val_value",
                    "val_accuracy",
                    "seconds",
                ]
            )

    gen = torch.Generator(device="cpu").manual_seed(args.seed)
    augment = None if args.no_augment else Augment(device, gen)
    for epoch in range(first_epoch, args.epochs):
        start = time.time()
        gen.manual_seed(args.seed * 1000 + epoch)  # resumable shuffles
        order = torch.randperm(train.n, generator=gen)[:per_epoch].to(data_device)
        sums = np.zeros(3)
        for s in range(steps_per_epoch):
            idx = order[s * args.batch : (s + 1) * args.batch]
            batch = _to(train.batch(idx), device)
            if augment:
                batch = augment(*batch)
            total, pl, vl, _, _, _ = losses(
                model, *batch, args.value_weight, args.aux_weight
            )
            opt.zero_grad(set_to_none=True)
            total.backward()
            opt.step()
            sched.step()
            sums += [pl.item(), vl.item(), 1]
        vp, vv, va, vaux = evaluate(model, val, args.batch * 4, args.value_weight)
        secs = time.time() - start
        row = [epoch + 1, sums[0] / sums[2], sums[1] / sums[2], vp, vv, va, secs]
        print(
            f"epoch {epoch + 1}: train policy {row[1]:.3f} value {row[2]:.3f} | "
            f"val policy {vp:.3f} value {vv:.3f} accuracy {va:.1%}"
            + ("" if math.isnan(vaux) else f" aux {vaux:.3f}")
            + f" | {secs:.0f} s",
            flush=True,
        )
        with open(log_path, "a", newline="") as f:
            csv.writer(f).writerow(
                [f"{x:.5g}" if isinstance(x, float) else x for x in row]
            )
        # net.npz is the epoch with the lowest validation loss so far (the
        # value head can overfit long before the policy stops improving);
        # last.npz is the latest.
        val_loss = float(vp + args.value_weight * vv)
        meta = {
            "meta_epoch": np.asarray(epoch + 1),
            "meta_val_loss": np.asarray(val_loss),
        }
        if val_loss < best:
            best = val_loss
            export(model, os.path.join(args.out, "net.npz"), meta)
        export(model, os.path.join(args.out, "last.npz"), meta)
        torch.save(
            {
                "model": model.state_dict(),
                "opt": opt.state_dict(),
                "sched": sched.state_dict(),
                "epoch": epoch + 1,
                "hidden": hidden,
                "best": best,
            },
            ckpt_path + ".tmp",
        )
        os.replace(ckpt_path + ".tmp", ckpt_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
