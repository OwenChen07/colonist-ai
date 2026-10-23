"""Probe t2d-standard-2 on-demand capacity in other us-central1 zones (lead's OK, Oct 22, 8:55 PM).

Creates a throwaway VM per zone, records whether it reached RUNNING, deletes it at once.
"""

import subprocess
import sys

ZONES = sys.argv[1:] or ["us-central1-b", "us-central1-c", "us-central1-f"]
TYPE = "t2d-standard-2"


def run(*args):
    p = subprocess.run(["gcloud", "compute", *args], capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


for z in ZONES:
    name = "t2d-probe-" + z.rsplit("-", 1)[1]
    rc, out = run(
        "instances",
        "create",
        name,
        "--zone",
        z,
        "--machine-type",
        TYPE,
        "--image-family",
        "ubuntu-2404-lts-amd64",
        "--image-project",
        "ubuntu-os-cloud",
        "--boot-disk-size",
        "10GB",
        "--no-address",
        "--quiet",
    )
    ok = rc == 0
    last = out.splitlines()[-1] if out else ""
    if ok:
        _, st = run(
            "instances",
            "describe",
            name,
            "--zone",
            z,
            "--format=value(status,cpuPlatform)",
        )
        print(f"{z}: CAPACITY OK ({st})", flush=True)
    else:
        print(f"{z}: no ({last[:200]})", flush=True)
    rc2, out2 = run("instances", "delete", name, "--zone", z, "--quiet")
    if ok or rc2 == 0:
        print(
            f"  {name} delete rc {rc2}: {out2.splitlines()[-1][:120] if out2 else ''}",
            flush=True,
        )
