# Colonist AI Capture

A Chrome extension that saves every game message colonist.io sends and receives, so your
games can be turned into records for the Phase 0 replay check (see `../PLAN.md`).

It is read-only: it copies messages as they pass and never sends or changes anything.
Captures include player names and chat, so they are kept out of git (`captures/` is ignored).

## Install

1. Open `chrome://extensions` and turn on **Developer mode** (top right).
2. Click **Load unpacked** and pick this `recorder/` folder.
3. Reload any open colonist.io tabs.

## Capture a game

1. Open colonist.io and play a game as usual. A small **● Save capture (N)** button sits in the
   bottom-left corner, and N counts messages as they arrive.
2. When the game ends, click **Save capture**. The file lands in your Downloads folder as
   `colonist-capture-YYYYMMDD-HHMM.json`.
3. Move it into `captures/` in this repo.

Start the game with the extension already on: the full board comes in the first messages,
so a capture started mid-game is missing it. If you forget to click Save, the extension keeps
the last 10 page loads; on a new colonist tab, Save downloads the most recent one with a game in it.

## Look at a capture

```bash
.venv/bin/python -m catanatron.colonist.capture captures/colonist-capture-....json --dump /tmp/frames.jsonl
```

## Test

```bash
cd recorder && npm install && npm test
```
