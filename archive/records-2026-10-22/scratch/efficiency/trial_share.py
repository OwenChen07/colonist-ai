"""Share of self-play games played by the nine trial VMs, per round (E22 brief)."""

import glob
import re

TRIAL = {
    "35.237.216.168",
    "34.24.234.117",
    "34.138.239.141",
    "34.23.222.238",
    "34.26.193.85",
    "35.231.252.76",
    "34.59.10.248",
    "35.223.45.100",
    "136.80.101.253",
}
pat = re.compile(r"([\w@.\-]+) (\d+) in (\d+) s")
rows = []
for path in glob.glob("runs/g1/round_*/prefetch.log"):
    r = int(path.split("round_")[1].split("/")[0])
    if r < 165:
        continue
    lines = open(path).read().splitlines()
    start = end = None
    for ln in lines:
        if "self-play games by host:" in ln:
            byhost = ln
        if f"round {r}: self-play by" in ln:
            start = ln[1:9]
        if "self-play complete" in ln:
            end = ln[1:9]
    try:
        byhost
    except NameError:
        continue
    tot = trial = 0
    trate = []
    for host, g, s in pat.findall(byhost.split("by host:")[1]):
        g, s = int(g), int(s)
        tot += g
        if host.split("@")[-1] in TRIAL:
            trial += g
            if s:
                trate.append(g / s)
    rows.append((r, tot, trial, sum(trate), start, end))
    del byhost
for r, tot, trial, rate, a, b in sorted(rows):
    print(
        f"round {r}: {tot} games, trial {trial} ({100*trial/max(tot,1):.1f}%), trial rate {rate:.2f} games/s, {a}-{b}"
    )
