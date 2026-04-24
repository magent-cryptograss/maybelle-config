#!/usr/bin/env python3
"""Re-seed a CID that's pinned but missing from the seeding directory.

Calls delivery-kid's /enrich/torrent endpoint, which fetches the content
from local IPFS, generates a deterministic torrent, and adds it to the
BitTorrent seeder.

Use after ``audit-storage.py`` reports "MISSING SEEDS".

Reads the delivery-kid API key from the ansible vault at
``secrets/vault.yml`` — requires ``ANSIBLE_VAULT_PASSWORD_FILE`` (or
``ANSIBLE_VAULT_PASSWORD``) set, same as the deploy scripts. You can
also override with ``DELIVERY_KID_API_KEY`` directly.

Usage:
  maybelle/scripts/reseed-cid.py <cid> [<cid> ...]
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import yaml


DELIVERY_KID_URL = "https://delivery-kid.cryptograss.live"
VAULT_PATH = Path(__file__).resolve().parents[2] / "secrets" / "vault.yml"


def load_api_key() -> str:
    """Return the delivery-kid API key.

    Order of precedence:
      1. ``DELIVERY_KID_API_KEY`` env var (explicit override).
      2. ansible-vault view of ``secrets/vault.yml`` using
         ``ANSIBLE_VAULT_PASSWORD_FILE`` or ``ANSIBLE_VAULT_PASSWORD``.
    """
    explicit = os.environ.get("DELIVERY_KID_API_KEY")
    if explicit:
        return explicit

    cmd = ["ansible-vault", "view", str(VAULT_PATH)]
    if os.environ.get("ANSIBLE_VAULT_PASSWORD_FILE"):
        cmd += ["--vault-password-file", os.environ["ANSIBLE_VAULT_PASSWORD_FILE"]]
    elif not os.environ.get("ANSIBLE_VAULT_PASSWORD"):
        raise RuntimeError(
            "Neither DELIVERY_KID_API_KEY nor ANSIBLE_VAULT_PASSWORD(_FILE) is set"
        )

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ansible-vault view failed: {result.stderr.strip()}")
    data = yaml.safe_load(result.stdout) or {}
    key = data.get("delivery_kid_api_key")
    if not key:
        raise RuntimeError(f"delivery_kid_api_key missing from {VAULT_PATH}")
    return key


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

    try:
        api_key = load_api_key()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("  Set ANSIBLE_VAULT_PASSWORD_FILE (same as deploy scripts) "
              "or DELIVERY_KID_API_KEY.", file=sys.stderr)
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
