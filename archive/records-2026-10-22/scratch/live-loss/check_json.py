import json

d = json.load(open("data/orchestrator/experiments.json"))
print("ok", len(d) if isinstance(d, list) else len(d.get("experiments", d)))
