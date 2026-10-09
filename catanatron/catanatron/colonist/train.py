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


def load_split(paths, val_fraction, value_mix=0.0):
    """Reads the shards one at a time into compact training and validation
    sets, split by game, as (static, train, validation, games).

    Compact, so many rounds of self-play fit in memory: legal moves stay
    packed (8 per byte), and a search policy keeps only its legal moves'
    shares, in row order (`pi`, with `pi_start` per row). Data.batch expands
    them. The value target mixes the game result with the search's value:
    (1 - value_mix) z + value_mix v, which is just z for rows without a search
    and for quick searches (`full` false: no policy target either; see
    datagen.load)."""
    statics, splits, offset = [], {False: [], True: []}, 0
    for path in sorted(paths):
        with np.load(path) as shard:
            d = {k: shard[k] for k in shard.files}
        n = len(d["z"])
        z = d["z"].astype(np.float32)
        soft = np.full(n, "pi" in d)
        v = d["v"].astype(np.float32) if "v" in d else z
        if "full" in d:
            quick = ~d["full"].astype(bool)
            soft &= ~quick
            v = np.where(quick, z, v)
        value = (1 - value_mix) * z + value_mix * v
        is_val = split_by_game(d["seed"], val_fraction)[d["game"]]
        for val in (False, True):
            rows = np.flatnonzero(is_val == val)
            if not len(rows):
                continue
            legal = d["legal"][rows]
            part = {
                "game": d["game"][rows].astype(np.int64) + offset,
                "dynamic": d["dynamic"][rows],
                "legal": legal,
                "action": d["action"][rows],
                "value": value[rows],
                "soft": soft[rows],
                "count": np.zeros(len(rows), np.int64),
                "pi": np.zeros(0, np.float16),
            }
            if soft[rows].any():
                mask = np.unpackbits(
                    legal[soft[rows]], axis=1, count=E.NUM_ACTIONS
                ).astype(bool)
                part["pi"] = d["pi"][rows[soft[rows]]][mask].astype(np.float16)
                part["count"][soft[rows]] = mask.sum(1)
            splits[val].append(part)
        statics.append(d["static"])
        offset += len(d["static"])
    static = np.concatenate(statics).astype(np.float32)
    out = []
    for val in (False, True):
        parts = splits[val]
        split = {}
        # One array at a time, freeing the pieces as they are joined, so the
        # peak is not two full copies.
        for k in ("game", "dynamic", "legal", "action", "value", "soft", "count", "pi"):
            split[k] = _join([p for p in parts], k) if parts else None
        if parts:
            ends = np.cumsum(split.pop("count"))
            split["pi_start"] = ends - np.diff(ends, prepend=0)
        out.append(split if parts else None)
    return static, out[0], out[1], offset


def _join(parts, key):
    """np.concatenate of the parts' arrays under `key`, freeing each piece as
    it is copied: the result's pages fill as the pieces' are released."""
    first = parts[0][key]
    out = np.empty((sum(len(p[key]) for p in parts), *first.shape[1:]), first.dtype)
    at = 0
    for p in parts:
        piece = p.pop(key)
        out[at : at + len(piece)] = piece
        at += len(piece)
        del piece
    return out


class Data:
    """Positions on the device, compact (load_split), expanded per batch."""

    def __init__(self, static, split, device):
        import torch

        t = lambda a: torch.from_numpy(np.ascontiguousarray(a)).to(device)
        self.n = 0 if split is None else len(split["value"])
        self.static = t(static)
        self.has_soft = bool(self.n) and bool(split["soft"].any())
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
        return x, legal, self.action[idx], pi, self.value[idx]


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
    static, train_split, val_split, games = load_split(
        paths, args.val_fraction, args.value_mix
    )
    train = Data(static, train_split, device)
    val = Data(static, val_split, device)
    del train_split, val_split
    print(
        f"{games} games, {train.n} training and {val.n} validation positions, "
        f"on {device}",
        flush=True,
    )
    baseline = float((val.value**2).mean()) if val.n else float("nan")
    print(f"validation value loss of always guessing 0: {baseline:.3f}", flush=True)

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
        order = torch.randperm(train.n, generator=gen)[:per_epoch].to(device)
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
