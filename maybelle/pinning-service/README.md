# Blue Railroad Pinning Service

Downloads videos from Instagram/YouTube via yt-dlp and pins them to IPFS (both Pinata cloud and local node).

## Authentication

Uses wallet-based authentication. Callers must:
1. Sign a message: `Authorize Blue Railroad pinning\nTimestamp: {unix_timestamp_ms}`
2. Include headers: `X-Signature` and `X-Timestamp`
3. Signature must be from an authorized wallet (configured via `AUTHORIZED_WALLETS` env var)
4. Timestamp must be within 5 minutes of server time

This ensures only wallet owners can authorize pinning operations - no shared secrets required.

## API Endpoints

### POST /pin-from-url
Download video from URL and pin to IPFS.

```bash
# First sign the message with your wallet, then:
curl -X POST https://pinning.maybelle.cryptograss.live/pin-from-url \
  -H "Content-Type: application/json" \
  -H "X-Signature: 0x..." \
  -H "X-Timestamp: 1704067200000" \
  -d '{"url": "https://www.instagram.com/p/ABC123/"}'
```

Response:
```json
{
  "cid": "QmXyz...",
  "ipfsUri": "ipfs://QmXyz...",
  "gatewayUrl": "https://gateway.pinata.cloud/ipfs/QmXyz...",
  "filename": "video.mp4",
  "size": 12345678,
  "locallyPinned": true
}
```

### POST /pin-file
Upload and pin a file directly.

```bash
curl -X POST https://pinning.maybelle.cryptograss.live/pin-file \
  -H "X-Signature: 0x..." \
  -H "X-Timestamp: 1704067200000" \
  -F "file=@video.mp4"
```

### POST /pin-cid
Pin an existing CID to local IPFS node.

```bash
curl -X POST https://pinning.maybelle.cryptograss.live/pin-cid \
  -H "Content-Type: application/json" \
  -H "X-Signature: 0x..." \
  -H "X-Timestamp: 1704067200000" \
  -d '{"cid": "QmXyz..."}'
```

### GET /health
Health check (no auth required).

## Configuration

### Authorized Wallets

Edit `authorized-wallets.json` in this directory to add/remove wallets:

```json
{
  "description": "Wallets authorized to use the Blue Railroad pinning service",
  "wallets": [
    {
      "address": "0x067acE39FbBFd3c3f7ceF9ED77590383345994Fe",
      "note": "Justin's wallet"
    }
  ]
}
```

The ansible playbook reads this file and passes the addresses to the container.

### Vault Variables Required

Add these to `secrets/vault.yml`:

```yaml
# Pinata IPFS pinning service JWT
# Get from https://app.pinata.cloud/keys - create an API key with "write files" permission
pinata_jwt: "your-pinata-jwt-token"
```

## Storage

- IPFS data: `/mnt/persist/ipfs/data` (persistent across deploys)
- Staging: `/mnt/persist/ipfs/staging` (temporary file storage)

## Ports

- 3001: Pinning service API (exposed via Caddy at pinning.maybelle.cryptograss.live)
- 5001: IPFS API (localhost only)
- 4001: IPFS swarm (public, for peering with other nodes)

## Testing

```bash
npm test
```

Runs the auth module tests (signature verification, timestamp validation, wallet authorization).
