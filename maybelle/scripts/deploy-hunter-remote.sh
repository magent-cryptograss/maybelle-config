#!/bin/bash
# Deploy hunter from your laptop via maybelle
# This script SSHs to maybelle and triggers the Jenkins deploy job
# with optional database backup restoration

set -e

echo "=== Deploy Hunter via Maybelle ==="
echo ""

# Step 1: Select backup option
echo "Database backup options:"
echo "  1) none - Skip database restoration"
echo "  2) latest - Use most recent backup"
echo "  3) select - Choose specific backup file"
echo ""
read -p "Select option (1-3): " BACKUP_CHOICE

case $BACKUP_CHOICE in
    1)
        DB_BACKUP="none"
        ;;
    2)
        DB_BACKUP="latest"
        ;;
    3)
        DB_BACKUP="select"
        echo ""
        echo "Available backups:"
        ssh root@maybelle.cryptograss.live "ls -lh /var/jenkins_home/hunter-db-backups/*.dump 2>/dev/null || echo 'No backups found'"
        echo ""
        read -p "Enter backup filename (e.g., magenta_20251113_020000.dump): " BACKUP_FILE
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

# Step 2: Confirm deployment
echo ""
echo "Ready to deploy hunter with backup option: $DB_BACKUP"
if [ "$DB_BACKUP" = "select" ]; then
    echo "Backup file: $BACKUP_FILE"
fi
read -p "Continue? (y/n): " CONFIRM
if [ "$CONFIRM" != "y" ]; then
    echo "Deployment cancelled"
    exit 0
fi

# Step 3: Deploy via maybelle
echo ""
echo "Connecting to maybelle and triggering deployment..."
echo "You will be prompted for the hunter root SSH key passphrase during deployment."
echo ""

ssh -t root@maybelle.cryptograss.live << 'EOF'
# This runs on maybelle

# Check hunter root SSH key exists
if [ ! -f ~/.ssh/id_ed25519_hunter ]; then
    echo "Error: Hunter root key not found at ~/.ssh/id_ed25519_hunter"
    exit 1
fi

# Get Jenkins admin password from secrets directory
JENKINS_PASSWORD=$(cat /var/jenkins_home/secrets/initialAdminPassword 2>/dev/null)
if [ -z "$JENKINS_PASSWORD" ]; then
    echo "Error: Could not read Jenkins admin password"
    exit 1
fi

# Trigger Jenkins job
echo ""
echo "Triggering Jenkins deploy-hunter job..."

if [ "$DB_BACKUP" = "select" ]; then
    HTTP_CODE=$(curl -X POST "http://localhost:8080/job/deploy-hunter/buildWithParameters" \
        --user "admin:$JENKINS_PASSWORD" \
        --data-urlencode "DB_BACKUP=select" \
        --data-urlencode "BACKUP_FILE=$BACKUP_FILE" \
        -w "%{http_code}" \
        -o /tmp/jenkins_response.txt)
else
    HTTP_CODE=$(curl -X POST "http://localhost:8080/job/deploy-hunter/buildWithParameters" \
        --user "admin:$JENKINS_PASSWORD" \
        --data-urlencode "DB_BACKUP=$DB_BACKUP" \
        -w "%{http_code}" \
        -o /tmp/jenkins_response.txt)
fi

echo ""
if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
    echo "Deployment job triggered successfully!"
    echo "View progress at: https://maybelle.cryptograss.live/job/deploy-hunter/"
else
    echo "Error: Failed to trigger Jenkins job (HTTP $HTTP_CODE)"
    cat /tmp/jenkins_response.txt
    exit 1
fi
EOF

echo ""
echo "=== Deployment Complete ==="
echo "Check Jenkins UI for build status and logs"
