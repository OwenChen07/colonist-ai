import pstats
import sys

s = pstats.Stats(sys.argv[1])
s.sort_stats("tottime").print_stats(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
