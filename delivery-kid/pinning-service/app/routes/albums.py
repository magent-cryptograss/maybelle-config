"""Album and pin management routes."""

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth, require_wallet_auth
from ..config import get_settings, Settings
from ..services import ipfs

router = APIRouter()


@router.get("/local-pins")
async def list_local_pins():
    """List all locally pinned CIDs. Public endpoint for build-time fetching."""
    cids = await ipfs.get_local_pins()
    # Return as objects with 'cid' property for arthel compatibility
    pins = [{"cid": cid} for cid in cids]
    return {"pins": pins, "count": len(pins), "node": "delivery-kid"}


# Encoding subdirectories we know how to pair across (FLAC ↔ OGG ↔ ...).
# delivery-kid pins record-type albums in the shape:
#   <album>/flac/{N - Title}.flac
#   <album>/ogg/{N - Title}.ogg
#   <album>/metadata.json
# so the same conceptual track appears once per format, sharing a basename
# (the part before the extension). We pair by basename.
KNOWN_ENCODING_DIRS = ("flac", "ogg", "mp3", "m4a", "wav")


def _strip_ext(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[:dot] if dot > 0 else filename


def _leading_track_number(basename: str) -> int | None:
    # Filenames look like "1 - Paint Exchange" or "01 - Foo". Take leading
    # digits; if absent, return None and the caller falls back to position.
    digits = []
    for ch in basename:
        if ch.isdigit():
            digits.append(ch)
        else:
            break
    return int("".join(digits)) if digits else None


def _title_from_basename(basename: str) -> str:
    # Strip leading "{N} - " if present, else use the basename as-is.
    sep = basename.find(" - ")
    if sep > 0 and basename[:sep].strip().isdigit():
        return basename[sep + 3:].strip()
    return basename.strip()


@router.get("/album-tracks/{album_cid}")
async def album_tracks(album_cid: str):
    """
    Return the per-track structure of a record-type album CID.

    Reads the album's IPFS directory listing, recurses into known encoding
    subdirs (flac/, ogg/, ...), pairs files by basename, and surfaces each
    conceptual track once with all its encoding CIDs. Public read — IPFS
    contents are public anyway.
    """
    top = await ipfs.list_directory(album_cid)
    if not top:
        raise HTTPException(404, f"Could not list {album_cid}")

    # Per-basename merge across encodings.
    tracks_by_basename: dict[str, dict] = {}
    extras = []

    for entry in top:
        name = entry.get("name", "")
        if entry.get("type") == 1 and name in KNOWN_ENCODING_DIRS:
            files = await ipfs.list_directory(entry["cid"])
            for f in files:
                fname = f.get("name", "")
                basename = _strip_ext(fname)
                slot = tracks_by_basename.setdefault(basename, {
                    "track_number": _leading_track_number(basename),
                    "title": _title_from_basename(basename),
                    "encodings": {},
                })
                slot["encodings"][name] = {
                    "cid": f.get("cid"),
                    "filename": fname,
                    "size": f.get("size", 0),
                }
        else:
            # Not an encoding subdir — surface as an extra (metadata.json,
            # cover art, etc.).
            extras.append({
                "name": name,
                "cid": entry.get("cid"),
                "size": entry.get("size", 0),
                "type": entry.get("type", 0),
            })

    # Order tracks: by leading track_number when present, else by basename.
    tracks = list(tracks_by_basename.values())
    tracks.sort(key=lambda t: (
        t["track_number"] is None,
        t["track_number"] if t["track_number"] is not None else 0,
        t["title"],
    ))
    # Backfill positional track_number when filenames lacked one.
    for i, track in enumerate(tracks, start=1):
        if track["track_number"] is None:
            track["track_number"] = i

    return {
        "album_cid": album_cid,
        "tracks": tracks,
        "extras": extras,
    }


@router.post("/pin/{cid}")
async def pin_cid(
    cid: str,
    identity: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
):
    """
    Pin a CID to the local IPFS node.

    Accepts API key, HMAC token, or wallet auth.
    """
    result = await ipfs.pin_cid(cid)

    if not result.success:
        raise HTTPException(
            status_code=500,
            detail=f"Pin failed: {result.error}"
        )

    return {
        "success": True,
        "cid": cid,
        "message": f"Pinned {cid}",
    }


@router.delete("/unpin/{cid}")
async def unpin_cid(
    cid: str,
    identity: str = Depends(require_auth),
):
    """
    Unpin a CID from both local IPFS and Pinata.

    Accepts API key, HMAC token, or wallet auth.
    """
    result = await ipfs.unpin(cid)

    if not result.success:
        raise HTTPException(
            status_code=500,
            detail=f"Unpin failed: {result.error}"
        )

    return {
        "success": True,
        "cid": cid,
        "local_unpinned": result.local_unpinned,
        "pinata_unpinned": result.pinata_unpinned,
        "message": f"Unpinned {cid}"
    }
