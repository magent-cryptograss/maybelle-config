#!/bin/bash
# Refresh hunter's SSH host key in maybelle's known_hosts
# Run this after wiping/rebuilding hunter

set -e

echo "Refreshing hunter's SSH host key on maybelle..."
echo ""

# Remove old key and add new one
ssh root@maybelle.cryptograss.live '
    ssh-keygen -R hunter.cryptograss.live 2>/dev/null || true
    ssh-keygen -R hunter.cryptograss.live,5.78.83.4 2>/dev/null || true
    ssh-keyscan -H hunter.cryptograss.live >> /root/.ssh/known_hosts

    # Also update for jenkins user
    docker exec jenkins bash -c "
        ssh-keygen -R hunter.cryptograss.live 2>/dev/null || true
        ssh-keygen -R hunter.cryptograss.live,5.78.83.4 2>/dev/null || true
        ssh-keyscan -H hunter.cryptograss.live >> /var/jenkins_home/.ssh/known_hosts
        chown 1000:1000 /var/jenkins_home/.ssh/known_hosts
    "
'

echo ""
echo "✓ Hunter host key refreshed on maybelle (root and jenkins user)"
