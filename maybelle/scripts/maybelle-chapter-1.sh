#!/bin/bash
# Chapter 1: Create symlink, clone repo, run ansible
# Run from your laptop (requires repo checkout)
#
# This script:
#   - Copies vault password from local env var to maybelle
#   - Connects via mosh/tmux
#   - Creates symlink from /mnt/persist to Hetzner volume
#   - Installs git and ansible
#   - Clones maybelle-config repo to the persistent volume
#   - Runs the ansible playbook
#
# Prerequisites:
#   - maybelle-chapter-0.sh has been run
#   - ANSIBLE_VAULT_PASSWORD_FILE env var set on your laptop
#
# Usage:
#   ./maybelle/scripts/maybelle-chapter-1.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config.yml"

# Parse config.yml
get_config() {
    grep "^$1:" "$CONFIG_FILE" | sed 's/^[^:]*: *"\?\([^"]*\)"\?/\1/'
}

VOLUME_ID=$(get_config hetzner_volume_id)
MOUNT_POINT=$(get_config mount_point)
HOST=$(get_config host)
USER=$(get_config user)
VAULT_PASSWORD_FILE=$(get_config vault_password_file)

HETZNER_VOLUME_PATH="/mnt/HC_Volume_${VOLUME_ID}"
REPO_DIR="${MOUNT_POINT}/maybelle-config"
SESSION_NAME="chapter-1"

VOLUME_DEVICE="/dev/disk/by-id/scsi-0HC_Volume_${VOLUME_ID}"

echo "=== Maybelle Chapter 1 ==="
echo "Host: ${USER}@${HOST}"
echo "Volume: ${HETZNER_VOLUME_PATH} -> ${MOUNT_POINT}"
echo ""

# Check local vault password file
if [ -z "$ANSIBLE_VAULT_PASSWORD_FILE" ]; then
    echo "ERROR: ANSIBLE_VAULT_PASSWORD_FILE env var not set"
    exit 1
fi

if [ ! -f "$ANSIBLE_VAULT_PASSWORD_FILE" ]; then
    echo "ERROR: Vault password file not found at $ANSIBLE_VAULT_PASSWORD_FILE"
    exit 1
fi

# Mount volume if needed (before copying files)
echo "Ensuring volume is mounted..."
ssh "${USER}@${HOST}" "mkdir -p ${HETZNER_VOLUME_PATH} && (mountpoint -q ${HETZNER_VOLUME_PATH} || mount ${VOLUME_DEVICE} ${HETZNER_VOLUME_PATH})"

echo "Copying vault password to maybelle..."
scp "$ANSIBLE_VAULT_PASSWORD_FILE" "${USER}@${HOST}:${HETZNER_VOLUME_PATH}/.vault-password"
ssh "${USER}@${HOST}" "chmod 600 ${HETZNER_VOLUME_PATH}/.vault-password"

# Extract secrets locally and copy only the JSON list to maybelle
# This keeps the vault password off maybelle for the scrubber
echo "Extracting secrets for scrubber..."
SECRETS_JSON=$(ansible-vault view "${SCRIPT_DIR}/../../secrets/vault.yml" --vault-password-file "$ANSIBLE_VAULT_PASSWORD_FILE" | python3 -c "
import sys, yaml, json
data = yaml.safe_load(sys.stdin)
secrets = [v for v in data.values() if isinstance(v, str) and len(v) > 4]
print(json.dumps(secrets))
")

# Create scrubber secrets directory and copy secrets
ssh "${USER}@${HOST}" "mkdir -p ${HETZNER_VOLUME_PATH}/scrubber-secrets && chmod 700 ${HETZNER_VOLUME_PATH}/scrubber-secrets"
echo "$SECRETS_JSON" | ssh "${USER}@${HOST}" "cat > ${HETZNER_VOLUME_PATH}/scrubber-secrets/secrets.json && chmod 600 ${HETZNER_VOLUME_PATH}/scrubber-secrets/secrets.json"
echo "Extracted $(echo "$SECRETS_JSON" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))') secrets for scrubber"

echo ""
echo "Connecting via mosh..."
echo ""

# Commands to run on maybelle
REMOTE_SCRIPT=$(cat <<'OUTER_EOF'
set -e
trap 'echo ""; echo "ERROR: Script failed at line $LINENO. Press enter to exit."; read' ERR

HETZNER_VOLUME_PATH="__HETZNER_VOLUME_PATH__"
MOUNT_POINT="__MOUNT_POINT__"
REPO_DIR="__REPO_DIR__"
VAULT_PASSWORD_FILE="__VAULT_PASSWORD_FILE__"
VOLUME_DEVICE="__VOLUME_DEVICE__"

# Mount volume if not already mounted
echo "Checking Hetzner volume..."
mkdir -p "$HETZNER_VOLUME_PATH"
if mountpoint -q "$HETZNER_VOLUME_PATH"; then
    echo "Volume already mounted at $HETZNER_VOLUME_PATH"
else
    echo "Mounting volume..."
    mount "$VOLUME_DEVICE" "$HETZNER_VOLUME_PATH"
fi

# Add to fstab for persistence across reboots
if ! grep -q "$HETZNER_VOLUME_PATH" /etc/fstab; then
    echo "Adding mount to fstab..."
    echo "$VOLUME_DEVICE $HETZNER_VOLUME_PATH ext4 defaults 0 2" >> /etc/fstab
fi

echo "Creating symlink..."
if [ -L "$MOUNT_POINT" ]; then
    echo "Symlink $MOUNT_POINT already exists"
elif [ -e "$MOUNT_POINT" ]; then
    echo "ERROR: $MOUNT_POINT exists but is not a symlink"
    exit 1
else
    ln -s "$HETZNER_VOLUME_PATH" "$MOUNT_POINT"
    echo "Created $MOUNT_POINT -> $HETZNER_VOLUME_PATH"
fi

echo ""
echo "Installing git and ansible..."
apt-get update -qq
apt-get install -y -qq git ansible

# Clone or update the repo
if [ -d "$REPO_DIR/.git" ]; then
    echo "Updating maybelle-config repository..."
    cd "$REPO_DIR"
    git fetch origin production
    git reset --hard origin/production
else
    echo "Cloning maybelle-config repository..."
    git clone --depth 1 --branch production \
        https://github.com/cryptograss/maybelle-config.git "$REPO_DIR"
fi

echo ""
echo "Running ansible playbook..."
cd "$REPO_DIR/maybelle/ansible"
ansible-playbook -i localhost, maybelle.yml --vault-password-file "$VAULT_PASSWORD_FILE"

echo ""
echo "=== Chapter 1 complete ==="
echo "Press enter to exit"
read
OUTER_EOF
)

# Substitute variables
REMOTE_SCRIPT="${REMOTE_SCRIPT//__HETZNER_VOLUME_PATH__/$HETZNER_VOLUME_PATH}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__MOUNT_POINT__/$MOUNT_POINT}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__REPO_DIR__/$REPO_DIR}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__VAULT_PASSWORD_FILE__/$VAULT_PASSWORD_FILE}"
REMOTE_SCRIPT="${REMOTE_SCRIPT//__VOLUME_DEVICE__/$VOLUME_DEVICE}"

# Copy script to maybelle and run it via mosh/tmux
REMOTE_SCRIPT_PATH="/tmp/chapter-1-script.sh"
echo "$REMOTE_SCRIPT" | ssh "${USER}@${HOST}" "cat > ${REMOTE_SCRIPT_PATH} && chmod +x ${REMOTE_SCRIPT_PATH}"

# Connect via mosh, create tmux session, run script
mosh "${USER}@${HOST}" -- tmux new-session -A -s "$SESSION_NAME" "${REMOTE_SCRIPT_PATH}"
