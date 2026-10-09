"""The network, for play: a numpy forward pass of the weights train.py exports.

A plain fully connected net: inputs -> hidden layers (ReLU) -> a policy head
(one logit per move) and a value head (tanh, the expected result for the
player to move). Play uses numpy rather than PyTorch because the net is small
and evaluated a few positions at a time, where a framework's per-call overhead
costs more than the arithmetic.

The board inputs never change during a game, so their share of the first
layer is computed once per board and cached.
"""

import numpy as np

from catanatron.colonist import encode as E


class Net:
    def __init__(self, layers, meta=None):
        """layers: [(W, b), ...] with W shaped (in, out); the last two are the
        policy and value heads, each on top of the last hidden layer."""
        self.hidden = layers[:-2]
        self.policy = layers[-2]
        self.value = layers[-1]
        self.meta = meta or {}
        w1 = self.hidden[0][0]
        self.w1_static = np.ascontiguousarray(w1[: E.STATIC_SIZE])
        self.w1_dynamic = np.ascontiguousarray(w1[E.STATIC_SIZE :])
        self._static_key = None
        self._static_out = None

    @staticmethod
    def load(path):
        """A net.npz: this fully connected net, or a graph.GraphNet when the
        file says arch "graph" (both have forward, forward_rows, evaluate)."""
        z = np.load(path, allow_pickle=False)
        if "arch" in z.files and str(z["arch"]) == "graph":
            from catanatron.colonist.graph import GraphNet

            return GraphNet.load(z)
        n = int(z["num_layers"])
        layers = [
            (z[f"w{i}"].astype(np.float32), z[f"b{i}"].astype(np.float32))
            for i in range(n)
        ]
        meta = {k: z[k] for k in z.files if k.startswith("meta_")}
        return Net(layers, meta)

    def save(self, path):
        layers = [*self.hidden, self.policy, self.value]
        arrays = {"num_layers": np.asarray(len(layers))}
        for i, (w, b) in enumerate(layers):
            arrays[f"w{i}"] = w
            arrays[f"b{i}"] = b
        np.savez(path, **arrays, **self.meta)

    def _first_layer_static(self, static):
        # One board per game: cache by identity (encode returns the cached array).
        if static is not self._static_key:
            self._static_key = static
            self._static_out = static @ self.w1_static + self.hidden[0][1]
        return self._static_out

    def forward(self, static, dynamic):
        """dynamic: (n, DYNAMIC_SIZE). Returns (logits (n, 313), values (n,))."""
        h = dynamic @ self.w1_dynamic + self._first_layer_static(static)
        np.maximum(h, 0, out=h)
        for w, b in self.hidden[1:]:
            h = h @ w + b
            np.maximum(h, 0, out=h)
        logits = h @ self.policy[0] + self.policy[1]
        values = np.tanh(h @ self.value[0] + self.value[1])[:, 0]
        return logits, values

    def forward_rows(self, statics, dynamic):
        """Like forward, but row i has its own board inputs statics[i] (rows
        from several games). Each board's first-layer share is computed once
        per call."""
        first = {}
        for st in statics:
            if id(st) not in first:
                first[id(st)] = st @ self.w1_static + self.hidden[0][1]
        h = dynamic @ self.w1_dynamic + np.stack([first[id(st)] for st in statics])
        np.maximum(h, 0, out=h)
        for w, b in self.hidden[1:]:
            h = h @ w + b
            np.maximum(h, 0, out=h)
        logits = h @ self.policy[0] + self.policy[1]
        values = np.tanh(h @ self.value[0] + self.value[1])[:, 0]
        return logits, values

    def evaluate(self, game, color, legal=None):
        """(priors over `legal` move indices, value) for one position."""
        static, dynamic = E.encode(game, color)
        logits, values = self.forward(static, dynamic[None, :].astype(np.float32))
        if legal is None:
            return logits[0], float(values[0])
        x = logits[0, legal]
        x = np.exp(x - x.max())
        return x / x.sum(), float(values[0])


def random_net(hidden=(512, 512, 512), seed=0):
    """An untrained net (for tests and benchmarks)."""
    rng = np.random.default_rng(seed)
    sizes = [E.INPUT_SIZE, *hidden]
    layers = []
    for a, b in zip(sizes, sizes[1:]):
        layers.append(
            (
                rng.normal(0, (2 / a) ** 0.5, (a, b)).astype(np.float32),
                np.zeros(b, np.float32),
            )
        )
    layers.append(
        (
            rng.normal(0, 0.01, (hidden[-1], E.NUM_ACTIONS)).astype(np.float32),
            np.zeros(E.NUM_ACTIONS, np.float32),
        )
    )
    layers.append(
        (
            rng.normal(0, 0.01, (hidden[-1], 1)).astype(np.float32),
            np.zeros(1, np.float32),
        )
    )
    return Net(layers)
