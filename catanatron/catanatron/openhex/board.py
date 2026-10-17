"""OpenHex's board in Catanatron's terms.

OpenHex numbers hexes 0-18, vertices 0-53 and edges 0-71 (its guide's
#/coordinates page, copied below). Hexes use cube coordinates like
Catanatron's, so a hex keeps its coordinate. A vertex is a cube point whose
coordinates sum to 1 or 2, touching three hexes; it is the one Catanatron node
those three tiles (land or water) share. An edge is a pair of vertices.

Ports can sit on any coastal edge, so the map is built tile by tile in the
BASE template's order (the same node and edge ids as every other board), with
a Port on the water tile across each OpenHex port edge.
"""

from catanatron.models.enums import BRICK, ORE, SHEEP, WHEAT, WOOD
from catanatron.models.map import (
    BASE_MAP_TEMPLATE,
    PORT_DIRECTION_TO_NODEREFS,
    CatanMap,
    get_nodes_and_edges,
)
from catanatron.models.tiles import LandTile, Port, Water

HEXES = [
    (0, 0, 0),
    (1, -1, 0),
    (1, 0, -1),
    (0, 1, -1),
    (-1, 1, 0),
    (-1, 0, 1),
    (0, -1, 1),
    (2, -2, 0),
    (2, -1, -1),
    (2, 0, -2),
    (1, 1, -2),
    (0, 2, -2),
    (-1, 2, -1),
    (-2, 2, 0),
    (-2, 1, 1),
    (-2, 0, 2),
    (-1, -1, 2),
    (0, -2, 2),
    (1, -2, 1),
]

VERTICES = [
    # sum 1
    (-2, 0, 3),
    (-2, 1, 2),
    (-2, 2, 1),
    (-2, 3, 0),
    (-1, -1, 3),
    (-1, 0, 2),
    (-1, 1, 1),
    (-1, 2, 0),
    (-1, 3, -1),
    (0, -2, 3),
    (0, -1, 2),
    (0, 0, 1),
    (0, 1, 0),
    (0, 2, -1),
    (0, 3, -2),
    (1, -2, 2),
    (1, -1, 1),
    (1, 0, 0),
    (1, 1, -1),
    (1, 2, -2),
    (2, -2, 1),
    (2, -1, 0),
    (2, 0, -1),
    (2, 1, -2),
    (3, -2, 0),
    (3, -1, -1),
    (3, 0, -2),
    # sum 2
    (-2, 1, 3),
    (-2, 2, 2),
    (-2, 3, 1),
    (-1, 0, 3),
    (-1, 1, 2),
    (-1, 2, 1),
    (-1, 3, 0),
    (0, -1, 3),
    (0, 0, 2),
    (0, 1, 1),
    (0, 2, 0),
    (0, 3, -1),
    (1, -2, 3),
    (1, -1, 2),
    (1, 0, 1),
    (1, 1, 0),
    (1, 2, -1),
    (1, 3, -2),
    (2, -2, 2),
    (2, -1, 1),
    (2, 0, 0),
    (2, 1, -1),
    (2, 2, -2),
    (3, -2, 1),
    (3, -1, 0),
    (3, 0, -1),
    (3, 1, -2),
]


def _edges():
    """The guide's edge order: each sum-1 vertex in order, joined to its
    +x, +y, +z neighbours that exist."""
    vertex_set = set(VERTICES)
    edges = []
    for x, y, z in VERTICES:
        if x + y + z != 1:
            continue
        for nb in ((x + 1, y, z), (x, y + 1, z), (x, y, z + 1)):
            if nb in vertex_set:
                edges.append(((x, y, z), nb))
    return edges


EDGES = _edges()

TERRAIN = {
    "forest": WOOD,
    "hills": BRICK,
    "pasture": SHEEP,
    "fields": WHEAT,
    "mountains": ORE,
    "desert": None,
}
RESOURCE = {"wood": WOOD, "brick": BRICK, "sheep": SHEEP, "wheat": WHEAT, "ore": ORE}
RESOURCE_NAME = {v: k for k, v in RESOURCE.items()}  # Catanatron -> OpenHex


def vertex_hexes(v):
    """The three hexes (on the board or in the sea) a vertex touches."""
    x, y, z = v
    if x + y + z == 1:
        return [(x, y - 1, z), (x - 1, y, z), (x, y, z - 1)]
    return [(x - 1, y, z - 1), (x, y - 1, z - 1), (x - 1, y - 1, z)]


def cube(p):
    """A coordinate as OpenHex sends it ({x,y,z} or [x,y,z]) -> tuple."""
    if isinstance(p, dict):
        return (p["x"], p["y"], p["z"])
    return tuple(p)


def as_hex(p):
    return HEXES[p] if isinstance(p, int) else cube(p)


def as_vertex(p):
    return VERTICES[p] if isinstance(p, int) else cube(p)


def as_edge(p):
    if isinstance(p, int):
        return EDGES[p]
    a, b = p
    return (as_vertex(a), as_vertex(b))


def xyz(p):
    return {"x": p[0], "y": p[1], "z": p[2]}


def _shared_node(tiles, coords):
    common = None
    for c in coords:
        nodes = set(tiles[c].nodes.values())
        common = nodes if common is None else common & nodes
    if not common or len(common) != 1:
        raise ValueError(f"no single node shared by tiles {coords}")
    return next(iter(common))


class Board:
    """One game's board: Catanatron's map, and the index translations."""

    def __init__(self, tiles, ports):
        """`tiles`, `ports`: OpenHex's state.board.tiles and .ports."""
        by_hex = {as_hex(t["hex"]): t for t in tiles}
        if set(by_hex) != set(HEXES):
            raise ValueError("OpenHex board doesn't have the 19 standard hexes")
        # First pass: the BASE topology with water everywhere off the land,
        # only to learn the node ids (they don't depend on tile types).
        geometry, autoinc = {}, 0
        for coordinate in BASE_MAP_TEMPLATE.topology:
            nodes, edges, autoinc = get_nodes_and_edges(geometry, coordinate, autoinc)
            geometry[coordinate] = Water(nodes, edges)
        self.node_of_vertex = {
            v: _shared_node(geometry, vertex_hexes(v)) for v in VERTICES
        }
        self.vertex_of_node = {n: v for v, n in self.node_of_vertex.items()}
        self.edge_of_nodes = {
            frozenset((self.node_of_vertex[a], self.node_of_vertex[b])): (a, b)
            for a, b in EDGES
        }
        # Each port: the sea tile across its edge, and the direction that
        # puts the port on that edge.
        port_at = {}
        for p in ports:
            a, b = as_edge(p["edge"])
            pair = {self.node_of_vertex[a], self.node_of_vertex[b]}
            resource = RESOURCE[p["resource"]] if p.get("resource") else None
            found = [
                (coordinate, direction)
                for coordinate, tile in geometry.items()
                if coordinate not in by_hex
                for direction, refs in PORT_DIRECTION_TO_NODEREFS.items()
                if {tile.nodes[r] for r in refs} == pair
            ]
            if len(found) != 1:
                raise ValueError(f"no single sea tile for port {p}")
            coordinate, direction = found[0]
            port_at[coordinate] = (resource, direction)
        if len(port_at) != len(ports):
            raise ValueError("two OpenHex ports on one sea tile")
        # Second pass: the real tiles, in the same order (same ids).
        built, autoinc, land_id, port_id = {}, 0, 0, 0
        for coordinate in BASE_MAP_TEMPLATE.topology:
            nodes, edges, autoinc = get_nodes_and_edges(built, coordinate, autoinc)
            if coordinate in by_hex:
                t = by_hex[coordinate]
                resource = TERRAIN[t["terrain"]]
                number = t.get("number") if resource else None
                built[coordinate] = LandTile(land_id, resource, number, nodes, edges)
                land_id += 1
            elif coordinate in port_at:
                resource, direction = port_at[coordinate]
                built[coordinate] = Port(port_id, resource, direction, nodes, edges)
                port_id += 1
            else:
                built[coordinate] = Water(nodes, edges)
        self.catan_map = CatanMap.from_tiles(built)

    # ----- OpenHex -> Catanatron -----
    def node(self, p):
        return self.node_of_vertex[as_vertex(p)]

    def edge(self, p):
        a, b = as_edge(p)
        return (self.node_of_vertex[a], self.node_of_vertex[b])

    # ----- Catanatron -> OpenHex (cube coordinates; actions accept them) -----
    def vertex(self, node):
        return xyz(self.vertex_of_node[node])

    def road(self, nodes):
        a, b = self.edge_of_nodes[frozenset(nodes)]
        return [xyz(a), xyz(b)]
