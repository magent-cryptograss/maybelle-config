"""General content draft routes — upload, review, transcode, pin any file type."""

import asyncio
import json
import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..auth import require_auth, require_finalize_auth, has_finalize_token
from ..config import get_settings, get_commit, Settings
from ..models.content import (
    ContentFile, ContentDraftState, ContentDraftResponse, ContentFinalizeRequest
)
from ..services import analyze, ipfs, transcode
from ..services.coconut import submit_to_coconut, save_job, load_job

logger = logging.getLogger(__name__)

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


@router.post("", response_model=ContentDraftResponse)
async def create_content_draft(
    files: list[UploadFile] = File(...),
    wallet_address: str = Depends(require_auth),
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
                    creation_time=a.creation_time,
                ))

        if not draft_files:
            shutil.rmtree(draft_dir)
            raise HTTPException(
                status_code=400,
                detail="No valid media files found in upload"
            )

        # Create and save draft state
        now = datetime.now(timezone.utc)

        # Determine if this is a single-video upload that should get a preview
        video_files = [f for f in draft_files if f.media_type == "video"]
        should_preview = len(draft_files) == 1 and len(video_files) == 1 and settings.coconut_api_key

        state = ContentDraftState(
            draft_id=draft_id,
            draft_type="content",
            created_at=now,
            uploaded_by=wallet_address,
            files=draft_files,
            preview_status="pending" if should_preview else "none",
        )
        save_draft_state(draft_dir, state)

        # Kick off background preview transcoding for video uploads
        if should_preview:
            asyncio.create_task(
                _submit_preview_transcode(draft_id, state, settings)
            )

        return ContentDraftResponse(
            draft_id=draft_id,
            files=draft_files,
            commit=get_commit(),
            preview_status=state.preview_status,
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
    request: Request,
    wallet_address: str = Depends(require_auth),
    settings: Settings = Depends(get_settings)
):
    """Retrieve content draft state by ID.

    Accessible by the original uploader OR any user with finalize-release
    permission (indicated by a valid finalize-prefixed HMAC token).
    """
    staging_dir = Path(settings.staging_dir)
    draft_dir = get_draft_dir(staging_dir, draft_id)

    state = load_draft_state(draft_dir)
    if state is None:
        raise HTTPException(status_code=404, detail="Content draft not found")

    is_owner = state.uploaded_by.lower() == wallet_address.lower()
    if not is_owner and not has_finalize_token(request, settings):
        raise HTTPException(status_code=403, detail="Not your draft")

    return ContentDraftResponse(
        draft_id=state.draft_id,
        files=state.files,
        metadata=state.metadata,
        commit=get_commit(),
        preview_status=state.preview_status,
        preview_cid=state.preview_cid,
        preview_mp4_cid=state.preview_mp4_cid,
    )


@router.delete("/{draft_id}")
async def delete_content_draft(
    draft_id: str,
    request: Request,
    wallet_address: str = Depends(require_auth),
    settings: Settings = Depends(get_settings)
):
    """Delete a content draft and clean up files.

    Accessible by the original uploader OR any user with finalize-release.
    """
    staging_dir = Path(settings.staging_dir)
    draft_dir = get_draft_dir(staging_dir, draft_id)

    state = load_draft_state(draft_dir)
    if state is None:
        raise HTTPException(status_code=404, detail="Content draft not found")

    is_owner = state.uploaded_by.lower() == wallet_address.lower()
    if not is_owner and not has_finalize_token(request, settings):
        raise HTTPException(status_code=403, detail="Not your draft")

    shutil.rmtree(draft_dir)
    return {"message": "Draft deleted", "draft_id": draft_id}


async def _submit_preview_transcode(
    draft_id: str, state: ContentDraftState, settings: Settings
) -> None:
    """Background task: submit video to Coconut for AV1 HLS preview.

    Coconut fetches the source from our staging endpoint via preview_token,
    transcodes to AV1 HLS, and delivers via webhook. The webhook handler
    pins the HLS output to IPFS and updates draft state with the CID.
    """
    staging_dir = Path(settings.staging_dir)
    draft_dir = get_draft_dir(staging_dir, draft_id)

    try:
        video_file = state.files[0]

        # Build the source URL: Coconut will fetch from our staging endpoint
        # using the preview_token for auth (no IPFS pin of the original needed)
        base_url = settings.ipfs_gateway_url.replace("ipfs.", "", 1)
        source_url = (
            f"{base_url}/staging/drafts/{draft_id}/{video_file.original_filename}"
            f"?preview_token={state.preview_token}"
        )

        # Build webhook URL — reuses existing /webhook/coconut handler
        job_id = f"preview-{draft_id[:12]}-{int(time.time())}"
        webhook_url = f"{base_url}/webhook/coconut?job_id={job_id}"

        logger.info("[preview:%s] Submitting to Coconut, source=%s", draft_id[:8], source_url[:80])

        coconut_result = await submit_to_coconut(
            source_url=source_url,
            api_key=settings.coconut_api_key,
            webhook_url=webhook_url,
            include_preview=True,
        )
        coconut_job_id = coconut_result.get("id")
        logger.info("[preview:%s] Coconut job created: %s", draft_id[:8], coconut_job_id)

        # Save job state for the webhook handler
        job_state = {
            "id": job_id,
            "coconutJobId": coconut_job_id,
            "status": "processing",
            "draftId": draft_id,
            "isPreview": True,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "identity": state.uploaded_by,
        }
        save_job(staging_dir, job_id, job_state)

        # Update draft state
        state.preview_status = "processing"
        state.preview_job_id = job_id
        save_draft_state(draft_dir, state)

    except Exception as e:
        logger.error("[preview:%s] Failed to submit preview: %s", draft_id[:8], e)
        try:
            state.preview_status = "failed"
            save_draft_state(draft_dir, state)
        except Exception:
            pass


def _should_use_coconut(request: ContentFinalizeRequest, settings: Settings) -> bool:
    """Determine if we should try Coconut cloud transcoding."""
    strategy = request.transcoding_strategy
    if strategy == "none":
        return False
    if strategy == "local":
        return False
    if strategy == "coconut":
        return bool(settings.coconut_api_key)
    # "auto" — use Coconut if available, otherwise local
    return bool(settings.coconut_api_key)


def _should_transcode_video(request: ContentFinalizeRequest) -> bool:
    """Determine if video transcoding is requested."""
    if request.transcoding_strategy == "none":
        return False
    # Legacy field support
    if request.transcode_hls:
        return True
    # Auto/coconut/local all imply transcoding for video
    return request.transcoding_strategy in ("auto", "coconut", "local")


async def finalize_sse_generator(
    draft_id: str,
    request: ContentFinalizeRequest,
    draft_dir: Path,
    state: ContentDraftState,
    settings: Settings
):
    """SSE generator for content finalization — transcode if needed, then pin.

    Fast path: if preview transcoding already produced an HLS CID and no trim
    is requested, finalization is instant — just emit the existing CID.

    Slow path (trim requested or no preview): Coconut cloud transcoding first,
    local ffmpeg fallback. Coconut fetches source from staging via preview_token.
    """
    async def send_event(event: str, data: dict):
        return {"event": event, "data": json.dumps(data)}

    has_trim = request.trim_start_seconds is not None or request.trim_end_seconds is not None

    try:
        upload_dir = draft_dir / "upload"
        output_dir = draft_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        yield await send_event("progress", {
            "stage": "prepare",
            "message": "Preparing content...",
            "progress": 5
        })

        # Preserve original file if requested
        if request.preserve_original:
            originals_dir = Path(settings.staging_dir) / "originals" / draft_id
            originals_dir.mkdir(parents=True, exist_ok=True)
            for f in state.files:
                src = upload_dir / f.original_filename
                if src.exists():
                    shutil.copy2(src, originals_dir / f.original_filename)
            logger.info("[content:%s] Original files preserved to %s", draft_id[:8], originals_dir)

        video_files = [f for f in state.files if f.media_type == "video"]
        wants_transcode = len(state.files) == 1 and video_files and _should_transcode_video(request)

        # === Fast path: preview already done, no trim ===
        if wants_transcode and state.preview_cid and not has_trim:
            logger.info("[content:%s] Using existing preview HLS: %s", draft_id[:8], state.preview_cid)
            yield await send_event("progress", {
                "stage": "transcode",
                "message": "AV1 HLS already transcoded — using preview.",
                "progress": 80
            })

            gateway_url = f"{settings.ipfs_gateway_url}/ipfs/{state.preview_cid}"

            yield await send_event("complete", {
                "cid": state.preview_cid,
                "gateway_url": gateway_url,
                "title": request.title,
                "file_type": request.file_type,
                "subsequent_to": request.subsequent_to,
            })
            return

        # === Coconut cloud transcoding (with trim, or no preview available) ===
        if wants_transcode and _should_use_coconut(request, settings):
            video_file = video_files[0]
            src_path = upload_dir / video_file.original_filename

            # Build source URL — Coconut fetches from staging via preview_token
            base_url = settings.ipfs_gateway_url.replace("ipfs.", "", 1)
            source_url = (
                f"{base_url}/staging/drafts/{draft_id}/{video_file.original_filename}"
                f"?preview_token={state.preview_token}"
            )

            trim_msg = ""
            if has_trim:
                s = request.trim_start_seconds or 0
                e = request.trim_end_seconds
                trim_msg = f" (trimming {s:.1f}s–{e:.1f}s)" if e else f" (trimming from {s:.1f}s)"
            yield await send_event("progress", {
                "stage": "transcode",
                "message": f"Submitting to Coconut for AV1 transcoding{trim_msg}...",
                "progress": 30
            })

            job_id = f"coconut-{int(time.time())}-{id(src_path) % 100000:05d}"
            webhook_url = f"{base_url}/webhook/coconut?job_id={job_id}"

            try:
                coconut_result = await submit_to_coconut(
                    source_url=source_url,
                    api_key=settings.coconut_api_key,
                    webhook_url=webhook_url,
                    qualities=request.transcoding_qualities,
                    trim_start=request.trim_start_seconds,
                    trim_end=request.trim_end_seconds,
                )
                coconut_job_id = coconut_result.get("id")
                logger.info("[content:%s] Coconut job created: %s", draft_id[:8], coconut_job_id)

                job_state = {
                    "id": job_id,
                    "coconutJobId": coconut_job_id,
                    "status": "processing",
                    "keepOriginal": request.preserve_original,
                    "title": request.title,
                    "fileType": request.file_type,
                    "subsequentTo": request.subsequent_to,
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "identity": state.uploaded_by,
                }
                save_job(Path(settings.staging_dir), job_id, job_state)

                # Don't delete draft dir yet — source file still needed if Coconut
                # hasn't fetched it. Draft TTL cleanup handles it.

                yield await send_event("transcoding-submitted", {
                    "jobId": job_id,
                    "coconutJobId": coconut_job_id,
                    "message": "Video submitted for AV1 cloud transcoding. HLS output will be pinned automatically when complete.",
                    "pollUrl": f"/job/{job_id}",
                    "title": request.title,
                    "fileType": request.file_type,
                    "subsequentTo": request.subsequent_to,
                })
                return

            except Exception as e:
                logger.warning(
                    "[content:%s] Coconut submission failed, falling back to local: %s",
                    draft_id[:8], e
                )
                yield await send_event("progress", {
                    "stage": "transcode",
                    "message": "Cloud transcoding unavailable, using local ffmpeg...",
                    "progress": 15
                })

        if wants_transcode:
            # === Local ffmpeg transcoding path (sync) ===
            video_file = video_files[0]
            src_path = upload_dir / video_file.original_filename

            yield await send_event("progress", {
                "stage": "transcode",
                "message": f"Transcoding {video_file.original_filename} to HLS...",
                "progress": 10
            })

            hls_dir = output_dir / "hls"
            result = await transcode.transcode_video_to_hls(
                src_path, hls_dir,
                trim_start=request.trim_start_seconds,
                trim_end=request.trim_end_seconds,
            )

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
        metadata = {k: v for k, v in metadata.items() if v is not None}

        with open(pin_path / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)

        yield await send_event("progress", {
            "stage": "ipfs",
            "message": "Pinning to IPFS...",
            "progress": 70
        })

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
    wallet_address: str = Depends(require_finalize_auth),
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

    # No ownership check — require_finalize_auth already ensures
    # the user has finalize-release permission.

    return EventSourceResponse(
        finalize_sse_generator(draft_id, request, draft_dir, state, settings),
        media_type="text/event-stream"
    )
