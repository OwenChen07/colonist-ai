import json

d = json.load(open("data/orchestrator/experiments.json"))
print("OK", len(d) if not isinstance(d, dict) else len(d.get("experiments", d)))
