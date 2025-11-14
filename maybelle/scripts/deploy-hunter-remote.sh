#!/bin/bash
# Deploy hunter from your laptop via maybelle
# Uses SSH agent forwarding for secure key access
# Posts deployment logs to Jenkins for history/tracking

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

# Step 3: Set up SSH agent with hunter key
echo ""
echo "Setting up SSH agent with hunter root key..."

# Start ssh-agent
eval $(ssh-agent -s) > /dev/null

# Add key (will prompt for passphrase)
ssh-add ~/.ssh/id_ed25519_hunter

if [ $? -ne 0 ]; then
    echo "Error: Failed to add SSH key to agent"
    ssh-agent -k > /dev/null
    exit 1
fi

echo "SSH agent configured successfully"

# Step 4: Execute deployment on maybelle with agent forwarding
echo ""
echo "Connecting to maybelle and deploying hunter..."
echo "Logs will be captured and posted to Jenkins for history."
echo ""

# Create temp file for deployment script
DEPLOY_SCRIPT=$(mktemp)
cat > "$DEPLOY_SCRIPT" << 'DEPLOY_EOF'
#!/bin/bash
set -e

# Verify SSH agent forwarding is working
if [ -z "$SSH_AUTH_SOCK" ]; then
    echo "ERROR: SSH agent forwarding not available"
    exit 1
fi

echo "=== Starting Hunter Deployment ==="
echo "Backup option: $DB_BACKUP"
echo "Deployment time: $(date)"
echo ""

# Test SSH connectivity to hunter
echo "Testing connection to hunter..."
if ssh -o BatchMode=yes -o ConnectTimeout=10 root@hunter.cryptograss.live 'echo "Connection successful"'; then
    echo "✓ SSH connection to hunter verified"
else
    echo "✗ Failed to connect to hunter"
    exit 1
fi

# Install maybelle backup key on hunter
echo ""
echo "Installing backup SSH key on hunter..."
ssh root@hunter.cryptograss.live '
    # Create backupuser if doesn't exist
    id backupuser || useradd -m -s /bin/bash backupuser

    # Create .ssh directory
    mkdir -p /home/backupuser/.ssh
    chmod 700 /home/backupuser/.ssh
    chown backupuser:backupuser /home/backupuser/.ssh
'

# Copy maybelle's backup public key to hunter
scp /var/jenkins_home/.ssh/id_ed25519_backup.pub root@hunter.cryptograss.live:/tmp/maybelle_backup.pub

ssh root@hunter.cryptograss.live '
    # Install the key
    cat /tmp/maybelle_backup.pub >> /home/backupuser/.ssh/authorized_keys
    chmod 600 /home/backupuser/.ssh/authorized_keys
    chown backupuser:backupuser /home/backupuser/.ssh/authorized_keys
    rm /tmp/maybelle_backup.pub
'

# Handle database backup if requested
if [ "$DB_BACKUP" = "latest" ]; then
    echo ""
    echo "Copying latest database backup to hunter..."
    scp /var/jenkins_home/hunter-db-backups/latest.dump root@hunter.cryptograss.live:/tmp/restore_db.dump
elif [ "$DB_BACKUP" = "select" ]; then
    echo ""
    echo "Copying selected database backup to hunter..."
    scp "/var/jenkins_home/hunter-db-backups/$BACKUP_FILE" root@hunter.cryptograss.live:/tmp/restore_db.dump
fi

# Clone/update maybelle-config on hunter
echo ""
echo "Updating maybelle-config repository on hunter..."
ssh root@hunter.cryptograss.live '
    if [ ! -d /root/maybelle-config ]; then
        git clone https://github.com/cryptograss/maybelle-config.git /root/maybelle-config
    fi
    cd /root/maybelle-config
    git fetch origin
    git checkout hunter-deploy
    git pull origin hunter-deploy
'

# Execute deployment
echo ""
echo "Executing hunter deployment..."
echo "========================================"
if [ "$DB_BACKUP" = "none" ]; then
    ssh -t root@hunter.cryptograss.live 'cd /root/maybelle-config/hunter && ./deploy.sh --do-not-copy-database'
else
    ssh -t root@hunter.cryptograss.live 'cd /root/maybelle-config/hunter && ./deploy.sh -e db_dump_file=/tmp/restore_db.dump'
fi
echo "========================================"

echo ""
echo "=== Deployment Complete ==="
DEPLOY_EOF

chmod +x "$DEPLOY_SCRIPT"

# Execute deployment with SSH agent forwarding, capture output
LOGFILE=$(mktemp)
if ssh -A root@maybelle.cryptograss.live "DB_BACKUP='$DB_BACKUP' BACKUP_FILE='$BACKUP_FILE' bash -s" < "$DEPLOY_SCRIPT" 2>&1 | tee "$LOGFILE"; then
    DEPLOY_STATUS="SUCCESS"
    DEPLOY_RESULT="success"
else
    DEPLOY_STATUS="FAILURE"
    DEPLOY_RESULT="failure"
fi

# Clean up local temp script
rm -f "$DEPLOY_SCRIPT"

# Step 5: Post results to Jenkins
echo ""
echo "Posting deployment logs to Jenkins..."

# Get next build number from Jenkins
JENKINS_PASSWORD=$(ssh root@maybelle.cryptograss.live "docker exec jenkins printenv JENKINS_ADMIN_PASSWORD")
BUILD_NUMBER=$(ssh root@maybelle.cryptograss.live "curl -s --user 'admin:$JENKINS_PASSWORD' 'http://localhost:8080/job/deploy-hunter/api/json' | grep -o '\"nextBuildNumber\":[0-9]*' | cut -d: -f2")

if [ -z "$BUILD_NUMBER" ]; then
    echo "Warning: Could not get next build number from Jenkins"
    BUILD_NUMBER="manual-$(date +%s)"
fi

# Create build directory on maybelle
ssh root@maybelle.cryptograss.live "mkdir -p /var/jenkins_home/jobs/deploy-hunter/builds/$BUILD_NUMBER"

# Upload log file
scp "$LOGFILE" root@maybelle.cryptograss.live:/var/jenkins_home/jobs/deploy-hunter/builds/$BUILD_NUMBER/log

# Create build metadata
ssh root@maybelle.cryptograss.live "cat > /var/jenkins_home/jobs/deploy-hunter/builds/$BUILD_NUMBER/build.xml" << BUILDXML
<?xml version='1.1' encoding='UTF-8'?>
<build>
  <actions/>
  <queueId>-1</queueId>
  <timestamp>$(date +%s)000</timestamp>
  <startTime>$(date +%s)000</startTime>
  <result>$DEPLOY_STATUS</result>
  <duration>0</duration>
  <charset>UTF-8</charset>
  <keepLog>false</keepLog>
  <builtOn></builtOn>
  <workspace>/external</workspace>
  <hudsonVersion>2.528.2</hudsonVersion>
  <scm class="hudson.scm.NullChangeLogParser"/>
  <culprits class="java.util.Collections\$UnmodifiableSet"/>
</build>
BUILDXML

# Fix permissions
ssh root@maybelle.cryptograss.live "chown -R 1000:1000 /var/jenkins_home/jobs/deploy-hunter/builds/$BUILD_NUMBER"

# Clean up local log
rm -f "$LOGFILE"

# Clean up SSH agent
echo ""
echo "Cleaning up SSH agent..."
ssh-agent -k > /dev/null

echo ""
echo "=== Deployment $DEPLOY_RESULT ==="
echo "View logs at: https://maybelle.cryptograss.live/job/deploy-hunter/$BUILD_NUMBER/"
