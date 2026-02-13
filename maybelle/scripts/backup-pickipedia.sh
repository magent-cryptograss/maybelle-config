#!/bin/bash
# Daily PickiPedia MySQL backup from VPS
# Pulls dump via SSH for disaster recovery and hunter preview environments

set -euo pipefail

LOG_FILE="/var/log/pickipedia-backup.log"
BACKUP_DIR="/mnt/persist/pickipedia/backups"
SSH_KEY="/root/.ssh/id_ed25519_hunter"
VPS_HOST="5.78.112.39"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $*" >> "$LOG_FILE"
}

log "Starting PickiPedia backup from VPS"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Backup filename
BACKUP_FILE="$BACKUP_DIR/pickipedia_$(date +%Y%m%d).sql.gz"

# Run mysqldump on VPS and pipe back
# The VPS has local MySQL with root access via socket
if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@${VPS_HOST}" \
    "mysqldump pickipedia" 2>> "$LOG_FILE" \
    | gzip > "$BACKUP_FILE"; then

    log "Backup successful: $BACKUP_FILE ($(stat -c%s "$BACKUP_FILE") bytes)"

    # Keep last 3 days of backups (persistent volume is small, offsite has longer retention)
    find "$BACKUP_DIR" -name "pickipedia_*.sql.gz" -mtime +3 -delete

    # Sync to hunter for preview environments
    log "Syncing to hunter..."
    if rsync -avz "$BACKUP_DIR"/ root@hunter.cryptograss.live:/opt/magenta/pickipedia-backups/ >> "$LOG_FILE" 2>&1; then
        log "Sync to hunter complete"
    else
        log "WARNING - sync to hunter failed"
    fi

    # Backup only recent images (modified in last 7 days) to maybelle
    # Full image archive lives on VPS; this is just recent disaster recovery
    log "Backing up recent images from VPS..."
    IMAGES_BACKUP_DIR="$BACKUP_DIR/images"
    mkdir -p "$IMAGES_BACKUP_DIR"

    # Use rsync with find to only grab files modified in last 7 days
    # This keeps the backup small while protecting recent uploads
    # Exclude temp/ directory - it's just transient upload/thumbnail processing
    if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@${VPS_HOST}" \
        "find /var/www/pickipedia/images -type f -mtime -7 -not -path '*/temp/*' -print0" 2>/dev/null \
        | rsync -avz --files-from=- --from0 -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=no" \
        "root@${VPS_HOST}:/" "$IMAGES_BACKUP_DIR/" >> "$LOG_FILE" 2>&1; then
        RECENT_COUNT=$(find "$IMAGES_BACKUP_DIR" -type f -mtime -7 2>/dev/null | wc -l)
        log "Recent images backup complete: $RECENT_COUNT files from last 7 days"
    else
        log "WARNING - recent images backup failed"
    fi

    # Clean up old local image backups (older than 14 days)
    find "$IMAGES_BACKUP_DIR" -type f -mtime +14 -delete 2>/dev/null || true
    # Remove empty directories
    find "$IMAGES_BACKUP_DIR" -type d -empty -delete 2>/dev/null || true
else
    log "Backup FAILED"
    rm -f "$BACKUP_FILE"  # Remove partial file
    exit 1
fi
