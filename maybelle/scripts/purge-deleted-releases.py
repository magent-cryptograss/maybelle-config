#!/usr/bin/env python3
"""Purge infrastructure (IPFS pin + seeding dir) for Release pages marked
with ``delete: true`` or ``unpin: true`` in their YAML.

Per-item interactive confirmation. Prints the matching YAML change needed
on the wiki side (``pinned_on``) after each cleanup — this script
deliberately does not edit wiki pages, to keep it runnable without an
admin bot password.

Run after ``audit-storage.py`` surfaces "CLEANUP PENDING" entries.

Usage:
  maybelle/scripts/purge-deleted-releases.py           # interactive
  maybelle/scripts/purge-deleted-releases.py --dry-run # list without touching
"""

import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request

import yaml


DK_HOST = "root@delivery-kid.cryptograss.live"
WIKI_API = "https://pickipedia.xyz/api.php"
IPFS_EMPTY_DIR = "qmunllspaccz1vlxqvkxqqlx5r1x345qqfhbsf67hva3nn"


def ssh(host: str, cmd: str, check: bool = False) -> tuple[int, str, str]:
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, cmd],
        capture_output=True, text=True, timeout=120,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"ssh {host} failed: {result.stderr.strip()}")
    return result.returncode, result.stdout, result.stderr


def wiki_get(params: dict) -> dict:
    params = {**params, "format": "json"}
    url = f"{WIKI_API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_releaselist() -> list[dict]:
    with urllib.request.urlopen(f"{WIKI_API}?action=releaselist&format=json", timeout=30) as resp:
        return json.loads(resp.read().decode()).get("releases", [])


def page_content(title: str) -> str:
    data = wiki_get({"action": "query", "titles": title,
                     "prop": "revisions", "rvprop": "content", "rvslots": "main"})
    for p in data.get("query", {}).get("pages", {}).values():
        for r in p.get("revisions", []):
            return r.get("slots", {}).get("main", {}).get("*", "") or ""
    return ""


def fetch_pins() -> set[str]:
    _, out, _ = ssh(DK_HOST, "docker exec ipfs ipfs pin ls --type=recursive -q 2>/dev/null")
    return {line.strip().lower() for line in out.splitlines() if line.strip()}


def fetch_seeding_dirs() -> list[str]:
    _, out, _ = ssh(DK_HOST, "ls /mnt/storage-box/staging/seeding/ 2>/dev/null")
    return [line.strip() for line in out.splitlines() if line.strip()]


def unpin_ipfs(cid: str) -> bool:
    """Unpin CID from the delivery-kid IPFS node. Return True on success."""
    rc, _, stderr = ssh(DK_HOST, f"docker exec ipfs ipfs pin rm {cid} 2>&1")
    if rc == 0:
        return True
    # "not pinned" is fine — already clean
    if "not pinned" in stderr.lower() or "not pinned" in _:
        return True
    print(f"    ipfs pin rm failed: {stderr.strip() or _.strip()}")
    return False


def remove_seeding_dir(cid: str) -> bool:
    """Remove the seeding directory for a CID. Case-insensitive match."""
    rc, out, _ = ssh(DK_HOST,
        f"ls /mnt/storage-box/staging/seeding/ 2>/dev/null | "
        f"grep -i '^{cid}$' | head -1")
    actual = out.strip()
    if not actual:
        return True  # already gone
    rc, _, stderr = ssh(DK_HOST,
        f"rm -rf /mnt/storage-box/staging/seeding/{actual}")
    if rc != 0:
        print(f"    rm failed: {stderr.strip()}")
        return False
    return True


def confirm(prompt: str) -> bool:
    try:
        ans = input(prompt + " [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans in ("y", "yes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="List items only, do not unpin or remove anything")
    args = parser.parse_args()

    print("Fetching Release pages, IPFS pins, seeding dirs...")
    releases = fetch_releaselist()
    pins = fetch_pins()
    seeding = [s.lower() for s in fetch_seeding_dirs()]

    candidates = []
    for r in releases:
        cid = r.get("ipfs_cid") or r.get("page_title") or ""
        title = r.get("title") or cid[:16]
        if not cid:
            continue

        try:
            ydata = yaml.safe_load(page_content(f"Release:{cid}")) or {}
        except Exception:
            ydata = {}
        if not isinstance(ydata, dict):
            ydata = {}

        reason = None
        if ydata.get("delete"):
            reason = "delete"
        elif ydata.get("unpin"):
            reason = "unpin"
        else:
            continue

        pinned = cid.lower() in pins
        seeded = cid.lower() in seeding
        pinned_on = ydata.get("pinned_on") or []

        if pinned or seeded or pinned_on:
            candidates.append({
                "cid": cid, "title": title, "reason": reason,
                "pinned": pinned, "seeded": seeded, "pinned_on": pinned_on,
            })

    if not candidates:
        print("Nothing to clean up — no deleted/retired releases have alive infrastructure.")
        return 0

    print(f"\nFound {len(candidates)} release(s) with cleanup pending:\n")
    for c in candidates:
        alive = []
        if c["pinned"]:
            alive.append("pinned")
        if c["seeded"]:
            alive.append("seeded")
        if c["pinned_on"]:
            alive.append(f"pinned_on={','.join(c['pinned_on'])}")
        print(f"  {c['cid'][:16]}... [{c['reason']}] {c['title']}")
        print(f"    alive: {', '.join(alive)}")

    if args.dry_run:
        print("\n(dry-run — nothing modified)")
        return 0

    print()
    for c in candidates:
        cid = c["cid"]
        print(f"--- {cid[:16]}... {c['title']} ---")
        if not confirm(f"  Purge infrastructure for this release?"):
            print("  skipped.\n")
            continue

        if c["pinned"]:
            print(f"  Unpinning from IPFS...")
            unpin_ipfs(cid)
        if c["seeded"]:
            print(f"  Removing seeding dir...")
            remove_seeding_dir(cid)

        if c["pinned_on"]:
            print(f"  NOTE: wiki YAML pinned_on is still {c['pinned_on']}.")
            print(f"        Edit Release:{cid} and clear/remove 'pinned_on' so the "
                  f"banner shows cleanly.")
        print()

    print("Done. Run audit-storage.py to verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
