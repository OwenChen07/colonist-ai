"""usage: stats.py <prof_file> <regex> [callers|callees]"""

import pstats
import sys

stats = pstats.Stats(sys.argv[1])
stats.sort_stats("tottime")
mode = sys.argv[3] if len(sys.argv) > 3 else "stats"
if mode == "callers":
    stats.print_callers(sys.argv[2])
elif mode == "callees":
    stats.print_callees(sys.argv[2])
else:
    stats.print_stats(sys.argv[2])
