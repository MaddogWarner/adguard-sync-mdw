from __future__ import annotations

import ssl
import sys
import urllib.request

PORT = 8080
PATH = "/healthz"
TIMEOUT = 3


def _probe(url: str, context: ssl.SSLContext | None) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT, context=context) as response:
            return response.status == 200
    except Exception:
        return False


def main() -> int:
    # The dashboard serves HTTPS by default (often a self-signed cert), but may be
    # plain HTTP when TLS is disabled. Accept either, ignoring cert trust locally.
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE

    if _probe(f"https://127.0.0.1:{PORT}{PATH}", insecure):
        return 0
    if _probe(f"http://127.0.0.1:{PORT}{PATH}", None):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
