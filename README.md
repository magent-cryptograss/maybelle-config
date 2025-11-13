# maybelle-config

Deployment configuration for cryptograss infrastructure. Maybelle is the control plane for deploying and managing hunter (development VPS) and maybelle itself (CI/CD server).

## Structure

- **secrets/** - Single unified vault for all secrets (gitignored plaintext, encrypted for production)
- **hunter/** - Deployment configuration for hunter VPS (multi-user magenta development environment)
- **maybelle/** - Deployment configuration for maybelle server (Jenkins CI/CD)

## Deployment

All deployments run from maybelle server.

### Deploy to hunter VPS
```bash
cd ~/maybelle-config/hunter
./deploy.sh
```

### Update maybelle itself
```bash
cd ~/maybelle-config/maybelle
ansible-playbook -i localhost, ansible/maybelle.yml --ask-vault-pass
```

## Secrets Management

Secrets are stored in `secrets/vault-plaintext.yml` (gitignored). Required variables:

**Hunter secrets:**
- `memory_lane_postgres_password` - PostgreSQL password
- `justin_vscode_password` - Justin's code-server password
- `rj_vscode_password` - R.J.'s code-server password

**Maybelle secrets:**
- `maybelle_env` - Jenkins environment variables (multiline string)
- `jenkins_admin_password` - Jenkins admin login
- `github_token` - GitHub API token

**Note:** TLS certificates are handled automatically by Caddy via Let's Encrypt.

For production, encrypt with:
```bash
ansible-vault encrypt secrets/vault-plaintext.yml -o secrets/vault.yml
```

To edit encrypted vault:
```bash
ansible-vault edit secrets/vault.yml
```

To decrypt for local development:
```bash
ansible-vault decrypt secrets/vault.yml -o secrets/vault-plaintext.yml
```

## Architecture

**Maybelle** (maybelle.cryptograss.live):
- Jenkins CI/CD in Docker
- Nginx reverse proxy with SSL
- Deploys arthel (justinholmes.com)
- Deploys to hunter via SSH

**Hunter** (hunter.cryptograss.live):
- Multi-user development containers
- PostgreSQL for magenta memory
- Shared services (MCP server, Memory Lane, watcher)
- Per-user isolation with dedicated ports and SSH

## Repository Migration

This repo consolidates deployment configuration from:
- `magenta/hunter/` → `hunter/`
- `arthel/deployment/` → `maybelle/`
- Scattered secrets → `secrets/vault.yml`
