"""Global 1v1 rank and rating per account name, from the public profile."""

import sys

from catanatron.colonist.top_pause import read_rank

for name in sys.argv[1:]:
    try:
        rank, rating = read_rank(name)
        print(f"{name}: #{rank} at {rating}")
    except Exception as error:  # noqa: BLE001
        print(f"{name}: unreadable ({error!s:.60})")
