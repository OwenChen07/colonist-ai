# Workstream: live-loss

**Aim:** find out exactly how people beat the bot, and turn that into concrete, testable tasks for `training` and `efficiency`.

**Owns:** analysis of `captures/` and `data/<account>/games.jsonl` (`loss_report.py`, `diagnostics.py`, `failure_set.py`, `replay.py`), the failure sets used as benchmarks, and the human data pipeline (`human_dataset.py`).

**First tasks:**
1. A loss breakdown over the last 300 ranked games across accounts: by game phase, length (90+ turns), opponent rating, repeat opponents, and the winning plan (longest road, largest army, cities, development cards).
2. For the biggest pattern, find 20 positions where the bot's move was clearly worse, and turn them into a benchmark (`failure_set.py`) the other workstreams can measure against.
3. Send each finding to the owning workstream as a message, with the evidence and a proposed metric.

**Rules for this workstream:**
- Never commit anything from `captures/`, and never quote usernames or chat in Discord posts.
- Support every claim with counts and a confidence interval.
