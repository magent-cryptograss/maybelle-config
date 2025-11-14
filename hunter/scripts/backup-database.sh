#!/bin/bash
# Backup magenta database for retrieval by maybelle
set -e

BACKUP_DIR="/var/backups/magenta"

# Get current Ethereum block height for temporal anchoring
BLOCK_HEIGHT=$(curl -s https://eth.blockscout.com/api/v2/stats | jq -r .total_blocks)
if [ -z "$BLOCK_HEIGHT" ] || [ "$BLOCK_HEIGHT" = "null" ]; then
    # Fallback to timestamp if block fetch fails
    BLOCK_HEIGHT=$(date +%Y%m%d_%H%M%S)
    echo "Warning: Could not fetch block height, using timestamp instead"
fi

BACKUP_FILE="magenta_memory_${BLOCK_HEIGHT}.dump"
CONTAINER_NAME="magenta-postgres"
DB_NAME="magenta_memory"
DB_USER="magent"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Create backup
echo "Creating database backup: $BACKUP_FILE"
docker exec "$CONTAINER_NAME" pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$BACKUP_DIR/$BACKUP_FILE"

# Create 'latest' symlink
ln -sf "$BACKUP_FILE" "$BACKUP_DIR/latest.dump"

# Count messages to verify
MSG_COUNT=$(docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -t -c "SELECT COUNT(*) FROM conversations_message;" | tr -d ' ')
echo "Backup complete: $MSG_COUNT messages"

# Keep only last 30 backups (only delete if we have more than 30)
BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/magenta_memory_*.dump 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt 30 ]; then
    # Delete oldest backups beyond 30
    ls -1t "$BACKUP_DIR"/magenta_memory_*.dump | tail -n +31 | xargs rm -f
    echo "Old backups cleaned up (kept last 30 backups)"
else
    echo "Backup count: $BACKUP_COUNT (keeping all - threshold is 30)"
fi
