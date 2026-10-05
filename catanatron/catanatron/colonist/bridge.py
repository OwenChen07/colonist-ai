"""The bridge between colonist in the browser and the AI (PLAN.md, Phase 4).

    python -m catanatron.colonist.bridge models/ab2/net.npz --simulations 200

Runs a small server on this machine (ws://127.0.0.1:8765) that the bot
extension (bot/) connects to. The extension forwards every message colonist
sends; the bridge follows the game with a LiveGame and, whenever the bot's
account has a decision to make, runs the network and search and sends back:

  - the move, in colonist's terms (corner/edge/tile indexes, resource ids),
    for the extension to show and, once clicking is built, to play;
  - the top few moves with their share of the search, and the AI's estimate
    of its chance to win, for the overlay.

If the position ever stops matching colonist's (see live.py), the bridge
reports it and suggests nothing more for that game.

Messages are JSON. From the extension:
  {"kind": "frame", "dir": "in"|"out", "b64": "<MessagePack bytes>", "t": ms}
  {"kind": "reset"}                       (a new page: forget the game)
To the extension:
  {"kind": "status", "state": "waiting"|"following"|"thinking"|"stopped", "text": ...}
  {"kind": "move", "position": n, "move": {...}, "plan": [...], "options": [...],
   "win_chance": p, "ms": t}
"""

import argparse
import asyncio
import json
import random
import sys
import time

import numpy as np

from catanatron.colonist import convert as C
from catanatron.colonist import encode as E
from catanatron.colonist.capture import decode
from catanatron.colonist.live import LiveGame
from catanatron.models.enums import ActionType

HOST, PORT = "127.0.0.1", 8765


class Bridge:
    """The bridge's logic, without the network: feed it messages, get back
    the replies to send."""

    def __init__(self, net, simulations=200, seed=0):
        self.net = net
        self.simulations = simulations
        self.rng = random.Random(seed)
        self.live = None
        self.answered = None  # position key of the last move sent
        self.seq = 0

    def reset(self):
        self.live, self.answered = None, None
        return [self.status("waiting", "waiting for a game to start")]

    @staticmethod
    def status(state, text):
        return {"kind": "status", "state": state, "text": text}

    def handle(self, message):
        """One message from the extension -> list of replies."""
        kind = message.get("kind")
        if kind == "reset":
            return self.reset()
        if kind != "frame":
            return []
        self.seq += 1
        frame = decode(
            {**message, "seq": self.seq, "socket": 0, "t": message.get("t", 0)}
        )
        if frame.dir != "in":
            return []
        if frame.type == C.GAME_STATE:
            self.live = LiveGame(frame.data["data"]["payload"], frame.t)
            self.answered = None
            if self.live.error:
                return [self.status("stopped", self.live.error)]
            who = self.live.bot.value if self.live.bot else "a spectator"
            replies = [self.status("following", f"game started; playing as {who}")]
            return replies + self.think()
        if self.live is None or not self.live.apply(frame):
            return []
        if self.live.error:
            return [
                self.status(
                    "stopped", f"out of sync, not playing on: {self.live.error}"
                )
            ]
        if self.live.over:
            return [self.status("waiting", "game over")]
        return self.think()

    def think(self):
        live = self.live
        if not live.bot_to_move or live.position_key == self.answered:
            return []
        self.answered = live.position_key
        start = time.time()
        game = live.game
        move, options, value = self.choose(game)
        plan = [live.describe(move)]
        if move.action_type == ActionType.DISCARD_RESOURCE:
            # colonist takes all discards at once; the simulator one at a time.
            plan = [live.describe(a) for a in self.plan_discards(game)]
        return [
            {
                "kind": "move",
                "position": live.position_key,
                "move": plan[0],
                "plan": plan,
                "options": [
                    {"move": live.describe(a), "share": round(s, 3)} for a, s in options
                ],
                "win_chance": round((value + 1) / 2, 3),
                "ms": int((time.time() - start) * 1000),
            }
        ]

    def choose(self, game):
        """(move, [(move, share)], value for the player to move)."""
        actions = game.playable_actions
        if len(actions) == 1:
            _, value = self.net.evaluate(
                game, game.state.current_color(), E.legal_indices(actions)
            )
            return actions[0], [(actions[0], 1.0)], value
        if self.simulations == 0:
            priors, value = self.net.evaluate(
                game, game.state.current_color(), E.legal_indices(actions)
            )
            order = np.argsort(-priors)[:3]
            return (
                actions[order[0]],
                [(actions[i], float(priors[i])) for i in order],
                value,
            )
        from catanatron.colonist.mcts import Search

        search = Search(self.net, self.simulations, rng=self.rng, noise=False)
        action, root = search.run(game)
        _, value = search.root_targets(root)
        # Show moves by the share of simulations they got: the search's
        # finalists, the move it plays among them.
        share = root.N / max(root.N.sum(), 1)
        key = E.action_key(action)
        chosen = next(i for i, a in enumerate(root.actions) if E.action_key(a) == key)
        order = [chosen] + [i for i in np.argsort(-share) if i != chosen][:2]
        move = next(a for a in actions if E.action_key(a) == key)
        return move, [(root.actions[i], float(share[i])) for i in order], value

    def plan_discards(self, game):
        """All of the bot's discards for this 7, chosen one by one on a copy."""
        g = game.copy()
        color = g.state.current_color()
        plan = []
        while (
            g.state.is_discarding
            and g.state.current_color() == color
            and g.winning_color() is None
        ):
            move, _, _ = self.choose(g)
            plan.append(move)
            g.execute(move, validate_action=False)
        return plan


async def serve_forever(make_bridge, host=HOST, port=PORT):
    """Serve until stopped. Each connection (one per colonist tab) gets its own
    Bridge from make_bridge(), so two tabs never mix their games."""
    from websockets.asyncio.server import serve

    async def handler(socket):
        bridge = make_bridge()
        print(f"extension connected from {socket.remote_address}", flush=True)
        for reply in bridge.reset():
            await socket.send(json.dumps(reply))
        async for raw in socket:
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            # Thinking can take a second or two: keep the connection responsive.
            replies = await asyncio.to_thread(bridge.handle, message)
            for reply in replies:
                if reply["kind"] == "move":
                    print(
                        f"  move: {reply['move']['label']} "
                        f"(win chance {reply['win_chance']:.0%}, {reply['ms']} ms)",
                        flush=True,
                    )
                elif reply["kind"] == "status":
                    print(f"  {reply['state']}: {reply['text']}", flush=True)
                await socket.send(json.dumps(reply))
        print("extension disconnected", flush=True)

    async with serve(handler, host, port, max_size=2**24):
        print(f"bridge listening on ws://{host}:{port}", flush=True)
        await asyncio.Future()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("net", help="net.npz")
    parser.add_argument("--simulations", type=int, default=200)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args(argv)
    from catanatron.colonist.net import Net

    net = Net.load(args.net)
    try:
        asyncio.run(
            serve_forever(lambda: Bridge(net, args.simulations), port=args.port)
        )
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
