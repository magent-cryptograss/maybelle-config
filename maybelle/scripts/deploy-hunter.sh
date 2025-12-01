#!/bin/bash
#
# Deploy hunter from maybelle
# This script runs ON maybelle and handles the full deploy + Jenkins reporting
#
# The vault password is passed via stdin from the caller's laptop.
# The Jenkins reporter password is read from /root/.jenkins_reporter_password
#
# Usage from laptop:
#   echo "$ANSIBLE_VAULT_PASSWORD" | ssh root@maybelle.cryptograss.live /mnt/persist/maybelle-config/maybelle/scripts/deploy-hunter.sh [username]
#

set -e

DEPLOY_USER="${1:-remote}"
REPO_DIR="/mnt/persist/maybelle-config"
JENKINS_REPORTER_FILE="/root/.jenkins_reporter_password"
LOG_FILE="/tmp/hunter-deploy-$$.log"
VAULT_FILE="/tmp/vault_pass_$$"

echo "============================================================"
echo "DEPLOY HUNTER FROM MAYBELLE"
echo "============================================================"
echo ""
echo "Deploy user: $DEPLOY_USER"
echo ""

# Read vault password from stdin
echo "Reading vault password from stdin..."
read -r VAULT_PASSWORD
if [ -z "$VAULT_PASSWORD" ]; then
    echo "ERROR: No vault password provided on stdin"
    exit 1
fi

# Write to temp file
echo "$VAULT_PASSWORD" > "$VAULT_FILE"
chmod 600 "$VAULT_FILE"
echo "✓ Vault password received"

# Cleanup function
cleanup() {
    rm -f "$VAULT_FILE" "$LOG_FILE"
}
trap cleanup EXIT

# Get Jenkins reporter password
if [ -f "$JENKINS_REPORTER_FILE" ]; then
    REPORTER_PASS=$(cat "$JENKINS_REPORTER_FILE")
else
    echo "⚠ No Jenkins reporter password found, will skip reporting"
    REPORTER_PASS=""
fi

# Update repository
echo ""
echo "Updating maybelle-config repository..."
cd "$REPO_DIR"
git fetch origin

# Hard reset to production (handles force pushes/rebases)
git checkout production 2>/dev/null || git checkout -b production origin/production
git reset --hard origin/production

# Check that production is not behind main
if ! git merge-base --is-ancestor origin/main origin/production; then
    echo "ERROR: production branch is behind main"
    echo "Please update production to include latest main changes"
    exit 1
fi
echo "✓ Repository updated"

# Run ansible
echo ""
echo "============================================================"
echo "RUNNING ANSIBLE PLAYBOOK"
echo "============================================================"
echo ""

START_TIME=$(date +%s)

cd "$REPO_DIR/hunter/ansible"
if ansible-playbook --vault-password-file="$VAULT_FILE" -i inventory.yml playbook.yml 2>&1 | tee "$LOG_FILE"; then
    DEPLOY_STATUS="success"
    EXIT_CODE=0
    echo ""
    echo "✓ Deployment complete"
else
    DEPLOY_STATUS="failure"
    EXIT_CODE=1
    echo ""
    echo "✗ Deployment failed"
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

echo ""
echo "============================================================"

# Report to Jenkins
if [ -n "$REPORTER_PASS" ]; then
    echo "Reporting to Jenkins..."

    # Read log, truncate if huge
    LOG_CONTENT=$(tail -c 50000 "$LOG_FILE" 2>/dev/null || echo "(no log)")

    AUTH=$(echo -n "reporter:$REPORTER_PASS" | base64)

    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST \
        -H "Authorization: Basic $AUTH" \
        --data-urlencode "DEPLOY_USER=$DEPLOY_USER" \
        --data-urlencode "DEPLOY_STATUS=$DEPLOY_STATUS" \
        --data-urlencode "DEPLOY_DURATION=$DURATION" \
        --data-urlencode "DEPLOY_LOG=$LOG_CONTENT" \
        "http://localhost:8080/job/deploy-hunter/buildWithParameters" \
        2>/dev/null || echo "000")

    if [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "200" ]; then
        echo "✓ Reported to Jenkins"
    else
        echo "⚠ Could not report to Jenkins (HTTP $HTTP_CODE)"
    fi
fi

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "✓ SUCCESS"
else
    echo "✗ FAILED"
fi

exit $EXIT_CODE
