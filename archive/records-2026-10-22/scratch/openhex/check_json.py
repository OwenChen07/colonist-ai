import json

with open("data/orchestrator/experiments.json") as f:
    data = json.load(f)
print("ok", len(data) if isinstance(data, list) else list(data)[:3])
