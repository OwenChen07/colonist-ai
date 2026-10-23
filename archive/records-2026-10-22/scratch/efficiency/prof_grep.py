"""usage: prof_grep.py <prof> <regex>: rows matching regex, with their callers."""

import pstats
import sys

s = pstats.Stats(sys.argv[1])
s.sort_stats("tottime").print_stats(sys.argv[2])
s.print_callers(sys.argv[2])
