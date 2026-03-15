"""Deterministic BitTorrent .torrent file generation.

Creates torrent files from directories with deterministic infohashes:
- Torrent name = IPFS CID (links the two systems)
- Piece length = deterministic function of total file size
- Files sorted alphabetically by path
- No non-deterministic fields in the info dict

Given the same files (fetchable by CID from IPFS), the same infohash
is always produced.
"""

import hashlib
import math
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


# Default public trackers
DEFAULT_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://exodus.desync.com:6969/announce",
]


def _bencode(obj) -> bytes:
    """Bencode a Python object (int, bytes, str, list, dict)."""
    if isinstance(obj, int):
        return b"i" + str(obj).encode() + b"e"
    elif isinstance(obj, bytes):
        return str(len(obj)).encode() + b":" + obj
    elif isinstance(obj, str):
        encoded = obj.encode("utf-8")
        return str(len(encoded)).encode() + b":" + encoded
    elif isinstance(obj, list):
        return b"l" + b"".join(_bencode(item) for item in obj) + b"e"
    elif isinstance(obj, dict):
        # Keys must be sorted bytes
        result = b"d"
        for key in sorted(obj.keys()):
            if isinstance(key, str):
                result += _bencode(key.encode("utf-8") if isinstance(key, str) else key)
            else:
                result += _bencode(key)
            result += _bencode(obj[key])
        return result + b"e"
    else:
        raise TypeError(f"Cannot bencode type: {type(obj)}")


def _deterministic_piece_length(total_size: int) -> int:
    """
    Choose piece length deterministically based on total file size.

    Target ~1000-2000 pieces. Piece length is always a power of 2,
    minimum 256KB (2^18), maximum 16MB (2^24).
    """
    min_piece_length = 256 * 1024       # 256 KB
    max_piece_length = 16 * 1024 * 1024  # 16 MB
    target_pieces = 1500

    if total_size == 0:
        return min_piece_length

    ideal = total_size / target_pieces
    # Round up to next power of 2
    power = max(18, math.ceil(math.log2(max(ideal, 1))))
    piece_length = 2 ** power

    return max(min_piece_length, min(piece_length, max_piece_length))


@dataclass
class TorrentResult:
    success: bool
    infohash: Optional[str] = None
    torrent_path: Optional[Path] = None
    torrent_bytes: Optional[bytes] = None
    piece_length: Optional[int] = None
    total_size: Optional[int] = None
    file_count: Optional[int] = None
    webseeds: Optional[list[str]] = None
    error: Optional[str] = None


def create_torrent(
    directory: Path,
    name: str,
    output_path: Optional[Path] = None,
    trackers: Optional[list[str]] = None,
    webseeds: Optional[list[str]] = None,
    single_file_webseeds: Optional[list[str]] = None,
    comment: Optional[str] = None,
) -> TorrentResult:
    """
    Create a .torrent file from a directory with deterministic infohash.

    The infohash depends only on: file contents, file paths (sorted),
    piece length (deterministic from total size), and name.

    Uses single-file torrent format when the directory contains exactly
    one file (common for video releases). This matters for BEP 19 webseed
    compatibility: single-file torrents fetch the URL directly, while
    multi-file torrents append name/path to the URL.

    Args:
        directory: Path to the directory to torrent
        name: Torrent name
        output_path: Where to write the .torrent file (optional)
        trackers: List of tracker announce URLs (outside info dict, doesn't affect infohash)
        webseeds: List of webseed URLs for multi-file torrents (outside info dict)
        single_file_webseeds: Webseed URLs for single-file torrents (BEP 19 fetches directly)
        comment: Optional comment (outside info dict, doesn't affect infohash)

    Returns:
        TorrentResult with infohash and torrent data
    """
    if not directory.is_dir():
        return TorrentResult(success=False, error=f"Not a directory: {directory}")

    # Collect all files, sorted alphabetically by relative path
    files = []
    for file_path in sorted(directory.rglob("*")):
        if file_path.is_file():
            rel_path = file_path.relative_to(directory)
            size = file_path.stat().st_size
            files.append((rel_path, size, file_path))

    if not files:
        return TorrentResult(success=False, error="No files in directory")

    is_single_file = len(files) == 1
    total_size = sum(size for _, size, _ in files)
    piece_length = _deterministic_piece_length(total_size)

    # Build the pieces: SHA-1 hashes of each piece across all files concatenated
    pieces = b""
    piece_buffer = b""

    for _, _, file_path in files:
        with open(file_path, "rb") as f:
            while True:
                needed = piece_length - len(piece_buffer)
                chunk = f.read(needed)
                if not chunk:
                    break
                piece_buffer += chunk
                if len(piece_buffer) == piece_length:
                    pieces += hashlib.sha1(piece_buffer).digest()
                    piece_buffer = b""

    # Hash the final partial piece
    if piece_buffer:
        pieces += hashlib.sha1(piece_buffer).digest()

    # Build info dict — single-file or multi-file format
    if is_single_file:
        # Single-file torrent: name is the filename, length at top level
        _, size, _ = files[0]
        info = {
            b"length": size,
            b"name": name.encode("utf-8"),
            b"piece length": piece_length,
            b"pieces": pieces,
        }
    else:
        # Multi-file torrent: name is directory name, files list
        file_list = []
        for rel_path, size, _ in files:
            file_list.append({
                b"length": size,
                b"path": [part.encode("utf-8") for part in rel_path.parts],
            })
        info = {
            b"files": file_list,
            b"name": name.encode("utf-8"),
            b"piece length": piece_length,
            b"pieces": pieces,
        }

    # Compute infohash
    info_bencoded = _bencode(info)
    infohash = hashlib.sha1(info_bencoded).hexdigest()

    # Build full torrent metainfo
    metainfo = {
        b"info": info,
    }

    # Announce + announce-list (outside info dict)
    tracker_list = trackers or DEFAULT_TRACKERS
    if tracker_list:
        metainfo[b"announce"] = tracker_list[0].encode("utf-8")
        metainfo[b"announce-list"] = [[t.encode("utf-8")] for t in tracker_list]

    # Webseeds (BEP 19 url-list, outside info dict)
    # Single-file torrents: client fetches the URL directly
    # Multi-file torrents: client appends name/path to the URL
    ws_urls = (single_file_webseeds if is_single_file and single_file_webseeds
               else webseeds)
    if ws_urls:
        if len(ws_urls) == 1:
            metainfo[b"url-list"] = ws_urls[0].encode("utf-8")
        else:
            metainfo[b"url-list"] = [ws.encode("utf-8") for ws in ws_urls]

    if comment:
        metainfo[b"comment"] = comment.encode("utf-8")

    torrent_bytes = _bencode(metainfo)

    # Write to file if path given
    torrent_path = None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(torrent_bytes)
        torrent_path = output_path

    return TorrentResult(
        success=True,
        infohash=infohash,
        torrent_path=torrent_path,
        torrent_bytes=torrent_bytes,
        piece_length=piece_length,
        total_size=total_size,
        file_count=len(files),
        webseeds=ws_urls or [],
    )
