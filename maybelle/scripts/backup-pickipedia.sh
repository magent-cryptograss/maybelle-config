#!/bin/bash
# Daily PickiPedia MySQL backup from NFS
# Pulls dump via SSH for use in hunter preview environments

set -euo pipefail

LOG_FILE="/var/log/pickipedia-backup.log"
BACKUP_DIR="/mnt/persist/pickipedia/backups"
SSH_KEY="/root/.ssh/id_ed25519_nfs"
NFS_HOST="ssh.nyc1.nearlyfreespeech.net"
NFS_USER="jmyles_pickipedia"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $*" >> "$LOG_FILE"
}

log "Starting PickiPedia backup"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Run mysqldump on NFS and pipe back
BACKUP_FILE="$BACKUP_DIR/pickipedia_$(date +%Y%m%d).sql.gz"

# MySQL credentials file (deployed by ansible from vault)
MYSQL_CNF="/root/.pickipedia-my.cnf"

# Read credentials from file
DB_USER=$(grep -oP 'user=\K.*' "$MYSQL_CNF")
DB_PASS=$(grep -oP 'password=\K.*' "$MYSQL_CNF")

if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "${NFS_USER}@${NFS_HOST}" \
    "mysqldump -h pickipedia.db -u '$DB_USER' -p'$DB_PASS' pickipedia" 2>> "$LOG_FILE" \
    | gzip > "$BACKUP_FILE"; then

    log "Backup successful: $BACKUP_FILE ($(stat -c%s "$BACKUP_FILE") bytes)"

    # Keep last 7 days of backups
    find "$BACKUP_DIR" -name "pickipedia_*.sql.gz" -mtime +7 -delete

    # Sync to hunter for preview environments
    log "Syncing to hunter..."
    if rsync -avz "$BACKUP_DIR"/ root@hunter.cryptograss.live:/opt/magenta/pickipedia-backups/ >> "$LOG_FILE" 2>&1; then
        log "Sync to hunter complete"
    else
        log "WARNING - sync to hunter failed"
    fi
else
    log "Backup FAILED"
    rm -f "$BACKUP_FILE"  # Remove partial file
    exit 1
fi
