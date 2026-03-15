"""Serve .torrent files for download."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ..services.seeder import get_seeder

router = APIRouter(prefix="/torrent", tags=["torrent"])


@router.get("/{infohash}.torrent")
async def get_torrent_file(infohash: str):
    """Serve a .torrent file by infohash."""
    seeder = get_seeder()
    if not seeder:
        raise HTTPException(503, "Seeder not running")

    torrent_bytes = seeder.get_torrent_file(infohash)
    if not torrent_bytes:
        raise HTTPException(404, "Torrent not found")

    return Response(
        content=torrent_bytes,
        media_type="application/x-bittorrent",
        headers={"Content-Disposition": f'attachment; filename="{infohash}.torrent"'},
    )


@router.get("/status")
async def seeder_status():
    """Get seeder status."""
    seeder = get_seeder()
    if not seeder:
        return {"running": False}
    return seeder.status()
