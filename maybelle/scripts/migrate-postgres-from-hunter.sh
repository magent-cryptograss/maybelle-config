#!/bin/bash
# Migrate postgres data from hunter to maybelle with secrets filtering
#
# This script:
#   1. Dumps the magenta_memory database from hunter
#   2. Runs the secrets filter to redact vault secrets
#   3. Copies the scrubbed dump to maybelle
#   4. Optionally triggers ansible to import it
#
# Prerequisites:
#   - SSH access to both hunter and maybelle
#   - ANSIBLE_VAULT_PASSWORD env var set (for secrets filter)
#   - Python with pyyaml installed locally
#
# Usage:
#   ./maybelle/scripts/migrate-postgres-from-hunter.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config.yml"

# Parse config.yml
get_config() {
    grep "^$1:" "$CONFIG_FILE" | sed 's/^[^:]*: *"\?\([^"]*\)"\?/\1/'
}

MAYBELLE_HOST=$(get_config host)
MAYBELLE_USER=$(get_config user)
HUNTER_HOST="hunter.cryptograss.live"
HUNTER_USER="root"

DUMP_FILE="/tmp/magenta_memory_dump.sql"
FILTERED_DUMP_FILE="/tmp/magenta_memory_dump_filtered.sql"
VAULT_FILE="$REPO_DIR/secrets/vault.yml"

echo "=== Migrate Postgres from Hunter to Maybelle ==="
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

# Step 1: Dump from hunter
echo "Step 1: Dumping database from hunter..."
ssh "${HUNTER_USER}@${HUNTER_HOST}" "docker exec magenta-postgres pg_dump -U magent magenta_memory" > "$DUMP_FILE"
echo "  Dumped to $DUMP_FILE ($(wc -c < "$DUMP_FILE") bytes)"

# Step 2: Run secrets filter
echo ""
echo "Step 2: Filtering secrets from dump..."

# Create a Python script to run the filter
FILTER_SCRIPT=$(cat <<'PYTHON_EOF'
import sys
import os
import yaml
import subprocess

# Read vault password from env
vault_password = os.environ.get('ANSIBLE_VAULT_PASSWORD')
vault_file = sys.argv[1]
input_file = sys.argv[2]
output_file = sys.argv[3]

# Decrypt vault
result = subprocess.run(
    ['ansible-vault', 'view', vault_file],
    input=vault_password.encode(),
    capture_output=True,
    check=True
)
vault_data = yaml.safe_load(result.stdout)

# Extract all secret values (any string value in the vault)
secrets = []
def extract_secrets(data, prefix=""):
    if isinstance(data, dict):
        for key, value in data.items():
            extract_secrets(value, f"{prefix}{key}.")
    elif isinstance(data, str) and len(data) > 3:  # Skip very short values
        secrets.append(data)
    elif isinstance(data, list):
        for item in data:
            extract_secrets(item, prefix)

extract_secrets(vault_data)
print(f"  Loaded {len(secrets)} secret values to filter", file=sys.stderr)

# Read and filter the dump
with open(input_file, 'r') as f:
    content = f.read()

original_size = len(content)
redaction_count = 0

for secret in secrets:
    if secret in content:
        count = content.count(secret)
        redaction_count += count
        content = content.replace(secret, '[REDACTED:VAULT_SECRET]')

with open(output_file, 'w') as f:
    f.write(content)

print(f"  Redacted {redaction_count} occurrences of secrets", file=sys.stderr)
print(f"  Output: {output_file} ({len(content)} bytes)", file=sys.stderr)
PYTHON_EOF
)

echo "$FILTER_SCRIPT" | python3 - "$VAULT_FILE" "$DUMP_FILE" "$FILTERED_DUMP_FILE"

# Step 3: Compress and copy to maybelle's backup directory
echo ""
echo "Step 3: Compressing and copying to maybelle backup directory..."
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILENAME="magenta_memory_${TIMESTAMP}.sql.gz"

gzip -c "$FILTERED_DUMP_FILE" > "/tmp/${BACKUP_FILENAME}"
scp "/tmp/${BACKUP_FILENAME}" "${MAYBELLE_USER}@${MAYBELLE_HOST}:/mnt/persist/magenta/backups/${BACKUP_FILENAME}"
echo "  Copied to ${MAYBELLE_HOST}:/mnt/persist/magenta/backups/${BACKUP_FILENAME}"

# Cleanup local temp files
rm -f "$DUMP_FILE" "$FILTERED_DUMP_FILE" "/tmp/${BACKUP_FILENAME}"

echo ""
echo "=== Migration complete ==="
echo ""
echo "The filtered backup is now at /mnt/persist/magenta/backups/${BACKUP_FILENAME}"
echo ""
echo "To restore:"
echo "  1. If database is empty: Run chapter-1 (auto-restores from latest backup)"
echo "  2. Manual restore:"
echo "     ssh ${MAYBELLE_USER}@${MAYBELLE_HOST}"
echo "     gunzip -c /mnt/persist/magenta/backups/${BACKUP_FILENAME} | docker exec -i magenta-postgres psql -U magent -d magenta_memory"
echo ""
