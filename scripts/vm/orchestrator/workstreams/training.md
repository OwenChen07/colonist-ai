# Workstream: training

**Aim:** a stronger net and search, measured live against people.

**Owns:** `loop.py`, the production training run (`scripts/vm/train.sh`, `workers.env`, the data window and sample), the promotion gate and guardrails, and deployments by `safe_restart_training.sh`. The net's architecture, targets and offline experiments belong to `research` (since Oct 18); the search's settings for live play (`BUDGETS`, simulations, the opening, the account env files) belong to `live-play` (since Oct 19, 9:18 AM).

**Known gaps to work on, in the lead's order:**
1. The live value error is about 0.64, against about 0.40 in self-play. The opening value error is about 0.95, no better than guessing.
2. Repeat opponents exploit the bot by racing longest road and building cities.
3. Win rate falls to 48% beyond 90 turns.

**Rules for this workstream:**
- Restart training only at a safe point (`safe_restart_training.sh`). One self-play/training experiment at a time, in the ledger first.
- Judge by the gate and guardrails, then by live results over at least 100 games.
- The `live-loss` workstream sends you findings. Weigh them against the evidence.
