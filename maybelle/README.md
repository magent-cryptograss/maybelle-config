# Maybelle Deployment

Infrastructure for deploying Jenkins CI/CD and Memory Lane services to maybelle server.

## Architecture

Maybelle uses a Hetzner persistent volume mounted at `/mnt/persist` (symlinked from Hetzner's auto-mount path). The volume contains:
- `maybelle-config/` - This repository (cloned once, updated via git pull)
- `.vault-password` - Ansible vault password (manual one-time setup)
- `backups/` - Database backups and other persistent data (future)

This allows maybelle to be wiped and rebuilt quickly - just reattach the volume and run the chapter scripts.

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

### Chapter 1: Bootstrap
```bash
./maybelle/scripts/maybelle-chapter-1.sh
```
Copies vault password to persistent volume, connects via mosh/tmux, creates symlink, installs dependencies, clones repo, runs ansible.

## Regular Deployments

### Chapter 2: Deploy
```bash
./maybelle/scripts/maybelle-chapter-2.sh
```
Connects via mosh/tmux, pulls latest, runs ansible.

## Services

- **Jenkins**: https://maybelle.cryptograss.live
- **Memory Lane**: https://memory-lane.maybelle.cryptograss.live
- **MCP Server**: 10.0.0.2:8000 (private network, accessible from hunter)

## Network

Maybelle is on Hetzner private network:
- maybelle: 10.0.0.2
- hunter: 10.0.0.3

Memory Lane and MCP server connect to postgres on hunter (10.0.0.3:5432).

## Configuration

- `config.yml` - Volume ID, host, vault password file location
- `ansible/maybelle.yml` - Main playbook
- `jenkins-docker/Dockerfile` - Custom Jenkins image
- `configs/jenkins.yml` - Jenkins CasC configuration
- `jobs/*.groovy` - Jenkins job definitions
- `scripts/` - Chapter scripts for deployment

## Secrets

Vault password stored at `/mnt/persist/.vault-password` on the persistent volume.
