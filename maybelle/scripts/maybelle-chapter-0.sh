#!/bin/bash
# Chapter 0: Install mosh and tmux
# Run from your laptop (requires repo checkout)
#
# This script:
#   - SSHs to maybelle
#   - Installs mosh and tmux
#
# Usage:
#   ./maybelle/scripts/maybelle-chapter-0.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config.yml"

# Parse config.yml
get_config() {
    grep "^$1:" "$CONFIG_FILE" | sed 's/^[^:]*: *"\?\([^"]*\)"\?/\1/'
}

HOST=$(get_config host)
USER=$(get_config user)

echo "=== Maybelle Chapter 0 ==="
echo "Host: ${USER}@${HOST}"
echo ""

ssh "${USER}@${HOST}" "apt-get update -qq && apt-get install -y -qq mosh tmux"

echo ""
echo "=== Chapter 0 complete ==="
echo ""
echo "Next step:"
echo "  ./maybelle/scripts/maybelle-chapter-1.sh"
