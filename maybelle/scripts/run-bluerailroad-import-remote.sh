#!/bin/bash
#
# Run Blue Railroad import from your laptop via maybelle
#
# Usage:
#   ./maybelle/scripts/run-bluerailroad-import-remote.sh
#

set -euo pipefail

MAYBELLE="root@maybelle.cryptograss.live"
REMOTE_SCRIPT="/mnt/persist/maybelle-config/maybelle/scripts/run-bluerailroad-import.sh"

echo "=== Blue Railroad Import (via maybelle) ==="
echo ""

ssh -t "$MAYBELLE" "$REMOTE_SCRIPT"
