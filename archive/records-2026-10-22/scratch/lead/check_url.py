"""Print the HTTP status and first bytes of a URL."""

import sys
import urllib.request

request = urllib.request.Request(
    sys.argv[1], headers={"User-Agent": "colonist-ai-check/1.0"}
)
with urllib.request.urlopen(request, timeout=30) as response:
    print(response.status, response.read(200))
