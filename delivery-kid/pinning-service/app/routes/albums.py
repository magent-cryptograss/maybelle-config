"""Album upload and pinning routes."""

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..auth import require_wallet_auth
from ..config import get_settings, Settings
from ..services import ipfs, transcode

router = APIRouter()


async def sse_generator(job_id: str, staging_dir: Path, files: list[UploadFile], metadata: dict):
    """
    Generator for SSE progress updates during album processing.
    """
    settings = get_settings()

    async def send_event(event: str, data: dict):
        return {"event": event, "data": json.dumps(data)}

    try:
        # Create job directory
        job_dir = staging_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        upload_dir = job_dir / "upload"
        upload_dir.mkdir()

        yield await send_event("progress", {"stage": "upload", "message": "Receiving files..."})

        # Save uploaded files
        has_flac = False
        for i, file in enumerate(files):
            file_path = upload_dir / file.filename
            yield await send_event("progress", {
                "stage": "upload",
                "message": f"Saving {file.filename}",
                "current": i + 1,
                "total": len(files)
            })

            with open(file_path, "wb") as f:
                content = await file.read()
                f.write(content)

            if file.filename.lower().endswith(".flac"):
                has_flac = True

        # Transcode FLAC to OGG if needed
        pin_dir = upload_dir
        if has_flac:
            yield await send_event("progress", {"stage": "transcode", "message": "Transcoding FLAC to OGG..."})

            ogg_dir = job_dir / "ogg"

            async def transcode_progress(msg: str):
                # We can't yield from here, but we could log
                pass

            success, outputs, errors = await transcode.transcode_album_directory(
                upload_dir, ogg_dir, transcode_progress
            )

            if not success and not outputs:
                yield await send_event("error", {"message": f"Transcode failed: {errors}"})
                return

            if errors:
                yield await send_event("warning", {"message": f"Some files failed: {errors}"})

            # Copy non-FLAC files to OGG directory (cover art, etc.)
            for file_path in upload_dir.iterdir():
                if not file_path.suffix.lower() == ".flac":
                    dest = ogg_dir / file_path.name
                    if not dest.exists():
                        shutil.copy2(file_path, dest)

            pin_dir = ogg_dir

        # Pin to IPFS
        yield await send_event("progress", {"stage": "ipfs", "message": "Pinning to IPFS..."})

        result = await ipfs.add_directory(pin_dir)

        if not result.success:
            yield await send_event("error", {"message": f"IPFS pinning failed: {result.error}"})
            return

        # Build gateway URL
        gateway_url = f"{settings.ipfs_gateway_url}/ipfs/{result.cid}"

        yield await send_event("complete", {
            "cid": result.cid,
            "gateway_url": gateway_url,
            "pinata": result.pinata_success,
            "metadata": metadata
        })

    except Exception as e:
        yield await send_event("error", {"message": str(e)})

    finally:
        # Cleanup staging directory
        try:
            job_dir = staging_dir / job_id
            if job_dir.exists():
                shutil.rmtree(job_dir)
        except Exception:
            pass


@router.post("/pin-album-direct")
async def pin_album_direct(
    files: list[UploadFile] = File(...),
    album_title: str = Form(...),
    artist: str = Form(...),
    year: Optional[str] = Form(None),
    track_order: Optional[str] = Form(None),  # JSON array of filenames in order
    wallet_address: str = Depends(require_wallet_auth),
    settings: Settings = Depends(get_settings)
):
    """
    Upload and pin an album directly.

    Accepts multipart form with audio files (FLAC or OGG).
    FLAC files are transcoded to OGG before pinning.
    Progress is streamed via Server-Sent Events.

    Required headers:
    - X-Signature: Wallet signature
    - X-Timestamp: Timestamp that was signed (milliseconds)
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    # Validate file types
    allowed_extensions = {".flac", ".ogg", ".mp3", ".wav", ".jpg", ".jpeg", ".png", ".webp"}
    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type: {file.filename}. Allowed: {allowed_extensions}"
            )

    # Check total size
    max_size = settings.max_file_size_mb * 1024 * 1024
    total_size = sum(file.size or 0 for file in files)
    if total_size > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"Total upload size exceeds {settings.max_file_size_mb}MB limit"
        )

    job_id = str(uuid.uuid4())
    staging_dir = Path(settings.staging_dir)

    metadata = {
        "album_title": album_title,
        "artist": artist,
        "year": year,
        "track_order": json.loads(track_order) if track_order else None,
        "uploaded_by": wallet_address
    }

    return EventSourceResponse(
        sse_generator(job_id, staging_dir, files, metadata),
        media_type="text/event-stream"
    )


@router.get("/local-pins")
async def list_local_pins(
    wallet_address: str = Depends(require_wallet_auth)
):
    """List all locally pinned CIDs."""
    pins = await ipfs.get_local_pins()
    return {"pins": pins, "count": len(pins)}
