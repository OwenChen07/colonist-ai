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
  net.npz        the weights for play (catanatron.colonist.net)
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
    """Positions on the device, with board inputs gathered per batch."""

    def __init__(self, d, rows, device):
        import torch

        self.static = torch.from_numpy(d["static"]).to(device)
        self.game = torch.from_numpy(d["game"][rows].astype(np.int64)).to(device)
        self.dynamic = torch.from_numpy(d["dynamic"][rows]).to(device)
        self.legal = torch.from_numpy(d["legal"][rows]).to(device)
        self.action = torch.from_numpy(d["action"][rows].astype(np.int64)).to(device)
        self.z = torch.from_numpy(d["z"][rows].astype(np.float32)).to(device)
        self.n = len(rows)

    def batch(self, idx):
        import torch

        x = torch.cat([self.static[self.game[idx]], self.dynamic[idx].float()], dim=1)
        return x, self.legal[idx], self.action[idx], self.z[idx]


def losses(model, x, legal, action, z, value_weight):
    import torch
    import torch.nn.functional as F

    logits, v = model(x)
    logits = logits.masked_fill(~legal, -1e9)
    has_target = action >= 0
    target = action.clamp(min=0)
    ce = F.cross_entropy(logits, target, reduction="none")
    n_target = has_target.sum().clamp(min=1)
    policy_loss = (ce * has_target).sum() / n_target
    correct = ((logits.argmax(1) == action) & has_target).sum()
    value_loss = F.mse_loss(v, z)
    total = policy_loss + value_weight * value_loss
    return total, policy_loss, value_loss, correct, n_target


def evaluate(model, data, batch_size, value_weight):
    import torch

    model.eval()
    sums = np.zeros(4)
    with torch.no_grad():
        for start in range(0, data.n, batch_size):
            idx = torch.arange(
                start, min(start + batch_size, data.n), device=data.z.device
            )
            _, pl, vl, correct, nt = losses(model, *data.batch(idx), value_weight)
            sums += [
                pl.item() * nt.item(),
                vl.item() * len(idx),
                correct.item(),
                nt.item(),
            ]
    model.train()
    return sums[0] / sums[3], sums[1] / data.n, sums[2] / sums[3]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("data", nargs="+", help="directories of datagen shards")
    parser.add_argument("--out", required=True)
    parser.add_argument("--hidden", default="512,512,512")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch", type=int, default=1024)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--value-weight", type=float, default=1.0)
    parser.add_argument("--val-fraction", type=float, default=0.05)
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
    train = Data(d, np.flatnonzero(~is_val), device)
    val = Data(d, np.flatnonzero(is_val), device)
    print(
        f"{len(d['seed'])} games, {train.n} training and {val.n} validation positions, "
        f"on {device}",
        flush=True,
    )
    del d

    hidden = [int(h) for h in args.hidden.split(",")]
    model = build_model(hidden).to(device)
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
    first_epoch = 0
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
    for epoch in range(first_epoch, args.epochs):
        start = time.time()
        gen.manual_seed(args.seed * 1000 + epoch)  # resumable shuffles
        order = torch.randperm(train.n, generator=gen).to(device)
        sums = np.zeros(3)
        for s in range(steps_per_epoch):
            idx = order[s * args.batch : (s + 1) * args.batch]
            total, pl, vl, _, _ = losses(model, *train.batch(idx), args.value_weight)
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
        torch.save(
            {
                "model": model.state_dict(),
                "opt": opt.state_dict(),
                "sched": sched.state_dict(),
                "epoch": epoch + 1,
                "hidden": hidden,
            },
            ckpt_path + ".tmp",
        )
        os.replace(ckpt_path + ".tmp", ckpt_path)
        export(
            model,
            os.path.join(args.out, "net.npz"),
            {"meta_epoch": np.asarray(epoch + 1)},
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
