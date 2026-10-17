"""graph.forward applies the global block once per layer and broadcasts it:
each position must still get only its own global features."""

import numpy as np

from catanatron.colonist import encode as E
from catanatron.colonist import graph as G


def test_a_position_scores_the_same_alone_or_in_a_batch():
    net = G.random_graph_net(hidden=16, glob=24, layers=2, seed=5)
    x = np.random.default_rng(1).random((5, E.INPUT_SIZE)).astype(np.float32)
    logits, values = G.forward(net.params, x, net.layers)
    for i in range(len(x)):
        l1, v1 = G.forward(net.params, x[i : i + 1], net.layers)
        assert np.allclose(l1[0], logits[i], atol=1e-4)
        assert np.allclose(v1[0], values[i], atol=1e-5)
