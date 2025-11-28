#!/bin/bash
# Migrate postgres data from hunter to maybelle with secrets filtering
#
# Uses SSH agent forwarding (like deploy-hunter-remote.py) so your local
# SSH key is used to reach hunter through maybelle.
#
# Data flows: hunter → maybelle (over private network, not through your laptop)
#
# Prerequisites:
#   - SSH key for hunter (~/.ssh/id_ed25519_hunter)
#   - ANSIBLE_VAULT_PASSWORD_FILE or ANSIBLE_VAULT_PASSWORD set
#   - Vault file accessible locally (for decrypting secrets list)
#
# Usage:
#   ./maybelle/scripts/migrate-postgres-from-hunter.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config.yml"
VAULT_FILE="$REPO_DIR/secrets/vault.yml"
SSH_KEY="$HOME/.ssh/id_ed25519_hunter"

# Parse config.yml
get_config() {
    grep "^$1:" "$CONFIG_FILE" | sed 's/^[^:]*: *"\?\([^"]*\)"\?/\1/'
}

MAYBELLE_HOST=$(get_config host)
MAYBELLE_USER=$(get_config user)

echo "=== Migrate Postgres from Hunter to Maybelle ==="
echo ""
echo "Data flow: hunter → maybelle (via private network)"
echo ""

# Get vault password from file or env var
if [ -n "$ANSIBLE_VAULT_PASSWORD_FILE" ] && [ -f "$ANSIBLE_VAULT_PASSWORD_FILE" ]; then
    ANSIBLE_VAULT_PASSWORD=$(cat "$ANSIBLE_VAULT_PASSWORD_FILE")
    export ANSIBLE_VAULT_PASSWORD
    echo "Using vault password from $ANSIBLE_VAULT_PASSWORD_FILE"
elif [ -z "$ANSIBLE_VAULT_PASSWORD" ]; then
    echo "ERROR: Neither ANSIBLE_VAULT_PASSWORD_FILE nor ANSIBLE_VAULT_PASSWORD is set"
    echo "This is needed to decrypt the vault for secrets filtering"
    exit 1
fi

# Step 1: Set up SSH agent with hunter key
echo "Step 1: Setting up SSH agent..."
eval "$(ssh-agent -s)"
trap 'ssh-agent -k > /dev/null 2>&1' EXIT

if [ -f "$SSH_KEY" ]; then
    ssh-add "$SSH_KEY"
    echo "  Added $SSH_KEY to agent"
else
    echo "ERROR: SSH key not found: $SSH_KEY"
    exit 1
fi

# Step 2: Extract secrets from vault (locally - just the secrets list, small)
echo ""
echo "Step 2: Extracting secrets from vault..."
SECRETS_JSON=$(ansible-vault view "$VAULT_FILE" | python3 -c '
import sys, yaml, json

data = yaml.safe_load(sys.stdin)
secrets = []

def extract(d):
    if isinstance(d, dict):
        for v in d.values():
            extract(v)
    elif isinstance(d, str) and len(d) > 3:
        secrets.append(d)
    elif isinstance(d, list):
        for item in d:
            extract(item)

extract(data)
print(json.dumps(secrets))
')
SECRET_COUNT=$(echo "$SECRETS_JSON" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)))')
echo "  Found $SECRET_COUNT secrets to filter"

# Step 3: Run the migration FROM maybelle (with agent forwarding)
echo ""
echo "Step 3: SSHing to maybelle to run migration..."
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILENAME="magenta_memory_${TIMESTAMP}.sql.gz"

# Escape the secrets JSON for shell
SECRETS_ESCAPED=$(printf '%s' "$SECRETS_JSON" | sed "s/'/'\\\\''/g")

# SSH to maybelle with agent forwarding (-A), which then SSHs to hunter
ssh -A "${MAYBELLE_USER}@${MAYBELLE_HOST}" bash -s "$SECRETS_ESCAPED" "$BACKUP_FILENAME" << 'REMOTE_SCRIPT'
set -e
SECRETS_JSON="$1"
BACKUP_FILENAME="$2"

echo "  Pulling database from hunter and filtering secrets..."

# Create filter script
FILTER_PY=$(cat <<'PYTHON_EOF'
import sys, json
secrets = json.loads(sys.argv[1])
for line in sys.stdin:
    for secret in secrets:
        if secret in line:
            line = line.replace(secret, '[REDACTED:VAULT_SECRET]')
    sys.stdout.write(line)
PYTHON_EOF
)

# Ensure backup directory exists
mkdir -p /mnt/persist/magenta/backups

# Pull from hunter (using forwarded agent), filter, compress, save
ssh root@hunter.cryptograss.live "docker exec magenta-postgres pg_dump -U magent magenta_memory" | \
    python3 -c "$FILTER_PY" "$SECRETS_JSON" | \
    gzip > "/mnt/persist/magenta/backups/${BACKUP_FILENAME}"

# Report size
SIZE=$(stat -c%s "/mnt/persist/magenta/backups/${BACKUP_FILENAME}")
echo "  Saved: /mnt/persist/magenta/backups/${BACKUP_FILENAME} (${SIZE} bytes)"
REMOTE_SCRIPT

echo ""
echo "=== Migration complete ==="
echo ""
echo "The filtered backup is at: /mnt/persist/magenta/backups/${BACKUP_FILENAME}"
echo ""
echo "To restore:"
echo "  1. If database is empty: Run chapter-1 (auto-restores from latest backup)"
echo "  2. Manual restore on maybelle:"
echo "     gunzip -c /mnt/persist/magenta/backups/${BACKUP_FILENAME} | docker exec -i magenta-postgres psql -U magent -d magenta_memory"
echo ""
