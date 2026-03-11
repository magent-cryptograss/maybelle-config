"""Multi-step album upload draft routes."""

import asyncio
import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..auth import require_auth
from ..config import get_settings, Settings
from ..models.draft import DraftFile, DraftState, DraftResponse, FinalizeRequest
from ..services import analyze, ipfs, transcode

router = APIRouter(prefix="/draft-album", tags=["drafts"])


def get_draft_dir(staging_dir: Path, draft_id: str) -> Path:
    """Get the directory for a draft."""
    return staging_dir / "drafts" / draft_id


def load_draft_state(draft_dir: Path) -> DraftState | None:
    """Load draft state from disk."""
    draft_json = draft_dir / "draft.json"
    if not draft_json.exists():
        return None
    try:
        with open(draft_json) as f:
            data = json.load(f)
        return DraftState(**data)
    except (json.JSONDecodeError, ValueError):
        return None


def save_draft_state(draft_dir: Path, state: DraftState) -> None:
    """Save draft state to disk."""
    draft_json = draft_dir / "draft.json"
    with open(draft_json, "w") as f:
        json.dump(state.model_dump(mode="json"), f, indent=2, default=str)


def is_draft_expired(state: DraftState) -> bool:
    """Check if a draft has expired."""
    return datetime.now(timezone.utc) > state.expires_at


@router.post("", response_model=DraftResponse)
async def create_draft(
    files: list[UploadFile] = File(...),
    wallet_address: str = Depends(require_auth),
    settings: Settings = Depends(get_settings)
):
    """
    Create a new draft album by uploading files.

    Files are saved to temporary storage and analyzed.
    Returns draft ID and file metadata for the review step.

    Required headers:
    - X-Signature: Wallet signature
    - X-Timestamp: Timestamp that was signed (milliseconds)
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    # Validate file types
    allowed_extensions = {".flac", ".ogg", ".mp3", ".wav", ".jpg", ".jpeg", ".png", ".webp", ".m4a"}
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

    # Create draft directory
    draft_id = str(uuid.uuid4())
    staging_dir = Path(settings.staging_dir)
    draft_dir = get_draft_dir(staging_dir, draft_id)
    upload_dir = draft_dir / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Save uploaded files
        for file in files:
            file_path = upload_dir / file.filename
            with open(file_path, "wb") as f:
                content = await file.read()
                f.write(content)

        # Analyze audio files
        analyses = await analyze.analyze_directory(upload_dir)

        # Convert analyses to DraftFile models
        draft_files = []
        for analysis in analyses:
            if analysis.success:
                draft_files.append(DraftFile(
                    original_filename=analysis.original_filename,
                    detected_title=analysis.detected_title,
                    format=analysis.format,
                    duration_seconds=analysis.duration_seconds,
                    sample_rate=analysis.sample_rate,
                    bit_depth=analysis.bit_depth,
                    channels=analysis.channels,
                    size_bytes=analysis.size_bytes
                ))

        if not draft_files:
            # Cleanup if no valid audio files
            shutil.rmtree(draft_dir)
            raise HTTPException(
                status_code=400,
                detail="No valid audio files found in upload"
            )

        # Create and save draft state
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=settings.draft_ttl_hours)

        state = DraftState(
            draft_id=draft_id,
            created_at=now,
            expires_at=expires_at,
            uploaded_by=wallet_address,
            files=draft_files
        )
        save_draft_state(draft_dir, state)

        return DraftResponse(
            draft_id=draft_id,
            expires_at=expires_at,
            files=draft_files
        )

    except HTTPException:
        raise
    except Exception as e:
        # Cleanup on error
        if draft_dir.exists():
            shutil.rmtree(draft_dir)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{draft_id}", response_model=DraftResponse)
async def get_draft(
    draft_id: str,
    wallet_address: str = Depends(require_auth),
    settings: Settings = Depends(get_settings)
):
    """
    Retrieve draft state by ID.

    Useful for recovering state after page refresh.
    """
    staging_dir = Path(settings.staging_dir)
    draft_dir = get_draft_dir(staging_dir, draft_id)

    state = load_draft_state(draft_dir)
    if state is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Verify ownership
    if state.uploaded_by.lower() != wallet_address.lower():
        raise HTTPException(status_code=403, detail="Not your draft")

    # Check expiration
    if is_draft_expired(state):
        # Cleanup expired draft
        shutil.rmtree(draft_dir)
        raise HTTPException(status_code=410, detail="Draft has expired")

    return DraftResponse(
        draft_id=state.draft_id,
        expires_at=state.expires_at,
        files=state.files
    )


@router.delete("/{draft_id}")
async def delete_draft(
    draft_id: str,
    wallet_address: str = Depends(require_auth),
    settings: Settings = Depends(get_settings)
):
    """
    Delete a draft and cleanup files.

    Use this to cancel an upload before finalization.
    """
    staging_dir = Path(settings.staging_dir)
    draft_dir = get_draft_dir(staging_dir, draft_id)

    state = load_draft_state(draft_dir)
    if state is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Verify ownership
    if state.uploaded_by.lower() != wallet_address.lower():
        raise HTTPException(status_code=403, detail="Not your draft")

    # Cleanup
    shutil.rmtree(draft_dir)

    return {"message": "Draft deleted", "draft_id": draft_id}


async def finalize_sse_generator(
    draft_id: str,
    request: FinalizeRequest,
    draft_dir: Path,
    state: DraftState,
    settings: Settings
):
    """
    Generator for SSE progress updates during album finalization.

    Transcodes files to OGG, creates album structure, pins to IPFS.
    """

    async def send_event(event: str, data: dict):
        return {"event": event, "data": json.dumps(data)}

    try:
        upload_dir = draft_dir / "upload"
        album_dir = draft_dir / "album"
        flac_dir = album_dir / "flac"
        ogg_dir = album_dir / "ogg"

        yield await send_event("progress", {
            "stage": "prepare",
            "message": "Preparing album structure...",
            "progress": 5
        })

        # Create album structure
        album_dir.mkdir(parents=True, exist_ok=True)
        flac_dir.mkdir(exist_ok=True)
        ogg_dir.mkdir(exist_ok=True)

        # Build track mapping from request order (includes title and per-track tags)
        track_map = {t.filename: t for t in request.tracks}
        ordered_files = [t.filename for t in request.tracks]

        yield await send_event("progress", {
            "stage": "organize",
            "message": "Organizing tracks...",
            "progress": 10
        })

        # Copy and rename files according to track order
        # Also build mapping from new filename to track info for transcoding
        has_flac = False  # True if user uploaded FLAC files (need FLAC→OGG)
        has_wav = False   # True if user uploaded WAV files (need WAV→FLAC and WAV→OGG)
        flac_to_track_info = {}  # Maps new FLAC filename -> FinalizeTrack
        wav_to_convert = []  # List of (src_wav, dest_flac, dest_ogg, track_info) tuples
        for idx, filename in enumerate(ordered_files, start=1):
            src_path = upload_dir / filename
            if not src_path.exists():
                yield await send_event("error", {
                    "message": f"File not found: {filename}"
                })
                return

            ext = src_path.suffix.lower()
            track_num = f"{idx:02d}"
            track_info = track_map.get(filename)
            title = track_info.title if track_info else filename

            # Sanitize title for filename
            safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in title)
            safe_title = safe_title.strip()[:50]  # Limit length

            if ext == ".flac":
                has_flac = True
                dest_name = f"{track_num}-{safe_title}.flac"
                shutil.copy2(src_path, flac_dir / dest_name)
                flac_to_track_info[dest_name] = track_info
            elif ext == ".wav":
                # WAV files: convert to FLAC (archive) and OGG (streaming) directly
                has_wav = True
                flac_dest = f"{track_num}-{safe_title}.flac"
                ogg_dest = f"{track_num}-{safe_title}.ogg"
                wav_to_convert.append((src_path, flac_dir / flac_dest, ogg_dir / ogg_dest, track_info))
            elif ext in {".jpg", ".jpeg", ".png", ".webp"}:
                # Cover art goes to album root
                shutil.copy2(src_path, album_dir / "cover" + ext)
            else:
                # Other audio formats go directly to OGG dir
                dest_name = f"{track_num}-{safe_title}{ext}"
                shutil.copy2(src_path, ogg_dir / dest_name)

        # Convert WAV files to FLAC (archive) and OGG (streaming) directly
        if wav_to_convert:
            yield await send_event("progress", {
                "stage": "wav_convert",
                "message": "Converting WAV files...",
                "progress": 10
            })

            for i, (wav_path, flac_path, ogg_path, track_info) in enumerate(wav_to_convert):
                base_progress = 10 + int((i / len(wav_to_convert)) * 25)

                # Extract track number from filename for metadata
                track_num_str = flac_path.stem.split("-")[0] if "-" in flac_path.stem else str(i + 1)
                track_title = "-".join(flac_path.stem.split("-")[1:]) if "-" in flac_path.stem else flac_path.stem

                # Build metadata for this track
                track_metadata = {
                    "ARTIST": request.artist,
                    "ALBUM": request.album_title,
                    "TITLE": track_title,
                    "TRACKNUMBER": track_num_str,
                }
                if request.year:
                    track_metadata["DATE"] = request.year
                if track_info and track_info.tags:
                    track_metadata.update(track_info.tags)

                # Convert WAV to FLAC (lossless archive)
                yield await send_event("progress", {
                    "stage": "wav_convert",
                    "message": f"Converting {wav_path.name} to FLAC...",
                    "progress": base_progress,
                    "track": wav_path.name
                })

                flac_cmd = [
                    "ffmpeg", "-y", "-i", str(wav_path),
                    "-c:a", "flac",
                    "-compression_level", "8",
                    str(flac_path)
                ]
                proc = await asyncio.create_subprocess_exec(
                    *flac_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                _, stderr = await proc.communicate()

                if proc.returncode != 0:
                    yield await send_event("warning", {
                        "message": f"Failed to convert {wav_path.name} to FLAC: {stderr.decode()[:200]}"
                    })

                # Convert WAV to OGG directly (with metadata)
                yield await send_event("progress", {
                    "stage": "wav_convert",
                    "message": f"Converting {wav_path.name} to OGG...",
                    "progress": base_progress + 5,
                    "track": wav_path.name
                })

                ogg_cmd = ["ffmpeg", "-y", "-i", str(wav_path), "-c:a", "libvorbis", "-q:a", "6"]
                for key, value in track_metadata.items():
                    ogg_cmd.extend(["-metadata", f"{key}={value}"])
                ogg_cmd.append(str(ogg_path))

                proc = await asyncio.create_subprocess_exec(
                    *ogg_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                _, stderr = await proc.communicate()

                if proc.returncode != 0:
                    yield await send_event("warning", {
                        "message": f"Failed to convert {wav_path.name} to OGG: {stderr.decode()[:200]}"
                    })

        # Transcode FLAC files to OGG
        if has_flac:
            yield await send_event("progress", {
                "stage": "transcode",
                "message": "Transcoding FLAC to OGG...",
                "progress": 20
            })

            flac_files = sorted(flac_dir.glob("*.flac"))
            total_flac = len(flac_files)

            for i, flac_path in enumerate(flac_files):
                ogg_name = flac_path.stem + ".ogg"
                ogg_path = ogg_dir / ogg_name

                # Skip if OGG already exists (e.g., from WAV→OGG conversion)
                if ogg_path.exists():
                    continue

                # Extract track number and title from filename (format: "01-Title.flac")
                track_num_str = flac_path.stem.split("-")[0] if "-" in flac_path.stem else str(i + 1)
                track_title = "-".join(flac_path.stem.split("-")[1:]) if "-" in flac_path.stem else flac_path.stem

                # Get per-track info for tags
                this_track_info = flac_to_track_info.get(flac_path.name)

                # Build metadata for this track
                track_metadata = {
                    "ARTIST": request.artist,
                    "ALBUM": request.album_title,
                    "TITLE": track_title,
                    "TRACKNUMBER": track_num_str,
                }
                if request.year:
                    track_metadata["DATE"] = request.year
                # Add per-track custom tags
                if this_track_info and this_track_info.tags:
                    track_metadata.update(this_track_info.tags)

                yield await send_event("progress", {
                    "stage": "transcode",
                    "message": f"Transcoding {flac_path.name}...",
                    "progress": 20 + int((i / total_flac) * 40),
                    "track": flac_path.name
                })

                result = await transcode.transcode_flac_to_ogg(flac_path, ogg_path, metadata=track_metadata)

                if not result.success:
                    yield await send_event("warning", {
                        "message": f"Failed to transcode {flac_path.name}: {result.error}"
                    })

        # Create metadata.json
        yield await send_event("progress", {
            "stage": "metadata",
            "message": "Writing metadata...",
            "progress": 65
        })

        metadata = {
            "album_title": request.album_title,
            "artist": request.artist,
            "year": request.year,
            "description": request.description,
            "tracks": [
                {"track_number": i + 1, "title": t.title, "original_filename": t.filename}
                for i, t in enumerate(request.tracks)
            ],
            "uploaded_by": state.uploaded_by,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        with open(album_dir / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        # Pin to IPFS
        yield await send_event("progress", {
            "stage": "ipfs",
            "message": "Pinning to IPFS...",
            "progress": 70
        })

        result = await ipfs.add_directory(album_dir)

        if not result.success:
            yield await send_event("error", {
                "message": f"IPFS pinning failed: {result.error}"
            })
            return

        yield await send_event("progress", {
            "stage": "ipfs",
            "message": "Verifying pin...",
            "progress": 90
        })

        # Build gateway URL
        gateway_url = f"{settings.ipfs_gateway_url}/ipfs/{result.cid}"

        yield await send_event("complete", {
            "cid": result.cid,
            "gateway_url": gateway_url,
            "pinata": result.pinata_success,
            "album_title": request.album_title,
            "artist": request.artist,
            "tracks": [
                {"track_number": i + 1, "title": t.title, "filename": t.filename}
                for i, t in enumerate(request.tracks)
            ]
        })

    except Exception as e:
        yield await send_event("error", {"message": str(e)})

    finally:
        # Cleanup draft directory after finalization
        try:
            if draft_dir.exists():
                shutil.rmtree(draft_dir)
        except Exception:
            pass


@router.post("/{draft_id}/finalize")
async def finalize_draft(
    draft_id: str,
    request: FinalizeRequest,
    wallet_address: str = Depends(require_auth),
    settings: Settings = Depends(get_settings)
):
    """
    Finalize a draft album.

    Transcodes files to OGG, creates album structure with both FLAC and OGG,
    pins to IPFS, and returns the CID.

    Progress is streamed via Server-Sent Events.
    """
    staging_dir = Path(settings.staging_dir)
    draft_dir = get_draft_dir(staging_dir, draft_id)

    state = load_draft_state(draft_dir)
    if state is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Verify ownership
    if state.uploaded_by.lower() != wallet_address.lower():
        raise HTTPException(status_code=403, detail="Not your draft")

    # Check expiration
    if is_draft_expired(state):
        shutil.rmtree(draft_dir)
        raise HTTPException(status_code=410, detail="Draft has expired")

    # Validate all requested files exist in draft
    draft_filenames = {f.original_filename for f in state.files}
    for track in request.tracks:
        if track.filename not in draft_filenames:
            raise HTTPException(
                status_code=400,
                detail=f"File not in draft: {track.filename}"
            )

    return EventSourceResponse(
        finalize_sse_generator(draft_id, request, draft_dir, state, settings),
        media_type="text/event-stream"
    )
