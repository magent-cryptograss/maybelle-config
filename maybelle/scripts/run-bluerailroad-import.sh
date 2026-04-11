#!/bin/bash
#
# Run Blue Railroad import manually on maybelle
#
# Usage from laptop:
#   ssh root@maybelle.cryptograss.live /mnt/persist/maybelle-config/maybelle/scripts/run-bluerailroad-import.sh
#

set -euo pipefail

CHAIN_DATA="/var/jenkins_home/shared/chain_data/chainData.json"

echo "=== Blue Railroad Import ==="

# Check chain data exists
docker exec jenkins test -f "$CHAIN_DATA" || {
    echo "ERROR: Chain data not found at $CHAIN_DATA"
    exit 1
}

echo "Running import..."
docker exec jenkins bash -c "
    /opt/blue-railroad-import/bin/python -m blue_railroad_import.cli import \
        --chain-data $CHAIN_DATA \
        --wiki-url https://pickipedia.xyz \
        --username \"\$BLUERAILROAD_BOT_USERNAME\" \
        --password \"\$BLUERAILROAD_BOT_PASSWORD\" \
        -v
"

echo ""
echo "Running torrent enrichment..."
docker exec jenkins bash -c "
    /opt/blue-railroad-import/bin/python -m blue_railroad_import.cli enrich-torrents \
        --wiki-url https://pickipedia.xyz \
        --username \"\$BLUERAILROAD_BOT_USERNAME\" \
        --password \"\$BLUERAILROAD_BOT_PASSWORD\" \
        --delivery-kid-api-key \"\$DELIVERY_KID_API_KEY\" \
        -v
"

echo ""
echo "Running IPFS metadata enrichment..."
docker exec jenkins bash -c "
    /opt/blue-railroad-import/bin/python -m blue_railroad_import.cli enrich-ipfs \
        --wiki-url https://pickipedia.xyz \
        --username \"\$BLUERAILROAD_BOT_USERNAME\" \
        --password \"\$BLUERAILROAD_BOT_PASSWORD\" \
        -v
"

echo ""
echo "=== Complete ==="
