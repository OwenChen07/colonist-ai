"""Print a host's ssh keys with SHA256 fingerprints, to check them against the serial console.

hostkey.py IP [EXPECTED_SHA256 ...]
"""

import base64
import hashlib
import subprocess
import sys

ip, expected = sys.argv[1], set(sys.argv[2:])
out = subprocess.run(
    ["ssh-keyscan", "-t", "ed25519,ecdsa,rsa", ip], capture_output=True, text=True
).stdout
for line in out.splitlines():
    if line.startswith("#") or not line.strip():
        continue
    host, kt, b64 = line.split()[:3]
    fp = (
        base64.b64encode(hashlib.sha256(base64.b64decode(b64)).digest())
        .decode()
        .rstrip("=")
    )
    mark = "MATCH" if fp in expected else "-"
    print(f"{mark} SHA256:{fp} {kt} {b64}")
