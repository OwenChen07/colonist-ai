import json
import sys

g = json.load(open(sys.argv[1]))
n = int(sys.argv[2]) if len(sys.argv) > 2 else 15
print("me", g["me"], "game", json.dumps(g["game"])[:500])
fs = g["final_state"]
print("winner", fs.get("winner"), "turn", fs.get("turnNumber"))
for p in fs["players"]:
    print({k: v for k, v in p.items() if k != "hand"})
for e in sorted(g["events"], key=lambda e: e["seq"])[-n:]:
    print(json.dumps(e)[:300])
for row in g["notes"][-n:]:
    print("note", json.dumps(row)[:300])
for m in g["moves"][-5:]:
    print("move", json.dumps(m)[:300])
