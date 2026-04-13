#!/usr/bin/env python3
"""Check Blue Railroad chain data against wiki Release pages.

Run inside the Jenkins container on maybelle:
  docker exec jenkins python3 /path/to/audit-chain-data.py
"""

import json
import urllib.request

CHAIN_DATA = "/var/jenkins_home/shared/chain_data/chainData.json"
WIKI_API = "https://pickipedia.xyz/api.php"

SONG_MAP = {
    '5': ('Blue Railroad Train', 'Squats'),
    '6': ('Nine Pound Hammer', 'Pushups'),
    '7': ('Blue Railroad Train', 'Squats'),
    '10': ('Ginseng Sullivan', 'Army Crawls'),
}

BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def video_hash_to_cidv0(video_hash):
    if not video_hash:
        return None
    hex_str = video_hash[2:] if video_hash.startswith('0x') else video_hash
    if not hex_str or hex_str == '0' * 64:
        return None
    try:
        digest = bytes.fromhex(hex_str)
        multihash = bytes([0x12, 0x20]) + digest
        leading_zeros = sum(1 for b in multihash if b == 0)
        num = int.from_bytes(multihash, 'big')
        result = []
        while num > 0:
            num, remainder = divmod(num, 58)
            result.append(BASE58_ALPHABET[remainder])
        return '1' * leading_zeros + ''.join(reversed(result))
    except Exception:
        return None


def main():
    # Load chain data
    with open(CHAIN_DATA) as f:
        d = json.load(f)

    # Group tokens by CID
    cid_tokens = {}  # cid -> list of (token_id, song_id)
    for key in ['blueRailroads', 'blueRailroadV2s']:
        for tid, t in d.get(key, {}).items():
            song_id = str(t.get('songId', ''))
            uri = t.get('uri', '')
            video_hash = t.get('videoHash', '')

            cid = None
            if video_hash:
                cid = video_hash_to_cidv0(video_hash)
            elif uri and uri.startswith('ipfs://'):
                cid = uri[7:]

            if cid:
                cid_tokens.setdefault(cid, []).append((int(tid), song_id))

    # Fetch releases from wiki
    url = f"{WIKI_API}?action=releaselist&format=json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        releases = json.loads(resp.read().decode())

    release_by_cid = {}
    for r in releases.get('releases', []):
        cid = r.get('ipfs_cid') or r.get('page_title', '')
        release_by_cid[cid.lower()] = r

    # Check each CID group
    issues = 0
    for cid in sorted(cid_tokens, key=lambda c: min(tid for tid, _ in cid_tokens[c])):
        token_list = cid_tokens[cid]
        token_ids = sorted(tid for tid, _ in token_list)
        song_id = token_list[0][1]  # all tokens for same CID should have same song

        song_exercise = SONG_MAP.get(song_id)
        if song_exercise:
            song_name, exercise = song_exercise
            id_str = ', '.join(f'#{tid}' for tid in token_ids)
            expected_title = f"{song_name} ({exercise}) {id_str}"
        else:
            expected_title = None

        rel = release_by_cid.get(cid.lower())
        if not rel:
            id_str = ', '.join(f'#{tid}' for tid in token_ids)
            print(f"  NO RELEASE: Tokens {id_str} CID={cid[:16]}...")
            issues += 1
        elif expected_title and rel.get('title') != expected_title:
            print(f"  TITLE MISMATCH: Tokens {', '.join(f'#{t}' for t in token_ids)}")
            print(f"    expected: {expected_title}")
            print(f"    actual:   {rel.get('title', '(none)')}")
            issues += 1

    if issues == 0:
        print("  All tokens have matching Release pages with correct titles")


if __name__ == '__main__':
    main()
