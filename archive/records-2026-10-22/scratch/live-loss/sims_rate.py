"""Search throughput (sims/s) per account from bridge.log, for E18's VM check."""

import re
import statistics

PAT = re.compile(r"\((?:win chance [^,]*, )?(\d+) ms, (\d+) sims\)")
for bot in ("bot3", "bot4", "bot5", "bot7"):
    rates = []
    with open(f"/home/owen/colonist-ai/data/{bot}/bridge.log") as f:
        for line in f:
            m = PAT.search(line)
            if not m:
                continue
            ms, sims = int(m[1]), int(m[2])
            if sims >= 1500 and ms >= 500:
                rates.append(sims / ms * 1000)
    rates = rates[-4000:]
    big = []
    with open(f"/home/owen/colonist-ai/data/{bot}/bridge.log") as f:
        for line in f:
            m = PAT.search(line)
            if m and int(m[2]) >= 1500 and int(m[2]) < 6000:
                big.append((int(m[1]), int(m[2])))
    big = big[-4000:]
    sim_cap = sum(s >= 4700 for _, s in big)
    time_cap = sum(4700 <= ms <= 5600 and s < 4700 for ms, s in big)
    print(
        f"{bot}: of {len(big)} searches with 1500-5999 sims: reached >=4700 sims "
        f"{sim_cap}, stopped by ~5 s with fewer {time_cap}; "
        f"median sims {statistics.median(s for _, s in big):.0f}, "
        f"median ms {statistics.median(ms for ms, _ in big):.0f}"
    )
    if not rates:
        print(bot, "no lines")
        continue
    q = statistics.quantiles(rates, n=20)
    hist = {}
    for r in rates:
        b = int(r // 100) * 100
        hist[b] = hist.get(b, 0) + 1
    print(
        f"{bot}: n={len(rates)} median={statistics.median(rates):.0f} "
        f"p5={q[0]:.0f} p25={q[4]:.0f} p75={q[14]:.0f} p95={q[18]:.0f}"
    )
    print("  hist(sims/s by 100):", dict(sorted(hist.items())))
