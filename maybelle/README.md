# Maybelle Deployment

Infrastructure for deploying Jenkins CI/CD and Memory Lane services to maybelle server.

## Architecture

Maybelle uses a Hetzner persistent volume for all stateful data. The volume survives instance rebuilds.

### Storage Layout

```
/mnt/HC_Volume_104062902/    # Hetzner auto-mount path
  └── (symlinked to /mnt/persist)

/mnt/persist/
  ├── .vault-password         # Ansible vault password (manual one-time setup)
  ├── maybelle-config/        # This repo (cloned once, updated via git pull)
  ├── magenta/
  │   ├── postgres-data/      # PostgreSQL data directory
  │   └── backups/            # Database backups (daily cron + migration imports)
  └── jenkins/
      └── jenkins_home/       # Jenkins home directory
```

This allows maybelle to be wiped and rebuilt quickly - just reattach the volume and run the chapter scripts.

### Services

- **Jenkins**: https://maybelle.cryptograss.live - CI/CD for cryptograss projects
- **Memory Lane**: https://memory-lane.maybelle.cryptograss.live - Web UI for magent conversation history
- **MCP Server**: http://10.0.0.2:8000 - Memory MCP server (private network, used by hunter containers)
- **PostgreSQL**: magenta-postgres container on memory-lane-net network

### Network

Hetzner private network:
- maybelle: 10.0.0.2
- hunter: 10.0.0.3

Hunter's watcher POSTs to maybelle's ingest endpoint. Hunter containers use maybelle's MCP server.

## Initial Setup (Fresh Instance)

### Prerequisites
1. Hetzner volume created and attached to maybelle (auto-mounted at `/mnt/HC_Volume_<id>`)
2. SSH access configured for `root@maybelle.cryptograss.live`
3. This repo checked out on your laptop
4. `ANSIBLE_VAULT_PASSWORD_FILE` env var pointing to your local vault password file

### Chapter 0: Install mosh and tmux
```bash
./maybelle/scripts/maybelle-chapter-0.sh
```
Quick SSH to install mosh and tmux.

### Chapter 1: Bootstrap (idempotent)
```bash
./maybelle/scripts/maybelle-chapter-1.sh
```
Copies vault password to persistent volume, connects via mosh/tmux, creates symlink, installs dependencies, clones repo, runs ansible. Safe to re-run.

## Database Backup & Restore

### Automatic Backups
Daily cron at 3am creates `/mnt/persist/magenta/backups/magenta_memory_YYYYMMDD_HHMMSS.sql.gz`. 7-day retention.

### Restore on Fresh Deploy
Chapter 1 automatically restores from the latest backup in `/mnt/persist/magenta/backups/` if the database is empty.

### Migration from Hunter
```bash
./maybelle/scripts/migrate-postgres-from-hunter.sh
```
Dumps hunter's database, filters vault secrets, places in backups directory. Then run chapter 1 to restore.

## Configuration

- `config.yml` - Volume ID, host, vault password file location
- `ansible/maybelle.yml` - Main playbook
- `jenkins-docker/Dockerfile` - Custom Jenkins image
- `configs/jenkins.yml` - Jenkins CasC configuration
- `jobs/*.groovy` - Jenkins job definitions
- `scripts/` - Chapter scripts for deployment

## Secrets

Vault password stored at `/mnt/persist/.vault-password` on the persistent volume.
