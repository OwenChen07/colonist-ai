import random

from catanatron.models.map import PORT_DIRECTION_TO_NODEREFS, build_map
from catanatron.models.tiles import Port
from catanatron.openhex import board as B

TERRAIN_OF = {v: k for k, v in B.TERRAIN.items()}


def land(catan_map):
    """A Catanatron map's land tiles in OpenHex's form."""
    tiles = []
    for coordinate, tile in catan_map.land_tiles.items():
        t = {"hex": B.xyz(coordinate), "terrain": TERRAIN_OF[tile.resource]}
        if tile.number:
            t["number"] = tile.number
        tiles.append(t)
    return tiles


def ports(catan_map, board):
    """Its ports in OpenHex's form (edges as cube vertex pairs)."""
    out = []
    for tile in catan_map.tiles.values():
        if isinstance(tile, Port):
            a, b = PORT_DIRECTION_TO_NODEREFS[tile.direction]
            p = {"edge": board.road((tile.nodes[a], tile.nodes[b])), "rate": 2}
            if tile.resource:
                p["resource"] = B.RESOURCE_NAME[tile.resource]
            else:
                p["rate"] = 3
            out.append(p)
    return out


def test_tables():
    assert len(B.HEXES) == 19 and len(B.VERTICES) == 54 and len(B.EDGES) == 72
    # the guide's examples: vertex 11 is (0,0,1), 17 is (1,0,0). (Its "edge
    # 15 = (0,0,1)-(0,1,1)" disagrees with its own edge table, which is what
    # EDGES follows; the client sends edges as vertex pairs to be safe.)
    assert B.VERTICES[11] == (0, 0, 1) and B.VERTICES[17] == (1, 0, 0)
    assert B.EDGES[0] == ((-2, 0, 3), (-1, 0, 3))
    assert B.EDGES[15] == ((-1, 1, 1), (0, 1, 1))


def test_geometry_matches_catanatron():
    m = build_map("BASE")
    board = B.Board(land(m), [])
    assert sorted(board.node_of_vertex.values()) == list(range(54))
    catan_edges = {
        frozenset(e) for t in m.land_tiles.values() for e in t.edges.values()
    }
    assert set(board.edge_of_nodes) == catan_edges
    # every hex's six vertices are that tile's six nodes
    for h in B.HEXES:
        touching = {board.node(v) for v in B.VERTICES if h in B.vertex_hexes(v)}
        assert touching == set(m.tiles[h].nodes.values())


def test_round_trip_of_random_boards():
    for seed in range(5):
        random.seed(seed)
        m = build_map("BASE", number_placement="random")
        built = B.Board(land(m), ports(m, B.Board(land(m), []))).catan_map
        for coordinate, tile in m.land_tiles.items():
            other = built.land_tiles[coordinate]
            assert (other.id, other.resource, other.number) == (
                tile.id,
                tile.resource,
                tile.number,
            )
        assert {k: set(v) for k, v in built.port_nodes.items()} == {
            k: set(v) for k, v in m.port_nodes.items()
        }


def test_nonstandard_port_and_index_forms():
    tiles = land(build_map("BASE"))
    # the guide's example port, as vertex indices
    a, b = B.VERTICES.index((3, -2, 0)), B.VERTICES.index((3, -2, 1))
    board = B.Board(tiles, [{"edge": [a, b], "rate": 3}])
    nodes = {board.node(a), board.node(b)}
    assert set(board.catan_map.port_nodes[None]) == nodes
    edge = B.EDGES.index(((3, -2, 0), (3, -2, 1)))
    assert set(board.edge(edge)) == nodes
    assert board.road(board.edge(edge)) == [B.xyz(v) for v in B.EDGES[edge]]
