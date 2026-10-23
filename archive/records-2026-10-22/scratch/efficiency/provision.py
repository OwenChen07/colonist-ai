"""Create spot self-play VMs, wait for the startup script, sync code, drop the external IP.

.venv/bin/python data/orchestrator/scratch/efficiency/provision.py t2d-standard-4 name:zone [name:zone ...]
"""

import subprocess
import sys
import time

key = "/home/owen/.ssh/colonist_worker_ed25519"
here = "data/orchestrator/scratch/efficiency"
machine = sys.argv[1]
vms = [a.split(":") for a in sys.argv[2:]]
image = (
    "ubuntu-2404-lts-arm64" if machine.startswith("t2a") else "ubuntu-2404-lts-amd64"
)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    print(
        " ".join(cmd[:5]),
        "->",
        r.returncode,
        r.stdout.strip()[-300:],
        r.stderr.strip()[-300:],
        flush=True,
    )
    return r


for name, zone in vms:
    run(
        [
            "gcloud",
            "compute",
            "instances",
            "create",
            name,
            "--zone",
            zone,
            "--machine-type",
            machine,
            "--provisioning-model",
            "SPOT",
            "--instance-termination-action",
            "STOP",
            "--image-family",
            image,
            "--image-project",
            "ubuntu-os-cloud",
            "--boot-disk-size",
            "20GB",
            "--metadata-from-file",
            "startup-script=scripts/vm/gcp_worker_startup.sh,ssh-keys=data/orchestrator/jobs/gcp-ssh-keys.txt",
        ]
    )

ips = {}
for name, zone in vms:
    r = run(
        [
            "gcloud",
            "compute",
            "instances",
            "describe",
            name,
            "--zone",
            zone,
            "--format=value(networkInterfaces[0].networkIP)",
        ]
    )
    ips[name] = r.stdout.strip()

deadline = time.time() + 600
pending = dict(ips)
while pending and time.time() < deadline:
    for name, ip in list(pending.items()):
        r = subprocess.run(
            [
                "ssh",
                "-i",
                key,
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=accept-new",
                f"owen@{ip}",
                "test -x colonist-ai/.venv/bin/python && colonist-ai/.venv/bin/python -c 'import numpy'",
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            print(name, ip, "venv ready", flush=True)
            del pending[name]
    time.sleep(15)

for name, zone in vms:
    if name in pending:
        print(name, "NOT READY", flush=True)
        continue
    run([".venv/bin/python", f"{here}/sync_vm.py", f"owen@{ips[name]}"])
    run(
        [
            "gcloud",
            "compute",
            "instances",
            "delete-access-config",
            name,
            "--zone",
            zone,
            "--access-config-name",
            "external-nat",
        ]
    )
print("IPS", ips)
