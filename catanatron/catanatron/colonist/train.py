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
import sys
import time

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist.datagen import load


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


def export(model, path, meta=None):
    """Write the weights in the layout catanatron.colonist.net reads."""
    from catanatron.colonist.net import Net

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


class Data:
    """Positions on the device, with board inputs gathered per batch.

    Policy targets are either the bot's move (`action`, a class index) or a
    search's improved policy (`pi`, a distribution); the value target mixes
    the game result with the search's value: (1 - value_mix) z + value_mix v,
    which is just z for rows without a search."""

    def __init__(self, d, rows, device, value_mix=0.0):
        import torch

        t = lambda a: torch.from_numpy(np.ascontiguousarray(a)).to(device)
        self.static = t(d["static"])
        self.game = t(d["game"][rows].astype(np.int64))
        self.dynamic = t(d["dynamic"][rows])
        self.legal = t(d["legal"][rows])
        self.action = t(d["action"][rows].astype(np.int64))
        z = d["z"][rows].astype(np.float32)
        v = d["v"][rows].astype(np.float32)
        self.value = t((1 - value_mix) * z + value_mix * v)
        soft = d["soft"][rows]
        self.has_soft = len(d["pi"]) > 0
        if self.has_soft:
            # pi rows for just these positions, renumbered
            used = soft >= 0
            self.pi = t(d["pi"][soft[used]])
            remap = np.full(len(rows), -1, np.int64)
            remap[used] = np.arange(used.sum())
            self.soft = t(remap)
        self.n = len(rows)

    def batch(self, idx):
        import torch

        x = torch.cat([self.static[self.game[idx]], self.dynamic[idx].float()], dim=1)
        if self.has_soft:
            soft = self.soft[idx]
            pi = torch.zeros(len(idx), E.NUM_ACTIONS, device=x.device)
            has = soft >= 0
            pi[has] = self.pi[soft[has]].float()
        else:
            pi = None
        return x, self.legal[idx], self.action[idx], pi, self.value[idx]


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

    def __call__(self, x, legal, action, pi, value):
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
        return x, legal, action, pi, value


def losses(model, x, legal, action, pi, value, value_weight):
    """Returns (total, policy loss, value loss, correct top moves, number of
    policy targets)."""
    import torch.nn.functional as F

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
    return total, policy_loss, value_loss, correct, n_target


def evaluate(model, data, batch_size, value_weight):
    import torch

    model.eval()
    sums = np.zeros(4)
    with torch.no_grad():
        for start in range(0, data.n, batch_size):
            idx = torch.arange(
                start, min(start + batch_size, data.n), device=data.value.device
            )
            _, pl, vl, correct, nt = losses(model, *data.batch(idx), value_weight)
            sums += [
                pl.item() * nt.item(),
                vl.item() * len(idx),
                correct.item(),
                nt.item(),
            ]
    model.train()
    return (
        sums[0] / max(sums[3], 1),
        sums[1] / max(data.n, 1),
        sums[2] / max(sums[3], 1),
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
    parser.add_argument("--val-fraction", type=float, default=0.05)
    parser.add_argument(
        "--no-augment",
        action="store_true",
        help="train without the 12 board symmetries",
    )
    parser.add_argument("--device", default=None)
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
    d = load(paths)
    val_game = split_by_game(d["seed"], args.val_fraction)
    is_val = val_game[d["game"]]
    train = Data(d, np.flatnonzero(~is_val), device, args.value_mix)
    val = Data(d, np.flatnonzero(is_val), device, args.value_mix)
    print(
        f"{len(d['seed'])} games, {train.n} training and {val.n} validation positions, "
        f"on {device}",
        flush=True,
    )
    baseline = float((val.value**2).mean()) if val.n else float("nan")
    print(f"validation value loss of always guessing 0: {baseline:.3f}", flush=True)
    del d

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
    steps_per_epoch = math.ceil(train.n / args.batch)
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
        order = torch.randperm(train.n, generator=gen).to(device)
        sums = np.zeros(3)
        for s in range(steps_per_epoch):
            idx = order[s * args.batch : (s + 1) * args.batch]
            batch = train.batch(idx)
            if augment:
                batch = augment(*batch)
            total, pl, vl, _, _ = losses(model, *batch, args.value_weight)
            opt.zero_grad(set_to_none=True)
            total.backward()
            opt.step()
            sched.step()
            sums += [pl.item(), vl.item(), 1]
        vp, vv, va = evaluate(model, val, args.batch * 4, args.value_weight)
        secs = time.time() - start
        row = [epoch + 1, sums[0] / sums[2], sums[1] / sums[2], vp, vv, va, secs]
        print(
            f"epoch {epoch + 1}: train policy {row[1]:.3f} value {row[2]:.3f} | "
            f"val policy {vp:.3f} value {vv:.3f} accuracy {va:.1%} | {secs:.0f} s",
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
