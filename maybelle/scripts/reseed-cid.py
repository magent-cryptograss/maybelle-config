#!/usr/bin/env python3
"""Re-seed a CID that's pinned but missing from the seeding directory.

Calls delivery-kid's /enrich/torrent endpoint, which fetches the content
from local IPFS, generates a deterministic torrent, and adds it to the
BitTorrent seeder.

Use after ``audit-storage.py`` reports "MISSING SEEDS".

Usage:
  DELIVERY_KID_API_KEY=... maybelle/scripts/reseed-cid.py <cid> [<cid> ...]
"""

import argparse
import json
import os
import sys
import urllib.request


DELIVERY_KID_URL = "https://delivery-kid.cryptograss.live"


def reseed(cid: str, api_key: str) -> bool:
    url = f"{DELIVERY_KID_URL}/enrich/torrent"
    payload = json.dumps({"cid": cid}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  {cid[:16]}... ERROR: {e}")
        return False

    if result.get("success"):
        print(f"  {cid[:16]}... ok (infohash {result.get('infohash', '?')[:16]}..., "
              f"{result.get('file_count', '?')} files, "
              f"{result.get('total_size', 0) // 1024} KB)")
        return True
    print(f"  {cid[:16]}... FAILED: {result.get('error')}")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cids", nargs="+", help="CID(s) to re-seed")
    args = parser.parse_args()

    api_key = os.environ.get("DELIVERY_KID_API_KEY")
    if not api_key:
        print("ERROR: DELIVERY_KID_API_KEY environment variable not set", file=sys.stderr)
        return 2

    print(f"Re-seeding {len(args.cids)} CID(s) via {DELIVERY_KID_URL}...")
    failures = 0
    for cid in args.cids:
        if not reseed(cid, api_key):
            failures += 1

    if failures:
        print(f"\n{failures} of {len(args.cids)} failed.")
        return 1
    print("\nAll done. Run audit-storage.py to verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
