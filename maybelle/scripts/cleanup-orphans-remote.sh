#!/bin/bash
#
# Clean up orphaned IPFS pins, seeding directories, and duplicate Release pages
#
# Usage:
#   ./maybelle/scripts/cleanup-orphans-remote.sh
#

set -euo pipefail

DK_HOST="root@delivery-kid.cryptograss.live"

echo "=== Orphan Cleanup ==="
echo ""

# Orphaned IPFS pins to remove
ORPHAN_PINS=(
    "bafybeidh6jwxri3jjwj2eeomxm7b5ofppz7c44sk3mo72s2ggap2nrdd5e"   # dupe of Token 8
    "bafybeidsjptidvb6wf6benznq2pxgnt5iyksgtecpmjoimlmswhtx2u5ua"   # dead/unresolvable
    "bafybeifkvoeqol4qyogvjeocehbcv4itonigfzu5txaih2kctpab7n6y2e"   # dupe of Token 2
    "QmSmB9XPMob8D3Y1GYHmXjMPSV4h89UnakBJ7z8EnfDLJt"               # half-uploaded 4masks
)

# Orphaned seeding directories to remove
ORPHAN_SEEDS=(
    "Bafybeidh6jwxri3jjwj2eeomxm7b5ofppz7c44sk3mo72s2ggap2nrdd5e"   # dupe of Token 8
)

echo "--- Unpinning ${#ORPHAN_PINS[@]} orphaned IPFS pins ---"
for cid in "${ORPHAN_PINS[@]}"; do
    echo -n "  Unpinning ${cid:0:16}... "
    if ssh "$DK_HOST" "docker exec ipfs ipfs pin rm $cid 2>/dev/null"; then
        echo "OK"
    else
        echo "already unpinned or not found"
    fi
done

echo ""
echo "--- Removing ${#ORPHAN_SEEDS[@]} orphaned seeding directories ---"
for cid in "${ORPHAN_SEEDS[@]}"; do
    echo -n "  Removing ${cid:0:16}... "
    size=$(ssh "$DK_HOST" "du -sh /mnt/storage-box/staging/seeding/$cid 2>/dev/null | cut -f1" || echo "?")
    if ssh "$DK_HOST" "rm -rf /mnt/storage-box/staging/seeding/$cid 2>/dev/null"; then
        echo "OK ($size freed)"
    else
        echo "not found"
    fi
done

echo ""
echo "--- Duplicate Release wiki pages to delete ---"
echo "  These need manual deletion by a wiki admin:"
echo "  - Release:Bafybeibxaykvwev5ofk6dx77as5borzlhupeilrmg6ozbsykx47e2puwje (dupe of Token 12)"
echo "  - Release:Bafybeifetbpuhslne5kpzwbdxq747le3jybt7vvwg7lldbchhc6s6aofmu (dupe of Token 13)"
echo "  - Release:Bafybeifqvfdhvf6w34rytzukctrgafvwbfh6vcub3ol5antadidxrvmhri (dupe of Token 7)"

echo ""
echo "=== Cleanup Complete ==="
echo ""
echo "Run audit-storage-remote.sh to verify."
