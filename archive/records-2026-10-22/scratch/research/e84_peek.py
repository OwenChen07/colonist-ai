"""Peek at E84's games on the PC: play.log tail and shard count."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e54_dist import PC, ssh  # noqa: E402

print(
    ssh(
        PC,
        "tail -c 800 /home/owen/e84/play.log; echo; ls /home/owen/e84/data | wc -l",
        False,
    )
)
