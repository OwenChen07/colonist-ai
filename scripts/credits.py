"""The cloud credits left on each platform, for the spectator site.

Run on the owner's Mac, where the cloud CLIs are signed in:

    python scripts/credits.py                 # Azure and AWS from their APIs
    python scripts/credits.py --gcp 251.40    # GCP's figure, from its console

GCP has no API for free-trial credits, so its figure is whatever was last
given with --gcp (kept with its date). The result goes to data/credits.json,
and --push copies it to the coordinator, whose sync_bot.sh passes it to the
bot VMs' publishers.
"""

import argparse
import datetime as dt
import json
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "credits.json"
COORDINATOR = "owen@34.60.203.129"
SSH_KEY = "~/.ssh/google_compute_engine"
# Free credits each platform granted (USD).
TOTALS = {"GCP": 300.0, "Azure": 100.0, "AWS": 200.0}


def _run(*command):
    out = subprocess.run(command, capture_output=True, text=True, timeout=90)
    if out.returncode:
        raise RuntimeError((out.stderr or out.stdout).strip().splitlines()[-1][:200])
    return json.loads(out.stdout)


def azure():
    """Azure for Students: the credit's balance summary on the billing profile."""
    accounts = _run(
        "az",
        "rest",
        "--method",
        "get",
        "--url",
        "https://management.azure.com/providers/Microsoft.Billing/billingAccounts"
        "?api-version=2024-04-01",
    )["value"]
    account = accounts[0]["name"]
    base = f"https://management.azure.com/providers/Microsoft.Billing/billingAccounts/{account}"
    profile = _run(
        "az",
        "rest",
        "--method",
        "get",
        "--url",
        f"{base}/billingProfiles?api-version=2024-04-01",
    )["value"][0]["name"]
    summary = _run(
        "az",
        "rest",
        "--method",
        "get",
        "--url",
        f"{base}/billingProfiles/{profile}/providers/Microsoft.Consumption/credits/"
        "balanceSummary?api-version=2023-05-01",
    )["properties"]["balanceSummary"]
    return round(float(summary["estimatedBalance"]["value"]), 2)


def aws():
    """AWS free plan: the account plan's remaining credits."""
    state = _run(
        "aws",
        "freetier",
        "get-account-plan-state",
        "--profile",
        "colonist",
        "--region",
        "us-east-1",
        "--output",
        "json",
    )
    return round(float(state["accountPlanRemainingCredits"]["amount"]), 2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gcp", type=float, help="GCP credits left (its console)")
    parser.add_argument("--push", action="store_true", help="copy to the coordinator")
    args = parser.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    try:
        previous = {c["provider"]: c for c in json.loads(OUT.read_text())["credits"]}
    except (OSError, ValueError, KeyError):
        previous = {}
    rows = []
    for provider, read in (("GCP", None), ("Azure", azure), ("AWS", aws)):
        row = previous.get(
            provider, {"provider": provider, "remaining": None, "asOf": None}
        )
        row = {**row, "total": TOTALS[provider]}
        if provider == "GCP" and args.gcp is not None:
            row.update(remaining=round(args.gcp, 2), asOf=now)
        elif read is not None:
            try:
                row.update(remaining=read(), asOf=now)
            except Exception as error:  # keep the last figure; say why
                print(f"{provider}: kept the last figure ({error})")
        row["source"] = "console" if provider == "GCP" else "api"
        rows.append(row)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"updatedAt": now, "credits": rows}, indent=2))
    for row in rows:
        left = "unknown" if row["remaining"] is None else f"${row['remaining']:.2f}"
        print(f"{row['provider']}: {left} of ${row['total']:.0f} (as of {row['asOf']})")
    if args.push:
        subprocess.run(
            [
                "scp",
                "-q",
                "-i",
                str(Path(SSH_KEY).expanduser()),
                str(OUT),
                f"{COORDINATOR}:colonist-ai/data/credits.json",
            ],
            check=True,
        )
        print("pushed to the coordinator")


if __name__ == "__main__":
    main()
