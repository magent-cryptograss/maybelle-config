#!/usr/bin/env python3
"""Purge infrastructure for items the wiki has flagged for cleanup.

Two kinds of cleanup:

1. Release pages marked ``delete: true`` or ``unpin: true`` —
   unpin from the delivery-kid IPFS node and remove the seeding dir.

2. ReleaseDraft pages marked ``abandoned: true`` (without
   ``abandoned_keep_files: true``) — remove the staging dir on
   delivery-kid. Drafts flagged with ``abandoned_keep_files: true``
   are left alone; the user explicitly asked to keep the files.

Per-item interactive confirmation. Wiki pages are not edited (so this
script doesn't need an admin bot password); for Releases it prints the
``pinned_on`` cleanup the user needs to do manually.

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
WIKI_BASE = "https://pickipedia.xyz/wiki"
IPFS_EMPTY_DIR = "qmunllspaccz1vlxqvkxqqlx5r1x345qqfhbsf67hva3nn"
NS_RELEASEDRAFT = 3006


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


def page_history(title: str) -> dict:
    """Fetch first + latest revision metadata for a page.

    Returns {creator, created_at, last_editor, last_edited_at, last_comment}.
    """
    info = {"creator": None, "created_at": None,
            "last_editor": None, "last_edited_at": None, "last_comment": None}
    # Latest revision
    latest = wiki_get({"action": "query", "titles": title, "prop": "revisions",
                       "rvlimit": "1", "rvprop": "timestamp|user|comment"})
    for p in latest.get("query", {}).get("pages", {}).values():
        for r in p.get("revisions", []):
            info["last_editor"] = r.get("user")
            info["last_edited_at"] = r.get("timestamp")
            info["last_comment"] = (r.get("comment") or "").strip() or None
    # First revision (page creation)
    first = wiki_get({"action": "query", "titles": title, "prop": "revisions",
                      "rvdir": "newer", "rvlimit": "1", "rvprop": "timestamp|user"})
    for p in first.get("query", {}).get("pages", {}).values():
        for r in p.get("revisions", []):
            info["creator"] = r.get("user")
            info["created_at"] = r.get("timestamp")
    return info


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


def fetch_release_draft_titles() -> list[str]:
    """Return all ReleaseDraft page titles (just the id part after the colon)."""
    titles = []
    cont = None
    while True:
        params = {"action": "query", "list": "allpages",
                  "apnamespace": str(NS_RELEASEDRAFT), "aplimit": "500"}
        if cont:
            params["apcontinue"] = cont
        data = wiki_get(params)
        for p in data.get("query", {}).get("allpages", []):
            t = p["title"]
            titles.append(t.split(":", 1)[1] if ":" in t else t)
        cont = data.get("continue", {}).get("apcontinue")
        if not cont:
            break
    return titles


def fetch_staging_dirs() -> set[str]:
    """Return the set of draft IDs that have a staging dir on delivery-kid."""
    _, out, _ = ssh(DK_HOST, "ls /mnt/storage-box/staging/drafts/ 2>/dev/null")
    return {line.strip() for line in out.splitlines() if line.strip()}


def remove_staging_dir(draft_id: str) -> bool:
    """Remove the staging dir for a draft. Case-insensitive match."""
    rc, out, _ = ssh(DK_HOST,
        f"ls /mnt/storage-box/staging/drafts/ 2>/dev/null | "
        f"grep -i '^{draft_id}$' | head -1")
    actual = out.strip()
    if not actual:
        return True  # already gone
    rc, _, stderr = ssh(DK_HOST,
        f"rm -rf /mnt/storage-box/staging/drafts/{actual}")
    if rc != 0:
        print(f"    rm failed: {stderr.strip()}")
        return False
    return True


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


def _alive_flags(c: dict) -> list[str]:
    alive = []
    if c["pinned"]:
        alive.append("pinned")
    if c["seeded"]:
        alive.append("seeded")
    if c["pinned_on"]:
        alive.append(f"pinned_on={','.join(c['pinned_on'])}")
    return alive


def _print_candidate(c: dict, terse: bool):
    if c["kind"] == "draft":
        if terse:
            print(f"  {c['draft_id'][:16]}... [abandoned] {c['title']}")
            print(f"    alive: staging dir")
            return
        print(f"  reason:     abandoned")
        if c.get("removal_reason"):
            print(f"  why:        {c['removal_reason']}")
        print(f"  alive:      staging dir")
        print(f"  page:       {c['url']}")
        if c.get("creator"):
            print(f"  created:    {c['created_at']} by {c['creator']}")
        if c.get("last_editor"):
            when = c.get("last_edited_at") or "?"
            who = c.get("last_editor") or "?"
            print(f"  last edit:  {when} by {who}")
            if c.get("last_comment"):
                print(f"    \"{c['last_comment']}\"")
        return

    alive = _alive_flags(c)
    if terse:
        print(f"  {c['cid'][:16]}... [{c['reason']}] {c['title']}")
        print(f"    alive: {', '.join(alive)}")
        return
    # Full detail view before the confirm prompt
    print(f"  reason:     {c['reason']}")
    if c.get("removal_reason"):
        print(f"  why:        {c['removal_reason']}")
    print(f"  alive:      {', '.join(alive)}")
    print(f"  page:       {c['url']}")
    if c.get("creator"):
        print(f"  created:    {c['created_at']} by {c['creator']}")
    if c.get("last_editor"):
        when = c.get("last_edited_at") or "?"
        who = c.get("last_editor") or "?"
        print(f"  last edit:  {when} by {who}")
        if c.get("last_comment"):
            print(f"    \"{c['last_comment']}\"")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="List items only, do not unpin or remove anything")
    args = parser.parse_args()

    print("Fetching release list...", end=" ", flush=True)
    releases = fetch_releaselist()
    print(f"{len(releases)} releases")

    print("Fetching IPFS pins...", end=" ", flush=True)
    pins = fetch_pins()
    print(f"{len(pins)} pins")

    print("Fetching seeding dirs...", end=" ", flush=True)
    seeding = [s.lower() for s in fetch_seeding_dirs()]
    print(f"{len(seeding)} dirs")

    print("Fetching ReleaseDraft titles...", end=" ", flush=True)
    draft_titles = fetch_release_draft_titles()
    print(f"{len(draft_titles)} drafts")

    print("Fetching staging dirs...", end=" ", flush=True)
    staging = {s.lower() for s in fetch_staging_dirs()}
    print(f"{len(staging)} dirs")

    # Per-release YAML scan is the slow part — each page_content is an HTTP
    # round trip. Show a dot per page fetched, `+` when we hit a candidate.
    print(f"Scanning {len(releases)} release pages for delete/unpin flags",
          end=" ", flush=True)
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
            print(".", end="", flush=True)
            continue

        pinned = cid.lower() in pins
        seeded = cid.lower() in seeding
        pinned_on = ydata.get("pinned_on") or []

        if pinned or seeded or pinned_on:
            print("+", end="", flush=True)
            hist = page_history(f"Release:{cid}")
            removal_reason = ydata.get("removal_reason")
            if not isinstance(removal_reason, str):
                removal_reason = None
            candidates.append({
                "kind": "release",
                "cid": cid, "title": title, "reason": reason,
                "removal_reason": removal_reason,
                "pinned": pinned, "seeded": seeded, "pinned_on": pinned_on,
                "url": f"{WIKI_BASE}/Release:{cid}",
                **hist,
            })
        else:
            # delete/unpin flag but nothing alive — already cleaned.
            print("·", end="", flush=True)
    print()

    print(f"Scanning {len(draft_titles)} ReleaseDraft pages for abandoned flag",
          end=" ", flush=True)
    for draft_title in draft_titles:
        try:
            ydata = yaml.safe_load(page_content(f"ReleaseDraft:{draft_title}")) or {}
        except Exception:
            ydata = {}
        if not isinstance(ydata, dict):
            ydata = {}

        # Only abandoned, and only when the user didn't explicitly ask to keep
        # the files. abandoned_keep_files: true → leave alone; the wiki YAML is
        # the source of truth and the user has signaled "I want these around".
        if not ydata.get("abandoned") or ydata.get("abandoned_keep_files"):
            print(".", end="", flush=True)
            continue

        # The wiki page title is the draft_id (uppercased first letter from
        # MediaWiki normalization), but the staging dir uses the raw uuid.
        draft_id = ydata.get("draft_id") or draft_title
        if draft_id.lower() not in staging:
            # Already cleaned up — wiki YAML still says abandoned, but
            # staging dir is gone. Nothing to do.
            print("·", end="", flush=True)
            continue

        print("+", end="", flush=True)
        hist = page_history(f"ReleaseDraft:{draft_title}")
        abandoned_reason = ydata.get("abandoned_reason")
        if not isinstance(abandoned_reason, str):
            abandoned_reason = None
        candidates.append({
            "kind": "draft",
            "draft_id": draft_id,
            "title": f"ReleaseDraft:{draft_title}",
            "removal_reason": abandoned_reason,
            "url": f"{WIKI_BASE}/ReleaseDraft:{draft_title}",
            **hist,
        })
    print()

    if not candidates:
        print("Nothing to clean up — no deleted/retired releases or "
              "abandoned drafts have alive infrastructure.")
        return 0

    print(f"\nFound {len(candidates)} item(s) with cleanup pending:\n")
    for c in candidates:
        _print_candidate(c, terse=True)

    if args.dry_run:
        print("\n(dry-run — nothing modified)")
        return 0

    print()
    for c in candidates:
        identifier = c["draft_id"] if c["kind"] == "draft" else c["cid"]
        print(f"--- {identifier[:16]}... {c['title']} ---")
        _print_candidate(c, terse=False)

        if c["kind"] == "draft":
            if not confirm("  Remove staging dir for this draft?"):
                print("  skipped.\n")
                continue
            print("  Removing staging dir...")
            remove_staging_dir(c["draft_id"])
            print()
            continue

        if not confirm("  Purge infrastructure for this release?"):
            print("  skipped.\n")
            continue

        if c["pinned"]:
            print("  Unpinning from IPFS...")
            unpin_ipfs(c["cid"])
        if c["seeded"]:
            print("  Removing seeding dir...")
            remove_seeding_dir(c["cid"])

        if c["pinned_on"]:
            print(f"  NOTE: wiki YAML pinned_on is still {c['pinned_on']}.")
            print(f"        Edit Release:{c['cid']} and clear/remove 'pinned_on' "
                  f"so the banner shows cleanly.")
        print()

    print("Done. Run audit-storage.py to verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
