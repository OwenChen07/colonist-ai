import json

d = json.load(open("/home/owen/colonist-ai/data/orchestrator/experiments.json"))
items = d if isinstance(d, list) else d.get("experiments", d)
for e in items:
    if e.get("id") in ("E38", "E39"):
        print(e["id"], e["status"], e["ended"])
print("ok", len(items))
