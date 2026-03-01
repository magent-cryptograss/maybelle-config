# Delivery Kid

IPFS and BitTorrent distribution server for Cryptograss music releases.

## Architecture

```
                        ┌─────────────────────┐
                        │     Caddy           │
                        │  (SSL termination)  │
                        └─────────┬───────────┘
                                  │
         ┌────────────────────────┼────────────────────────┐
         │                        │                        │
         ▼                        ▼                        ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Pinning Service │    │   IPFS Gateway  │    │     aria2       │
│   (port 3001)   │    │   (port 8080)   │    │  BitTorrent     │
│                 │    │                 │    │   (port 6881)   │
│ POST /api/pin   │    │ /ipfs/<cid>     │    │                 │
│ POST /api/upload│    │                 │    │ Seeds torrents  │
│ GET /api/pins   │    │                 │    │ created by      │
│ POST /api/torrent    │                 │    │ delivery-driver │
└────────┬────────┘    └────────┬────────┘    └─────────────────┘
         │                      │
         └──────────┬───────────┘
                    ▼
         ┌─────────────────┐      ┌─────────────────┐
         │   IPFS Node     │      │     Pinata      │
         │  (Kubo daemon)  │─────▶│  (backup pins)  │
         │                 │      │                 │
         └─────────────────┘      └─────────────────┘
```

## Deployment

1. Provision a Hetzner VPS (CX22 or larger)
2. Copy inventory example:
   ```bash
   cp ansible/inventory.yml.example ansible/inventory.yml
   ```
3. Edit inventory with your server IP and domains
4. Add Pinata JWT to `secrets/vault.yml` (optional)
5. Run playbook:
   ```bash
   ansible-playbook -i ansible/inventory.yml ansible/playbook.yml
   ```

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/pin` | Pin an existing CID |
| `POST /api/upload` | Upload and pin a file |
| `POST /api/upload-directory` | Upload and pin a directory (tar) |
| `POST /api/torrent` | Add a torrent for seeding |
| `GET /api/pins` | List all pinned content |
| `GET /api/health` | Health check |

## Related

- **delivery-driver** - CLI tool for creating releases (runs on your laptop)
- **maybelle** - CI server that deploys this
