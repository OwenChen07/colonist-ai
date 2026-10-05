# Colonist AI Bot

A Chrome extension that connects colonist.io games to the colonist-ai bridge running on
the same computer (PLAN.md, Phase 4).

**Advisor mode for now:** a panel on the page shows the move the AI would play, its
top alternatives and its estimated chance to win. It does not click anything yet; that
comes next (see "Next" below).

```
colonist.io tab                         this computer
  page.js   copies game messages   ──>  python -m catanatron.colonist.bridge
  relay.js  shows the AI's answer  <──    follows the game (live.py) and runs
  background.js  link to the bridge       the network + search for the bot's moves
```

## Run it

1. Start the bridge with a trained net:
   ```bash
   .venv/bin/python -m catanatron.colonist.bridge models/ab2/net.npz --simulations 200
   ```
2. In Chrome, open `chrome://extensions`, turn on **Developer mode**, click **Load
   unpacked** and pick this `bot/` folder.
3. Open colonist.io and start a game. The extension has to be on from the start of the
   game: the first message carries the whole board.

The panel in the top-right corner says whether the bridge is connected and what it is
doing. The bridge's terminal prints each move it suggests.

## What it does on its own

- **Follows the game:** every update colonist sends goes through the same converter that
  replays recorded games, and the simulator is checked against colonist's card counts and
  points after each one. If they ever disagree, or colonist does something the converter
  doesn't know, the bridge says "stopped" and suggests nothing more for that game. The
  bot must never play on a position it isn't sure of.
- **Answers only for its own seat:** it suggests a move only when the logged-in account
  has a decision to make, once per position.
- **Discards:** colonist takes all of a 7's discards at once, so the bridge returns the
  whole set.

## Rules for playing (see PLAN.md, Phase 4)

colonist has allowed this bot (confirmed with their team). The bot plays from a
clearly labelled bot account and says in the game chat that it is a bot.

## Next

- The actor: turning the bridge's move (corner, edge and tile indexes, resource ids, all
  in colonist's own numbering) into clicks on colonist's buttons and board.
- Declining trade offers, and posting the bot notice in chat.
- Running it unattended in a headless browser on a cloud VM.

## Test

```bash
cd bot && npm test                                   # page.js
.venv/bin/python -m pytest tests/colonist/test_bridge.py   # live sync and the bridge
```
