# colonist-ai: the orchestrator's records, archived Oct 22, 2026

A copy of the project's working records from the coordinator VM (`data/orchestrator/`, which git ignores), taken the night the project was paused (owner's decision, Oct 22, 2026, 10:11 PM Toronto). Read with `docs/PROJECT_SUMMARY.md`.

What is here:

- `experiments.md`: the experiments ledger, E1 to E93, every experiment with its hypothesis, metric, numbers, verdict and revert. The single most useful file.
- `experiments.json`: the same ledger in the form the Discord `#experiments` channel rendered.
- `journal.md`: the lead's journal: every owner decision with its time, and what was done about it.
- `reports/`: the lead's major reports to the owner (9 AM, 3 PM, 9 PM), Oct 17 to Oct 22.
- `analysis/`: the live-loss and late-game studies (losses, seat split, road races, opponent ratings, calibration, rematches, the peak-loss study, and the study of one strong opponent, name redacted to "[opponent A]").
- `workstreams/<name>/`: each worker's standing brief (`brief.md`), its running notes (`progress.md`), the messages it received (`inbox-read.md`) and a one-paragraph summary of every run (`runs/*.md`). The full run transcripts (`runs/*.log`, about 300 MB) are not here.
- `decisions.jsonl`, `answers.jsonl`: every decision put to the owner and the owner's answers.
- `usage.jsonl`: the token cost of every agent run (API-equivalent dollars).
- `rank-watch.json`: rating milestones (best rank 8, rating 2519, Tedi4960, Oct 22, 10:23 PM).
- `chat/`: the owner's messages to the lead in Discord (`in-*.md`) and the lead's replies (`*.md`); screenshots left out.
- `scratch/`: the workers' analysis scripts (Python and shell only).
- `training/log.csv`: the production loop's 232 rounds (promoted, match games, score, best round); `state.json`; `credits.json`.

Left out on purpose: captures (opponents' usernames and chat), tokens and keys, nets and training shards, images.
