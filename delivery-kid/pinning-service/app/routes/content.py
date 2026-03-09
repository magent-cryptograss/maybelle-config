"""General content draft routes — upload, review, transcode, pin any file type."""

import asyncio
import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..auth import require_wallet_auth
from ..config import get_settings, Settings
from ..models.content import (
    ContentFile, ContentDraftState, ContentDraftResponse, ContentFinalizeRequest
)
from ..services import analyze, ipfs, transcode

router = APIRouter(prefix="/draft-content", tags=["content"])

# All media types we accept
ALLOWED_EXTENSIONS = (
    analyze.AUDIO_EXTENSIONS | analyze.VIDEO_EXTENSIONS | analyze.IMAGE_EXTENSIONS
)


def get_draft_dir(staging_dir: Path, draft_id: str) -> Path:
    return staging_dir / "drafts" / draft_id


def load_draft_state(draft_dir: Path) -> ContentDraftState | None:
    draft_json = draft_dir / "draft.json"
    if not draft_json.exists():
        return None
    try:
        with open(draft_json) as f:
            data = json.load(f)
        # Only load content drafts, not album drafts
        if data.get("draft_type") != "content":
            return None
        return ContentDraftState(**data)
    except (json.JSONDecodeError, ValueError):
        return None


def save_draft_state(draft_dir: Path, state: ContentDraftState) -> None:
    draft_json = draft_dir / "draft.json"
    with open(draft_json, "w") as f:
        json.dump(state.model_dump(mode="json"), f, indent=2, default=str)


def is_draft_expired(state: ContentDraftState) -> bool:
    return datetime.now(timezone.utc) > state.expires_at


@router.post("", response_model=ContentDraftResponse)
async def create_content_draft(
    files: list[UploadFile] = File(...),
    wallet_address: str = Depends(require_wallet_auth),
    settings: Settings = Depends(get_settings)
):
    """
    Upload files to a new content draft for review before pinning.

    Accepts audio, video, and image files. Files are analyzed and
    metadata is returned for review/editing before finalization.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    # Validate file types
    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type: {file.filename}. Allowed extensions: {sorted(ALLOWED_EXTENSIONS)}"
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

        # Analyze all media files
        analyses = await analyze.analyze_media_directory(upload_dir)

        # Convert to ContentFile models
        draft_files = []
        for a in analyses:
            if a.success:
                draft_files.append(ContentFile(
                    original_filename=a.original_filename,
                    detected_title=a.detected_title,
                    media_type=a.media_type,
                    format=a.format,
                    duration_seconds=a.duration_seconds,
                    sample_rate=a.sample_rate,
                    bit_depth=a.bit_depth,
                    channels=a.channels,
                    width=a.width,
                    height=a.height,
                    video_codec=a.video_codec,
                    audio_codec=a.audio_codec,
                    size_bytes=a.size_bytes,
                ))

        if not draft_files:
            shutil.rmtree(draft_dir)
            raise HTTPException(
                status_code=400,
                detail="No valid media files found in upload"
            )

        # Create and save draft state
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=settings.draft_ttl_hours)

        state = ContentDraftState(
            draft_id=draft_id,
            draft_type="content",
            created_at=now,
            expires_at=expires_at,
            uploaded_by=wallet_address,
            files=draft_files,
        )
        save_draft_state(draft_dir, state)

        return ContentDraftResponse(
            draft_id=draft_id,
            expires_at=expires_at,
            files=draft_files,
        )

    except HTTPException:
        raise
    except Exception as e:
        if draft_dir.exists():
            shutil.rmtree(draft_dir)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{draft_id}", response_model=ContentDraftResponse)
async def get_content_draft(
    draft_id: str,
    wallet_address: str = Depends(require_wallet_auth),
    settings: Settings = Depends(get_settings)
):
    """Retrieve content draft state by ID."""
    staging_dir = Path(settings.staging_dir)
    draft_dir = get_draft_dir(staging_dir, draft_id)

    state = load_draft_state(draft_dir)
    if state is None:
        raise HTTPException(status_code=404, detail="Content draft not found")

    if state.uploaded_by.lower() != wallet_address.lower():
        raise HTTPException(status_code=403, detail="Not your draft")

    if is_draft_expired(state):
        shutil.rmtree(draft_dir)
        raise HTTPException(status_code=410, detail="Draft has expired")

    return ContentDraftResponse(
        draft_id=state.draft_id,
        expires_at=state.expires_at,
        files=state.files,
        metadata=state.metadata,
    )


@router.delete("/{draft_id}")
async def delete_content_draft(
    draft_id: str,
    wallet_address: str = Depends(require_wallet_auth),
    settings: Settings = Depends(get_settings)
):
    """Delete a content draft and clean up files."""
    staging_dir = Path(settings.staging_dir)
    draft_dir = get_draft_dir(staging_dir, draft_id)

    state = load_draft_state(draft_dir)
    if state is None:
        raise HTTPException(status_code=404, detail="Content draft not found")

    if state.uploaded_by.lower() != wallet_address.lower():
        raise HTTPException(status_code=403, detail="Not your draft")

    shutil.rmtree(draft_dir)
    return {"message": "Draft deleted", "draft_id": draft_id}


async def finalize_sse_generator(
    draft_id: str,
    request: ContentFinalizeRequest,
    draft_dir: Path,
    state: ContentDraftState,
    settings: Settings
):
    """SSE generator for content finalization — transcode if needed, then pin."""

    async def send_event(event: str, data: dict):
        return {"event": event, "data": json.dumps(data)}

    try:
        upload_dir = draft_dir / "upload"
        output_dir = draft_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        yield await send_event("progress", {
            "stage": "prepare",
            "message": "Preparing content...",
            "progress": 5
        })

        # Determine what we're working with
        video_files = [f for f in state.files if f.media_type == "video"]
        audio_files = [f for f in state.files if f.media_type == "audio"]
        image_files = [f for f in state.files if f.media_type == "image"]

        # For single-file uploads: pin the file directly (or transcode first)
        # For multi-file uploads: pin as a directory
        if len(state.files) == 1 and video_files and request.transcode_hls:
            # Single video → HLS transcode → pin directory
            video_file = video_files[0]
            src_path = upload_dir / video_file.original_filename

            yield await send_event("progress", {
                "stage": "transcode",
                "message": f"Transcoding {video_file.original_filename} to HLS...",
                "progress": 10
            })

            hls_dir = output_dir / "hls"
            result = await transcode.transcode_video_to_hls(src_path, hls_dir)

            if not result.success:
                yield await send_event("error", {
                    "message": f"HLS transcode failed: {result.error}"
                })
                return

            pin_path = hls_dir

            yield await send_event("progress", {
                "stage": "transcode",
                "message": "Transcode complete",
                "progress": 60
            })

        else:
            # No transcode needed — copy files to output and pin as-is
            for f in state.files:
                src = upload_dir / f.original_filename
                if src.exists():
                    shutil.copy2(src, output_dir / f.original_filename)

            pin_path = output_dir

            yield await send_event("progress", {
                "stage": "organize",
                "message": "Files ready",
                "progress": 20
            })

        # Write metadata.json into the pin directory
        metadata = {
            "title": request.title,
            "description": request.description,
            "file_type": request.file_type,
            "subsequent_to": request.subsequent_to,
            "uploaded_by": state.uploaded_by,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **request.metadata,
        }
        # Remove None values
        metadata = {k: v for k, v in metadata.items() if v is not None}

        with open(pin_path / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        yield await send_event("progress", {
            "stage": "ipfs",
            "message": "Pinning to IPFS...",
            "progress": 70
        })

        # Pin to IPFS
        result = await ipfs.add_directory(pin_path)

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

        gateway_url = f"{settings.ipfs_gateway_url}/ipfs/{result.cid}"

        yield await send_event("complete", {
            "cid": result.cid,
            "gateway_url": gateway_url,
            "pinata": result.pinata_success,
            "title": request.title,
            "file_type": request.file_type,
            "subsequent_to": request.subsequent_to,
        })

    except Exception as e:
        yield await send_event("error", {"message": str(e)})

    finally:
        try:
            if draft_dir.exists():
                shutil.rmtree(draft_dir)
        except Exception:
            pass


@router.post("/{draft_id}/finalize")
async def finalize_content_draft(
    draft_id: str,
    request: ContentFinalizeRequest,
    wallet_address: str = Depends(require_wallet_auth),
    settings: Settings = Depends(get_settings)
):
    """
    Finalize a content draft — optionally transcode, then pin to IPFS.

    Progress is streamed via Server-Sent Events.
    """
    staging_dir = Path(settings.staging_dir)
    draft_dir = get_draft_dir(staging_dir, draft_id)

    state = load_draft_state(draft_dir)
    if state is None:
        raise HTTPException(status_code=404, detail="Content draft not found")

    if state.uploaded_by.lower() != wallet_address.lower():
        raise HTTPException(status_code=403, detail="Not your draft")

    if is_draft_expired(state):
        shutil.rmtree(draft_dir)
        raise HTTPException(status_code=410, detail="Draft has expired")

    return EventSourceResponse(
        finalize_sse_generator(draft_id, request, draft_dir, state, settings),
        media_type="text/event-stream"
    )
