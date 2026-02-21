# Maybelle Config - Infrastructure Notes

## No-Touch Server Policy

**Maybelle and other production servers are no-touch.** Never suggest manual commands to run on production servers. All changes must go through the deployment automation:

1. Make changes in this repo
2. Commit and push to the appropriate branch
3. Merge to `production` on `cryptograss/maybelle-config`
4. Run `./maybelle/scripts/maybelle-chapter-1.sh` from laptop

### Rebuilding Docker Images

When code changes need to be picked up (not just config changes), use the `--rebuild` flag:

```bash
./maybelle/scripts/maybelle-chapter-1.sh --rebuild
```

This passes `rebuild_images=true` to ansible, which runs `docker compose build --no-cache` to bypass Docker's layer caching.

## Deployment Flow

The `maybelle-chapter-1.sh` script:
1. Copies vault password to maybelle
2. Connects via mosh/tmux
3. Clones/updates repo from `origin/production`
4. Runs ansible playbook with vault

## Key Services

- **Pinning Service**: Video transcoding (Coconut.co) and IPFS pinning
- **IPFS Node**: Local kubo node for redundancy alongside Pinata
- **Jenkins**: CI/CD for arthel builds
- **Caddy**: Reverse proxy with automatic SSL

## Repo Structure

- `maybelle/ansible/` - Ansible playbooks and configs
- `maybelle/pinning-service/` - Blue Railroad pinning service code
- `maybelle/scripts/` - Deployment and maintenance scripts
- `maybelle/jobs/` - Jenkins job definitions
- `hunter/` - Hunter VPS (development containers) config
- `pickipedia-vps/` - PickiPedia MediaWiki server config
