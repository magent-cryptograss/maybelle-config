#!/bin/bash
# Chapter 2: Pull latest and run ansible (regular deploys)
# Run from your laptop (requires repo checkout)
#
# This script:
#   - Connects via mosh/tmux
#   - Pulls latest from production branch
#   - Runs the ansible playbook
#
# Prerequisites:
#   - maybelle-chapter-1.sh has been run at least once
#
# Usage:
#   ./maybelle/scripts/maybelle-chapter-2.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config.yml"

# Parse config.yml
get_config() {
    grep "^$1:" "$CONFIG_FILE" | sed 's/^[^:]*: *"\?\([^"]*\)"\?/\1/'
}

MOUNT_POINT=$(get_config mount_point)
HOST=$(get_config host)
USER=$(get_config user)
VAULT_PASSWORD_FILE=$(get_config vault_password_file)

REPO_DIR="${MOUNT_POINT}/maybelle-config"
SESSION_NAME="chapter-2"

echo "=== Maybelle Chapter 2 ==="
echo "Host: ${USER}@${HOST}"
echo ""
echo "Connecting via mosh..."
echo ""

# Commands to run on maybelle
REMOTE_SCRIPT=$(cat <<'OUTER_EOF'
set -e
trap 'echo ""; echo "ERROR: Script failed at line $LINENO. Press enter to exit."; read' ERR

REPO_DIR="__REPO_DIR__"
VAULT_PASSWORD_FILE="__VAULT_PASSWORD_FILE__"

# Check repo exists
if [ ! -d "$REPO_DIR/.git" ]; then
    echo "ERROR: Repository not found at $REPO_DIR"
    echo "Run maybelle-chapter-1.sh first"
    exit 1
fi

# Check for vault password file
if [ ! -f "$VAULT_PASSWORD_FILE" ]; then
    echo "ERROR: Vault password file not found at $VAULT_PASSWORD_FILE"
    exit 1
fi

cd "$REPO_DIR"

echo "Pulling latest from production..."
git fetch origin production
git reset --hard origin/production

echo ""
echo "Running ansible playbook..."
cd maybelle/ansible
ansible-playbook -i localhost, maybelle.yml --vault-password-file "$VAULT_PASSWORD_FILE"

echo ""
echo "=== Chapter 2 complete ==="
echo "Press enter to exit"
read
OUTER_EOF
)

# Substitute variables
REMOTE_SCRIPT="${REMOTE_SCRIPT//__REPO_DIR__/$REPO_DIR}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__VAULT_PASSWORD_FILE__/$VAULT_PASSWORD_FILE}"

# Copy script to maybelle and run it via mosh/tmux
REMOTE_SCRIPT_PATH="/tmp/chapter-2-script.sh"
echo "$REMOTE_SCRIPT" | ssh "${USER}@${HOST}" "cat > ${REMOTE_SCRIPT_PATH} && chmod +x ${REMOTE_SCRIPT_PATH}"

# Connect via mosh, create tmux session, run script
mosh "${USER}@${HOST}" -- tmux new-session -A -s "$SESSION_NAME" "${REMOTE_SCRIPT_PATH}"
