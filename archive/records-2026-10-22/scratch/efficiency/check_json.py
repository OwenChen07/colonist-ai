import json

d = json.load(open("data/orchestrator/experiments.json"))
print("ok", [e["id"] for e in d["experiments"]][-3:])
