import json
import os
import sys

sys.path.insert(0, "data/orchestrator/scratch/live-loss")
from led_late import wilson  # noqa: E402

res = json.load(open("data/orchestrator/scratch/live-loss/led_late.json"))
led = [r for r in res if r["lead"]]
for label, sel in (
    ("blown", [r for r in led if not r["won"]]),
    ("held", [r for r in led if r["won"]]),
):
    n = len(sel)
    L = lambda r: r["lead"]  # noqa: E731
    F = lambda r: r["final"]  # noqa: E731
    print(f"{label} n={n}")
    print(
        "  person VP card at end",
        wilson(sum(F(r)["them"]["vpcards"] > 0 for r in sel), n),
    )
    print(
        "  person VP card at lead",
        wilson(sum(L(r)["them"]["vpcards"] > 0 for r in sel), n),
    )
    for key in ("vp", "cities", "vpcards", "road_len", "knights"):
        print(
            f"  {key}: lead bot {sum(L(r)['me'][key] for r in sel)/n:.2f} person {sum(L(r)['them'][key] for r in sel)/n:.2f};"
            f" end bot {sum(F(r)['me'][key] for r in sel)/n:.2f} person {sum(F(r)['them'][key] for r in sel)/n:.2f}"
        )
    print(
        "  v at lead mean %.2f; bot 13+ VP at lead %s"
        % (
            sum(L(r)["v"] for r in sel) / n,
            wilson(sum(L(r)["me"]["vp"] >= 13 for r in sel), n),
        )
    )
    print(
        "  lead road margin (bot-person road_len) mean %.2f"
        % (sum(L(r)["me"]["road_len"] - L(r)["them"]["road_len"] for r in sel) / n)
    )
    road = [r for r in sel if L(r)["me"]["road"]]
    print(
        "  bot held road at lead: road margin <=1:",
        wilson(
            sum(L(r)["me"]["road_len"] - L(r)["them"]["road_len"] <= 1 for r in road),
            len(road),
        ),
        "| stolen:",
        wilson(sum(F(r)["them"]["road"] for r in road), len(road)),
    )
os.makedirs("data/orchestrator/live-loss", exist_ok=True)
with open("data/orchestrator/live-loss/blown_leads.jsonl", "w") as f:
    for r in led:
        if not r["won"]:
            f.write(
                json.dumps(
                    {
                        "capture": r["path"],
                        "lead_turn": r["lead"]["turn"],
                        "v": round(r["lead"]["v"], 3),
                        "road_stolen": r["lead"]["me"]["road"]
                        and r["final"]["them"]["road"],
                        "lead": r["lead"],
                        "final": r["final"],
                    }
                )
                + "\n"
            )
