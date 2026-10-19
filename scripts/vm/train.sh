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
# guardrails still apply.
# Oct 15: the default is back to the confidence gate (a fixed 2000-game match
# whose 95% lower bound must reach 50%, plus anchor, benchmark and tactical
# checks); its held-out human-game check now asks only that a net is not
# clearly worse (loop.py). Warm starts stay.
# Also Oct 15: --prefetch starts the next round's self-play while this round
# trains and plays its match (the workers sat idle during training), and
# --futility-games 1000 ends a match early when the net clearly can't pass.
# Oct 15 night: --selfplay-from latest (self-play by the newest trained net
# unless it scored below 48%, not the promoted one: rounds 57-59 all learned
# from round 40's games and stalled) and --match-simulations 200 (promotion,
# anchor and benchmark matches at 200 simulations a move instead of 100,
# closer to live play; the anchor reference is played again at 200).
# PROMOTION_GATE=progress uses the 51% gate instead.
# Oct 16 (E12): --train-ema 0.9997 trains with a moving average of the
# weights (about the last epoch's worth of steps), which is validated and
# exported as the round's net: KataGo averages weight snapshots the same way.
# TRAIN_WINDOW (E37) sets how many rounds of self-play training reads (6
# unless set). The GPU host loads at most 4M positions (loop.py
# --train-max-positions, its memory), drawn by shard afresh each round, so a
# wider window means each round trains on a different subset, not on more.
# Oct 18 (E37 go, lead 8:25 PM): TRAIN_SAMPLE (--train-sample) and
# TRAIN_MAX_POSITIONS (--train-max-positions) set how many positions a round
# draws and the GPU host loads (4M each unless set); the cloud GPU VM (29 GB)
# fits 12M, so E37 sets TRAIN_WINDOW=18 and both to 12000000.
# Oct 18 (E38, E39; owner): the progress gate (PROMOTION_GATE=progress) plays
# 4000 games; 52% passes, 50-52% plays 4000 more and passes at 51% of all
# 8000 (± 1.1%), under 50% fails. The confidence gate's 4000-game match would
# extend from the same flags. Every round now trains from the best net
# (--train-from best): chained nets drifted (late live error up about 0.003 a
# round, E31), so each round is the best net plus one clean step.
# Oct 19 (lead): a worker host that can't be prepared or drops out mid-round
# starts gcp_start_spot.sh in the background at once (distributed.py, at most
# once per host in 10 min), not only at the next round's start.
set -euo pipefail
cd "$(dirname "$0")/../.."
export COLONIST_RESTART_SCRIPT="$PWD/scripts/vm/gcp_start_spot.sh"
init=()
[ -e runs/g1/state.json ] || init=(--init models/graph1/net.npz)
sp1=runs/sp1
human=$(printf ',data/human/train/value%.0s' $(seq "${HUMAN_REPEAT:-4}"))
# E14: the moves people made in the games they won against the bot, as move
# targets only, listed HUMAN_POLICY_REPEAT times (3 is about 9% of the move
# targets in a round). Off (0) unless set. E56: HUMAN_POLICY_DIR picks the
# folder (policy_won_exact adds the turns where the person's development-card
# hand is known exactly; policy_won has the card-free turns only).
for ((i = 0; i < ${HUMAN_POLICY_REPEAT:-0}; i++)); do human+=",${HUMAN_POLICY_DIR:-data/human/train/policy_won}"; done
starts=()
[ -e data/human/starts.jsonl.gz ] && starts=(--human-starts data/human/starts.jsonl.gz)
# E13: a HUMAN_OPPONENT_RATE share of self-play games has one seat playing
# the human opponent model's moves, no search. Off (0) unless set.
humanseat=()
[ "${HUMAN_OPPONENT_RATE:-0}" != 0 ] && humanseat=(--human-opponent "${HUMAN_OPPONENT:-models/human/net.npz}" --human-opponent-rate "$HUMAN_OPPONENT_RATE")
exec .venv/bin/python -m catanatron.colonist.loop runs/g1 "${init[@]}" \
    --rounds 1000 --games "${SELFPLAY_GAMES:-3500}" --epochs "${TRAIN_EPOCHS:-3}" --window "${TRAIN_WINDOW:-6}" --train-sample "${TRAIN_SAMPLE:-4000000}" --train-max-positions "${TRAIN_MAX_POSITIONS:-4000000}" \
    --gate "${PROMOTION_GATE:-confidence}" --promote-at 0.51 --max-games 4000 --extend-below 0.50 --extend-above 0.52 --extend-games 4000 --futility-games 1000 --prefetch --train-from best --selfplay-from latest --match-simulations 200 --train-ema 0.9997 --min-games 1000 --anchor runs/g1/nets/round_013.npz --anchor-games 2000 --guard-live 0.01 \
    --distribute-eval \
    --simulations "${SELFPLAY_SIMULATIONS:-400}" --full-rate 0.25 --fast-simulations 24 \
    --reference AB:2,F \
    --extra-data "${human#,}" \
    --gauntlet $sp1/nets/round_016.npz,$sp1/nets/round_008.npz \
    --live-data data/human/holdout/value "${starts[@]}" "${humanseat[@]}" --human-start-rate "${HUMAN_START_RATE:-0.35}" --before-round "scripts/vm/gcp_start_spot.sh; scripts/vm/human_dataset.sh" \
    --workers "${COLONIST_LOCAL_WORKERS:-8}" --train-threads "${COLONIST_TRAIN_THREADS:-8}"
