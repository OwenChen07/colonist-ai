# Road races and repeats: Oct 13 2026 evening Toronto

Source: `.venv/bin/python -m catanatron.colonist.loss_report --last 600`.
All 2043 indexed human-game captures replayed successfully; newest 600 selected
in capture timestamp order, 1424 distinct opponents over full history. This
is human-game analysis across accounts/nets and includes casual games; it is
not the ranked-only A/B population. No opponent names or tokens recorded.

## Results (Wilson 95% intervals)
- Once-seen opponents: 141/260 wins, 54.2% [48.2, 60.2].
- Opponents seen 2–3 times: 145/244, 59.4% [53.2, 65.4].
- Opponents seen 4+ times: 48/96, 50.0% [40.2, 59.8].
- Among repeat opponents: first meeting 46/64, 71.9% [59.9, 81.4];
  meetings 2–3 111/205, 54.1% [47.3, 60.8]; meetings 4+ 36/71,
  50.7% [39.3, 62.0]. These are different pooled opponent/account/net
  groups, selected using future repeat counts; not a causal adaptation test.
- Humans winning at 15 VP (266): road 222/266, 83.5% [78.5, 87.4];
  army 155/266, 58.3% [52.3, 64.0]; VP card 240/266, 90.2%.
- Bot wins at 15 VP (218): road 154/218, 70.6% [64.3, 76.3];
  army 193/218, 88.5% [83.6, 92.1]; VP card 205/218, 94.0%.
- In human wins with road first claimed, human claimed it first 118/264,
  44.7% [38.8, 50.7]. Therefore the bot took it first in 146/264 losses
  (55.3%); many failures concern defending or retaking, not initial racing.
- If bot claimed road first: 231/377 wins, 61.3% [56.3, 66.1].
  If human first: 61/179, 34.1% [27.5, 41.3]. These associations reflect
  position strength and cannot establish that rushing road causes wins.
- Human first-road frequency: first meetings 97/301, 32.2% [27.2, 37.7];
  repeat meetings 82/255, 32.2% [26.7, 38.1]. No observed increase.

## Concrete improvement to investigate next
Allocate more existing search time to contested late road positions: both
longest-road lengths >=5, gap <=2, and either player's visible VP >=10.
The initial hypothesis is that deeper search sees road loss/retake consequences
missed by routine search. encode.py already includes both longest-road
lengths and holders, so merely adding those features would duplicate inputs.
The graph uses three message-passing layers (graph.py shapes default), which
may make long paths difficult, but that is a hypothesis, not measured evidence.

First audit paired decisions in these replay positions with incumbent 800 and
1600 simulations, bounded by the existing live time limit; record selected move,
value, and search time. Then use >=1000 paired evaluation games vs incumbent,
with anchor/live guardrails and a comparison against equal aggregate compute.
Only if that passes, run one-account ranked A/B for >=400 games, tracking
win rate/Elo and road transfers/losses from >=10 VP. Do not invent a bonus
heuristic from winner-only correlations. No implementation or deployment now:
E2 owns live play and E5 is queued in training.

## Execution note
The initial nohup invocation produced no output and its process disappeared;
a foreground tool session completed in under two minutes, exit 0. There is
no surviving analysis job to monitor; its initial PID file is historical.
