"""rehex's robber rule: tile score = opponent's pips - own pips (cities x2)."""


def pips(n):
    return 0 if n is None else 6 - abs(7 - n)


def tile_scores(game, me):
    board = game.state.board
    out = {}
    for coord, tile in board.map.land_tiles.items():
        mine = theirs = 0
        for nid in tile.nodes.values():
            b = board.buildings.get(nid)
            if b is None:
                continue
            w = pips(tile.number) * (2 if b[1] == "CITY" else 1)
            if b[0] == me:
                mine += w
            else:
                theirs += w
        out[coord] = (theirs - mine, mine > 0)
    return out
