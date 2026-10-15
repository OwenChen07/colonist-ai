#!/usr/bin/env bash
# The graph net's training run (runs/g1), from models/graph1 (graph.py, trained
# from scratch on runs/sp1's rounds 9-14; it beat sp1's round 15 at 57%).
# For its first rounds, sp1's last self-play (rounds 13-16, played by the MLP
# nets) is mixed in, so training doesn't start from one round of its own.
# Same self-play, gate and evaluation as train-sp1.sh; the gauntlet anchors
# progress against the old line (sp1 round 16 and round 8).
# From round 14, the bot's real games against people (human_dataset.py,
# rebuilt weekly by human_dataset.sh) are mixed in as value targets only; from
# round 15 their folder is listed HUMAN_REPEAT times (the loader keeps
# duplicates), about 3% of positions at 10 instead of 0.3%. Live value error
# is scored on the held-out 15% of those games, which training never reads.
# From round 22, self-play games can start from a turn of one of those games
# (starts.py; the loop's --human-start-rate). The launch rate is 35% from
# round 32; round 31 was already running at 25%. Every self-play
# position also has auxiliary targets (longest road, largest army, final
# points), which training learns at --aux-weight 0.1 (train.py's default).
# The confidence gate uses a fixed paired match and its unrounded lower bound,
# then checks game-level human results, newly collected confirmation games,
# the fixed anchor, benchmark, and private tactical decisions. Earlier rounds
# used the strict SPRT gate; PROMOTION_GATE can override this rollout.
# From round 23, self-play's full searches get 400 simulations instead of 160
# (stronger policy targets: at 160 the search barely improved on the net),
# with 3500 games a round instead of 5000 to keep rounds about as long.
# From round 24, every round starts by adding the newest games against people
# (human_dataset.sh: value rows, starting positions, and a background retrain
# of the human opponent model the A/B bot uses).
# From round 27, a shorter, fresher window: 6 rounds instead of 8, without
# sp1's old MLP-era data (~28% of games), and the human folder 4 times instead
# of 10 (8.2% of round 30's loaded positions), so the 400-simulation targets are most
# of what training sees instead of ~10%.
# From round 58 (E9): rounds 41-56 mostly beat round 40 by 1-2.5% but no
# single 2000-game match could prove it, and every round restarted from round
# 40, so nothing added up. Now training continues from the previous round's
# net (--train-from latest; back to the best after a round below 48%), and
# the progress gate promotes at 51% against the best, playing 2000 more games
# for a near miss (50.5-52.5%); the anchor, benchmark and live-error
# guardrails still apply. PROMOTION_GATE=confidence restores the old gate.
set -euo pipefail
cd "$(dirname "$0")/../.."
init=()
[ -e runs/g1/state.json ] || init=(--init models/graph1/net.npz)
sp1=runs/sp1
human=$(printf ',data/human/train/value%.0s' $(seq "${HUMAN_REPEAT:-4}"))
starts=()
[ -e data/human/starts.jsonl.gz ] && starts=(--human-starts data/human/starts.jsonl.gz)
exec .venv/bin/python -m catanatron.colonist.loop runs/g1 "${init[@]}" \
    --rounds 1000 --games "${SELFPLAY_GAMES:-3500}" --epochs "${TRAIN_EPOCHS:-3}" --window 6 --train-sample 4000000 \
    --gate "${PROMOTION_GATE:-progress}" --promote-at 0.51 --extend-games 2000 --train-from latest --min-games 1000 --anchor runs/g1/nets/round_013.npz --anchor-games 2000 --guard-live 0.01 \
    --distribute-eval \
    --simulations "${SELFPLAY_SIMULATIONS:-400}" --full-rate 0.25 --fast-simulations 24 \
    --reference AB:2,F \
    --extra-data "${human#,}" \
    --gauntlet $sp1/nets/round_016.npz,$sp1/nets/round_008.npz \
    --live-data data/human/holdout/value "${starts[@]}" --human-start-rate "${HUMAN_START_RATE:-0.35}" --before-round scripts/vm/human_dataset.sh \
    --workers "${COLONIST_LOCAL_WORKERS:-8}" --train-threads "${COLONIST_TRAIN_THREADS:-8}"
