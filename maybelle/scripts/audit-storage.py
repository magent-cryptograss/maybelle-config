#!/usr/bin/env python3
"""Audit delivery-kid storage against PickiPedia Release/ReleaseDraft pages.

Cross-references IPFS pins, BitTorrent seeding dirs, and staging draft dirs
against what's actually tracked in the wiki. Flags orphans in both directions
so nothing falls through the cracks.

Checks performed:
  1. IPFS pins vs Release pages
     - ORPHAN PIN: pinned but no Release page
     - MISSING PIN: Release page but not pinned (unless delete/unpin flag)
     - DELETED / RETIRED: Release pages flagged delete/unpin (and pin status)

  2. BitTorrent seeding dirs vs Release pages
     - ORPHAN SEED: seeding dir but no Release page
     - MISSING SEED: Release page but no seeding dir

  3. Staging drafts vs ReleaseDraft pages
     - ORPHAN DRAFT: staging dir but no wiki page
     - STALLED DRAFT: wiki page + empty/incomplete staging (no draft.json or empty upload/)
     - DEAD WIKI DRAFT: wiki page but no staging, never finalized
     - ABANDONED DRAFT: wiki page flagged `abandoned: true` (shown separately,
       with alive infra flagged as CLEANUP PENDING)

  4. Blue Railroad chain data vs Release pages
     - Delegates to audit-chain-data.py on maybelle

Usage: maybelle/scripts/audit-storage.py
"""

import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import yaml


DK_HOST = "root@delivery-kid.cryptograss.live"
MAYBELLE_HOST = "root@maybelle.cryptograss.live"
WIKI_API = "https://pickipedia.xyz/api.php"

# Every kubo node has this empty directory pinned, so it always appears in pin listings.
IPFS_EMPTY_DIR = "qmunllspaccz1vlxqvkxqqlx5r1x345qqfhbsf67hva3nn"


def ssh(host: str, cmd: str) -> str:
    """Run a command on a remote host via SSH, return stdout. Empty string on failure."""
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, cmd],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        print(f"  [ssh {host} failed: {result.stderr.strip()}]", file=sys.stderr)
        return ""
    return result.stdout


def wiki_get(params: dict) -> dict:
    params = {**params, "format": "json"}
    url = f"{WIKI_API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def allpages(namespace: int) -> list[str]:
    """Return all page titles (after the colon) in a namespace."""
    titles = []
    cont = None
    while True:
        params = {"action": "query", "list": "allpages",
                  "apnamespace": str(namespace), "aplimit": "500"}
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


def page_content(title: str) -> str:
    data = wiki_get({"action": "query", "titles": title,
                     "prop": "revisions", "rvprop": "content", "rvslots": "main"})
    for p in data.get("query", {}).get("pages", {}).values():
        for r in p.get("revisions", []):
            return r.get("slots", {}).get("main", {}).get("*", "") or ""
    return ""


def page_comments(title: str, limit: int = 50) -> list[str]:
    data = wiki_get({"action": "query", "titles": title, "prop": "revisions",
                     "rvprop": "comment", "rvlimit": str(limit)})
    comments = []
    for p in data.get("query", {}).get("pages", {}).values():
        for r in p.get("revisions", []):
            comments.append(r.get("comment") or "")
    return comments


def fetch_releaselist() -> list[dict]:
    """Use the wiki's releaselist API module to get all releases + their YAML fields."""
    with urllib.request.urlopen(f"{WIKI_API}?action=releaselist&format=json", timeout=30) as resp:
        return json.loads(resp.read().decode()).get("releases", [])


def fetch_pins() -> set[str]:
    """Return set of lowercase recursive pin CIDs on the delivery-kid IPFS node."""
    out = ssh(DK_HOST, "docker exec ipfs ipfs pin ls --type=recursive -q 2>/dev/null")
    return {line.strip().lower() for line in out.splitlines() if line.strip()}


def fetch_seeding_dirs() -> list[str]:
    out = ssh(DK_HOST, "ls /mnt/storage-box/staging/seeding/ 2>/dev/null")
    return [line.strip() for line in out.splitlines() if line.strip()]


def fetch_staging_drafts() -> list[dict]:
    """Return [{id, has_draft_json, upload_files, size_kb}, ...] for each staging draft dir."""
    script = r"""
cd /mnt/storage-box/staging/drafts 2>/dev/null || exit 0
for d in */; do
  d=${d%/}
  [ -z "$d" ] && continue
  has_json=no; [ -f "$d/draft.json" ] && has_json=yes
  upload_files=0
  [ -d "$d/upload" ] && upload_files=$(find "$d/upload" -type f 2>/dev/null | wc -l | tr -d ' ')
  size_kb=$(du -sk "$d" 2>/dev/null | cut -f1)
  echo "$d $has_json $upload_files $size_kb"
done
"""
    out = ssh(DK_HOST, script)
    drafts = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            drafts.append({
                "id": parts[0],
                "has_draft_json": parts[1] == "yes",
                "upload_files": int(parts[2] or 0),
                "size_kb": int(parts[3] or 0),
            })
    return drafts


def human_size(kb: int) -> str:
    if kb < 1024:
        return f"{kb}K"
    if kb < 1024 * 1024:
        return f"{kb // 1024}M"
    return f"{kb / 1024 / 1024:.1f}G"


def audit_pins(releases: list[dict], pins: set[str], seeding: list[str]) -> dict:
    """Cross-reference Release pages against IPFS pins + seeding dirs.

    Captures per-release state (pinned, seeded, pinned_on) so the summary can
    flag cases where YAML says delete/unpin but the infrastructure is still
    alive — the "cleanup pending" state.
    """
    release_cids = {r.get("ipfs_cid", "").lower() for r in releases if r.get("ipfs_cid")}
    seed_cids = {s.lower() for s in seeding}

    orphan_pins = []
    for pin in pins:
        if pin == IPFS_EMPTY_DIR:
            continue
        if pin not in release_cids:
            orphan_pins.append(pin)

    missing_pins, deleted, retired = [], [], []
    for r in releases:
        cid = r.get("ipfs_cid") or r.get("page_title") or ""
        title = r.get("title") or cid[:16]
        pinned = cid.lower() in pins
        seeded = cid.lower() in seed_cids

        # Parse YAML for delete/unpin flags + pinned_on
        ydata = {}
        try:
            raw = page_content(f"Release:{cid}")
            parsed = yaml.safe_load(raw) if raw else None
            if isinstance(parsed, dict):
                ydata = parsed
        except Exception:
            pass

        pinned_on = ydata.get("pinned_on") or []
        entry = {"cid": cid, "title": title, "pinned": pinned,
                 "seeded": seeded, "pinned_on": pinned_on}
        if ydata.get("delete"):
            deleted.append(entry)
        elif ydata.get("unpin"):
            retired.append(entry)
        elif not pinned:
            missing_pins.append(entry)

    return {"orphan_pins": orphan_pins, "missing_pins": missing_pins,
            "deleted": deleted, "retired": retired}


def audit_seeding(releases: list[dict], seeding: list[str]) -> dict:
    release_cids = {r.get("ipfs_cid", "").lower() for r in releases if r.get("ipfs_cid")}
    seed_cids = {s.lower() for s in seeding}

    orphan_seeds = [s for s in seeding if s.lower() not in release_cids]
    missing_seeds = sorted(release_cids - seed_cids - {""})
    return {"orphan_seeds": orphan_seeds, "missing_seeds": missing_seeds}


def fetch_abandoned_drafts(wiki_draft_ids: list[str]) -> dict[str, str]:
    """Return {draft_id_lower: reason} for drafts flagged `abandoned: true`."""
    abandoned = {}
    for w in wiki_draft_ids:
        try:
            raw = page_content(f"ReleaseDraft:{w}")
            data = yaml.safe_load(raw) if raw else None
        except Exception:
            continue
        if isinstance(data, dict) and data.get("abandoned"):
            abandoned[w.lower()] = data.get("abandoned_reason") or ""
    return abandoned


def audit_drafts(
    wiki_draft_ids: list[str],
    staging_drafts: list[dict],
    abandoned: dict[str, str],
) -> dict:
    staging_by_id = {d["id"].lower(): d for d in staging_drafts}
    wiki_lower = {w.lower(): w for w in wiki_draft_ids}

    orphan_drafts = []      # staging dir, no wiki page
    stalled_drafts = []     # wiki page + no draft.json + empty upload/
    dead_wiki_drafts = []   # wiki page, no staging, never finalized
    finalized_gone = []     # wiki page, no staging, WAS finalized (expected)
    abandoned_drafts = []   # wiki page flagged `abandoned: true`

    for d in staging_drafts:
        lower = d["id"].lower()
        if lower not in wiki_lower:
            orphan_drafts.append(d)
            continue
        if lower in abandoned:
            abandoned_drafts.append({
                **d, "wiki_title": wiki_lower[lower],
                "reason": abandoned[lower], "has_staging": True,
            })
        elif not d["has_draft_json"] and d["upload_files"] == 0:
            stalled_drafts.append({**d, "wiki_title": wiki_lower[lower]})

    seen_abandoned_with_staging = {a["wiki_title"].lower() for a in abandoned_drafts}

    for w in wiki_draft_ids:
        lower = w.lower()
        if lower in staging_by_id:
            continue
        if lower in abandoned and lower not in seen_abandoned_with_staging:
            abandoned_drafts.append({
                "wiki_title": w, "reason": abandoned[lower], "has_staging": False,
            })
            continue
        # Check if ever finalized
        is_finalized = False
        try:
            for c in page_comments(f"ReleaseDraft:{w}"):
                if "pinned to IPFS" in c:
                    is_finalized = True
                    break
        except Exception:
            pass
        if is_finalized:
            finalized_gone.append(w)
        else:
            dead_wiki_drafts.append(w)

    return {"orphan_drafts": orphan_drafts, "stalled_drafts": stalled_drafts,
            "dead_wiki_drafts": dead_wiki_drafts, "finalized_gone": finalized_gone,
            "abandoned_drafts": abandoned_drafts}


def print_section(title: str):
    print(f"\n--- {title} ---")


def _alive_flags(entry: dict) -> list[str]:
    flags = []
    if entry.get("pinned"):
        flags.append("pinned")
    if entry.get("seeded"):
        flags.append("seeded")
    if entry.get("pinned_on"):
        flags.append(f"pinned_on={','.join(entry['pinned_on'])}")
    return flags


def print_pin_audit(result: dict, release_count: int):
    if result["deleted"]:
        print(f"  Deleted releases ({len(result['deleted'])}):")
        for r in result["deleted"]:
            alive = _alive_flags(r)
            state = "CLEANUP PENDING: " + ", ".join(alive) if alive else "fully cleaned"
            print(f"    {r['cid'][:16]}... {r['title']} [{state}]")
    if result["retired"]:
        print(f"  Retired releases ({len(result['retired'])}):")
        for r in result["retired"]:
            alive = _alive_flags(r)
            state = "CLEANUP PENDING: " + ", ".join(alive) if alive else "fully cleaned"
            print(f"    {r['cid'][:16]}... {r['title']} [{state}]")
    if result["missing_pins"]:
        print(f"  MISSING PINS ({len(result['missing_pins'])}):")
        for r in result["missing_pins"]:
            print(f"    {r['cid'][:16]}... {r['title']}")
    if result["orphan_pins"]:
        print(f"  ORPHAN PINS ({len(result['orphan_pins'])}) — pinned but no Release page:")
        for p in result["orphan_pins"]:
            print(f"    {p}")

    total = release_count
    d, r, m = len(result["deleted"]), len(result["retired"]), len(result["missing_pins"])
    active = total - d - r
    print(f"  {active} active, {d} deleted, {r} retired, {m} missing pins")


def print_seeding_audit(result: dict, seed_count: int):
    if result["orphan_seeds"]:
        print(f"  ORPHAN SEEDS ({len(result['orphan_seeds'])}) — seeding dir but no Release page:")
        for s in result["orphan_seeds"]:
            size = ssh(DK_HOST, f"du -sh /mnt/storage-box/staging/seeding/{s} 2>/dev/null | cut -f1").strip()
            print(f"    {s} ({size or '?'})")
    if result["missing_seeds"]:
        print(f"  MISSING SEEDS ({len(result['missing_seeds'])}) — Release page but no seeding dir:")
        for c in result["missing_seeds"]:
            print(f"    {c}")
    print(f"  {seed_count} total seeding dirs, "
          f"{len(result['orphan_seeds'])} orphaned, "
          f"{len(result['missing_seeds'])} missing")


def print_draft_audit(result: dict, wiki_count: int, staging_count: int):
    if result["orphan_drafts"]:
        print(f"  ORPHAN DRAFTS ({len(result['orphan_drafts'])}) — staging dir, no wiki page:")
        for d in result["orphan_drafts"]:
            print(f"    {d['id']} ({human_size(d['size_kb'])})")
    if result["stalled_drafts"]:
        print(f"  STALLED DRAFTS ({len(result['stalled_drafts'])}) — wiki page + empty staging:")
        for d in result["stalled_drafts"]:
            print(f"    {d['id']} (no draft.json, upload/ empty)")
    if result["dead_wiki_drafts"]:
        print(f"  DEAD WIKI DRAFTS ({len(result['dead_wiki_drafts'])}) — "
              f"never finalized, no staging (safe to delete from wiki):")
        for w in result["dead_wiki_drafts"]:
            print(f"    {w}")
    if result["abandoned_drafts"]:
        print(f"  ABANDONED DRAFTS ({len(result['abandoned_drafts'])}) — flagged `abandoned: true`:")
        for a in result["abandoned_drafts"]:
            state = "CLEANUP PENDING: staging dir" if a["has_staging"] else "clean"
            reason = f" — {a['reason']}" if a["reason"] else ""
            print(f"    {a['wiki_title'][:36]} [{state}]{reason}")
    if result["finalized_gone"]:
        print(f"  {len(result['finalized_gone'])} finalized wiki drafts without staging "
              f"(expected — staging cleaned on finalize)")
    print(f"  {wiki_count} wiki drafts, {staging_count} staging dirs")


def main():
    print("=== Storage Audit ===\n")

    print("Fetching Release pages from wiki...")
    releases = fetch_releaselist()
    release_count = len(releases)
    print(f"  {release_count} Release pages")

    print("Fetching ReleaseDraft pages from wiki...")
    wiki_draft_ids = allpages(3006)
    draft_count = len(wiki_draft_ids)
    print(f"  {draft_count} ReleaseDraft pages")

    print("Fetching IPFS pins from delivery-kid...")
    pins = fetch_pins()
    pin_count = len(pins)
    print(f"  {pin_count} recursive pins")

    print("Fetching seeding directories...")
    seeding = fetch_seeding_dirs()
    seed_count = len(seeding)
    print(f"  {seed_count} seeding directories")

    print("Fetching staging drafts...")
    staging_drafts = fetch_staging_drafts()
    staging_count = len(staging_drafts)
    print(f"  {staging_count} staging draft directories")

    print_section("IPFS Pins vs Release Pages")
    pin_result = audit_pins(releases, pins, seeding)
    print_pin_audit(pin_result, release_count)

    print_section("Seeding Dirs vs Release Pages")
    seed_result = audit_seeding(releases, seeding)
    print_seeding_audit(seed_result, seed_count)

    print("Scanning wiki drafts for `abandoned: true`...")
    abandoned = fetch_abandoned_drafts(wiki_draft_ids)
    print(f"  {len(abandoned)} abandoned")

    print_section("Staging Drafts vs Wiki ReleaseDraft Pages")
    draft_result = audit_drafts(wiki_draft_ids, staging_drafts, abandoned)
    print_draft_audit(draft_result, draft_count, staging_count)

    print_section("Blue Railroad Chain Data vs Releases")
    script_dir = Path(__file__).parent
    chain_audit = script_dir / "audit-chain-data.py"
    if chain_audit.exists():
        try:
            with open(chain_audit) as f:
                script_body = f.read()
            subprocess.run(
                ["ssh", MAYBELLE_HOST, "docker exec -i jenkins python3 -"],
                input=script_body, text=True, timeout=120,
            )
        except Exception as e:
            print(f"  (could not check chain data: {e})")
    else:
        print(f"  (missing {chain_audit})")

    print_section("Summary")
    print(f"  Release pages:       {release_count}")
    print(f"  ReleaseDraft pages:  {draft_count}")
    print(f"  IPFS pins:           {pin_count}")
    print(f"  Seeding dirs:        {seed_count}")
    print(f"  Staging drafts:      {staging_count}")
    print()
    print(f"  Orphan pins:         {len(pin_result['orphan_pins'])}")
    print(f"  Missing pins:        {len(pin_result['missing_pins'])}")
    print(f"  Orphan seeds:        {len(seed_result['orphan_seeds'])}")
    print(f"  Missing seeds:       {len(seed_result['missing_seeds'])}")
    print(f"  Orphan drafts:       {len(draft_result['orphan_drafts'])}")
    print(f"  Stalled drafts:      {len(draft_result['stalled_drafts'])}")
    print(f"  Dead wiki drafts:    {len(draft_result['dead_wiki_drafts'])}")
    print(f"  Abandoned drafts:    {len(draft_result['abandoned_drafts'])}")

    cleanup_pending = sum(
        1 for r in pin_result["deleted"] + pin_result["retired"]
        if _alive_flags(r)
    )
    cleanup_pending += sum(1 for a in draft_result["abandoned_drafts"] if a["has_staging"])
    print(f"  Cleanup pending:     {cleanup_pending} "
          f"(deleted/retired releases + abandoned drafts with alive infra)")

    print("\n=== Audit Complete ===")


if __name__ == "__main__":
    main()
