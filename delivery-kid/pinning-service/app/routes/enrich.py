"""Torrent generation endpoint — generate deterministic BitTorrent metadata from IPFS CIDs.

Fetches album directory from local IPFS, generates a deterministic torrent,
and returns the infohash + tracker list. Does NOT edit wiki pages — that's
Blue Railroad's responsibility.

Requires API key auth (X-API-Key header).
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_auth
from ..config import get_settings, Settings
from ..services.torrent import create_torrent, DEFAULT_TRACKERS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enrich", tags=["enrich"])


class TorrentRequest(BaseModel):
    cid: str
    name: str | None = None


class TorrentResponse(BaseModel):
    success: bool
    cid: str
    infohash: str | None = None
    trackers: list[str] | None = None
    file_count: int | None = None
    total_size: int | None = None
    piece_length: int | None = None
    error: str | None = None


async def fetch_ipfs_content(cid: str, ipfs_api_url: str) -> Path | None:
    """Fetch a CID from local IPFS to a temp dir.

    Handles both directory CIDs (albums) and single-file CIDs (videos).
    For single files, wraps them in a directory so create_torrent works.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="enrich-"))
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                f"{ipfs_api_url}/api/v0/get",
                params={"arg": cid, "archive": "true"},
            )
            if r.status_code != 200:
                logger.warning("IPFS get failed for %s: %s", cid, r.status_code)
                shutil.rmtree(tmpdir)
                return None

        # Write and extract tar
        tar_path = tmpdir / "archive.tar"
        tar_path.write_bytes(r.content)
        subprocess.run(
            ["tar", "xf", str(tar_path), "-C", str(tmpdir)],
            capture_output=True, check=True,
        )
        tar_path.unlink()

        # Find extracted content — could be a directory or a single file
        children = list(tmpdir.iterdir())
        if not children:
            shutil.rmtree(tmpdir)
            return None

        child = children[0]
        if child.is_dir():
            return child

        # Single file — wrap in a directory for create_torrent
        wrapper = tmpdir / "content"
        wrapper.mkdir()
        child.rename(wrapper / child.name)
        return wrapper
    except Exception as e:
        logger.error("Error fetching %s: %s", cid, e)
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None


@router.post("/torrent", response_model=TorrentResponse)
async def generate_torrent(
    req: TorrentRequest,
    identity: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
):
    """
    Generate deterministic BitTorrent metadata for an IPFS CID.

    Fetches the directory from local IPFS, generates a deterministic
    torrent (same files = same infohash every time), and returns the
    infohash and tracker list.

    The caller (e.g. Blue Railroad bot) is responsible for writing
    the metadata to the wiki page.
    """
    cid = req.cid

    album_dir = await fetch_ipfs_content(cid, settings.ipfs_api_url)
    if album_dir is None:
        return TorrentResponse(
            success=False,
            cid=cid,
            error="Could not fetch CID from IPFS",
        )

    try:
        torrent_name = req.name or cid
        base_url = settings.ipfs_gateway_url.replace("ipfs.", "", 1)
        result = create_torrent(
            directory=album_dir,
            name=torrent_name,
            # Multi-file: Caddy rewrites /webseed/{cid}/{name}/{file} → /ipfs/{cid}/{file}
            webseeds=[
                f"{base_url}/webseed/{cid}/",
            ],
            # Single-file: BEP 19 fetches URL directly
            single_file_webseeds=[
                f"{settings.ipfs_gateway_url}/ipfs/{cid}",
            ],
        )

        if not result.success:
            return TorrentResponse(
                success=False,
                cid=cid,
                error=f"Torrent generation failed: {result.error}",
            )

        return TorrentResponse(
            success=True,
            cid=cid,
            infohash=result.infohash,
            trackers=DEFAULT_TRACKERS,
            file_count=result.file_count,
            total_size=result.total_size,
            piece_length=result.piece_length,
        )

    finally:
        parent = album_dir.parent
        shutil.rmtree(parent, ignore_errors=True)
