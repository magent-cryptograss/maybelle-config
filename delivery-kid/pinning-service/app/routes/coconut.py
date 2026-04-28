"""Coconut.co cloud transcoding routes.

POST /transcode-coconut  — submit a video for AV1 HLS transcoding
POST /webhook/coconut     — receive completion/failure from Coconut
GET  /job/{job_id}        — check job status
GET  /jobs                — list recent jobs
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Request
from pydantic import BaseModel

from ..auth import require_auth
from ..config import get_settings, Settings
from ..models.content import ContentDraftState
from ..services import ipfs
from ..services.coconut import (
    submit_to_coconut,
    save_job,
    load_job,
    list_jobs,
    process_completed_job,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["coconut"])


PREVIEW_LOG_MAX = 50


def _append_preview_log(staging_dir: Path, draft_id: str, message: str,
                        progress: int | None = None,
                        status: str | None = None) -> None:
    """Append a single progress entry to a draft's preview_log.

    ``status`` (when non-None) updates ``preview_status`` in the same write —
    saves a separate disk hit. The log is capped at PREVIEW_LOG_MAX entries.
    """
    draft_json = staging_dir / "drafts" / draft_id / "draft.json"
    if not draft_json.exists():
        return
    try:
        data = json.loads(draft_json.read_text())
        log = data.get("preview_log") or []
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "message": message,
        }
        if progress is not None:
            entry["progress"] = progress
        log.append(entry)
        # Keep only the most recent N — drafts can in theory accumulate many
        # progress events across retries and we don't want draft.json to bloat.
        data["preview_log"] = log[-PREVIEW_LOG_MAX:]
        if status is not None:
            data["preview_status"] = status
        draft_json.write_text(json.dumps(data, indent=2, default=str))
    except Exception as e:
        logger.error("[%s] Failed to append preview log: %s", draft_id[:8], e)


def _update_draft_preview(staging_dir: Path, job: dict) -> None:
    """Update a content draft's preview state after Coconut webhook."""
    draft_id = job["draftId"]
    draft_json = staging_dir / "drafts" / draft_id / "draft.json"
    if not draft_json.exists():
        logger.warning("[%s] Preview draft not found: %s", job["id"], draft_id)
        return
    try:
        data = json.loads(draft_json.read_text())
        log = data.get("preview_log") or []
        if job["status"] == "complete" and job.get("hlsCid"):
            data["preview_status"] = "ready"
            data["preview_cid"] = job["hlsCid"]
            # previewCid = 480p MP4 for the video player on the ReleaseDraft page
            if job.get("previewCid"):
                data["preview_mp4_cid"] = job["previewCid"]
            log.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "message": f"Transcode complete · HLS pinned ({job['hlsCid'][:12]}…)",
                "progress": 100,
            })
            logger.info("[%s] Draft %s preview ready: hls=%s mp4=%s",
                        job["id"], draft_id[:8], job["hlsCid"], job.get("previewCid", "none"))
        else:
            data["preview_status"] = "failed"
            err = job.get("error") or "Unknown error"
            log.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "message": f"Preview transcode failed: {err}",
            })
            logger.warning("[%s] Draft %s preview failed", job["id"], draft_id[:8])
        data["preview_log"] = log[-PREVIEW_LOG_MAX:]
        draft_json.write_text(json.dumps(data, indent=2, default=str))
    except Exception as e:
        logger.error("[%s] Failed to update draft preview state: %s", job["id"], e)


class TranscodeRequest(BaseModel):
    qualities: list[int] = [720, 480]
    keep_original: bool = False


class TranscodeResponse(BaseModel):
    """Response uses camelCase to match arthel frontend expectations."""
    jobId: str
    coconutJobId: str | None = None
    status: str
    sourceCid: str | None = None
    message: str


@router.post("/transcode-coconut", response_model=TranscodeResponse)
async def transcode_coconut(
    video: UploadFile = File(...),
    identity: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
):
    """Submit a video for AV1 HLS transcoding via Coconut.co.

    Pins the source video to IPFS first (so Coconut can fetch it),
    then submits a transcoding job. Poll GET /job/{job_id} for status.
    """
    if not settings.coconut_api_key:
        raise HTTPException(500, "Coconut API not configured")

    job_id = f"coconut-{int(time.time())}-{id(video) % 100000:05d}"
    staging_dir = Path(settings.staging_dir)

    try:
        # Save uploaded video to staging
        video_dir = staging_dir / f"coconut-src-{job_id}"
        video_dir.mkdir(parents=True, exist_ok=True)
        video_path = video_dir / (video.filename or "video.mp4")

        content = await video.read()
        video_path.write_bytes(content)

        # Pin source to IPFS so Coconut can fetch it via gateway
        logger.info("[%s] Pinning source video to IPFS...", job_id)
        pin_result = await ipfs.add_file(video_path)

        if not pin_result.success:
            raise HTTPException(500, f"Failed to pin source video: {pin_result.error}")

        source_cid = pin_result.cid
        source_url = f"{settings.ipfs_gateway_url}/ipfs/{source_cid}"
        logger.info("[%s] Source pinned: %s", job_id, source_cid)

        # Parse qualities from form data (arthel frontend sends as JSON string)
        # but our Pydantic model handles the default

        # Build webhook URL
        base_url = settings.ipfs_gateway_url.replace("ipfs.", "", 1)
        webhook_url = f"{base_url}/webhook/coconut?job_id={job_id}"

        # Submit to Coconut
        logger.info("[%s] Submitting to Coconut...", job_id)
        coconut_result = await submit_to_coconut(
            source_url=source_url,
            api_key=settings.coconut_api_key,
            webhook_url=webhook_url,
            qualities=[720, 480],  # Default for now
        )

        coconut_job_id = coconut_result.get("id")
        logger.info("[%s] Coconut job created: %s", job_id, coconut_job_id)

        # Save job state (camelCase to match arthel frontend polling)
        job_state = {
            "id": job_id,
            "coconutJobId": coconut_job_id,
            "status": "processing",
            "sourceCid": source_cid,
            "keepOriginal": False,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "identity": identity,
        }
        save_job(staging_dir, job_id, job_state)

        # Clean up temp video file (source is on IPFS now)
        import shutil
        shutil.rmtree(video_dir, ignore_errors=True)

        return TranscodeResponse(
            jobId=job_id,
            coconutJobId=coconut_job_id,
            status="processing",
            sourceCid=source_cid,
            message="Video submitted for transcoding. Check /job/{job_id} for status.",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[%s] Error: %s", job_id, e)
        raise HTTPException(500, str(e))


@router.post("/webhook/coconut")
async def webhook_coconut(request: Request, settings: Settings = Depends(get_settings)):
    """Receive completion/failure webhook from Coconut.co."""
    job_id = request.query_params.get("job_id")
    if not job_id:
        raise HTTPException(400, "Missing job_id")

    staging_dir = Path(settings.staging_dir)
    job = load_job(staging_dir, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    event = await request.json()
    event_type = event.get("event", "unknown")
    logger.info("[%s] Coconut webhook: %s", job_id, event_type)

    # Capture progress/lifecycle events into the draft's preview_log so the
    # ReleaseDraft page can show what's happening during transcoding. Only
    # for preview jobs — finalize uses its own SSE stream.
    if job.get("isPreview") and job.get("draftId"):
        draft_id = job["draftId"]
        staging_dir_path = Path(settings.staging_dir)
        if event_type == "job.progress":
            progress = event.get("progress")
            stage = event.get("stage") or event.get("status") or "transcoding"
            msg = f"{stage}"
            if progress is not None:
                msg += f" · {progress}%"
            _append_preview_log(staging_dir_path, draft_id, msg,
                                progress=progress, status="processing")
        elif event_type in ("output.completed", "output.transferred"):
            output = event.get("output") or {}
            label = output.get("key") or output.get("format") or "output"
            _append_preview_log(staging_dir_path, draft_id,
                                f"output ready: {label}")
        elif event_type == "output.failed":
            output = event.get("output") or {}
            label = output.get("key") or output.get("format") or "output"
            _append_preview_log(staging_dir_path, draft_id,
                                f"output failed: {label}")

    try:
        if event_type == "job.completed":
            outputs = event.get("outputs", {})
            hls_cid = await process_completed_job(
                job=job,
                outputs=outputs,
                staging_dir=staging_dir,
                ipfs_api_url=settings.ipfs_api_url,
                pinata_jwt=settings.pinata_jwt,
            )

            if hls_cid:
                job["status"] = "complete"
                job["hlsCid"] = hls_cid
                job["completedAt"] = datetime.now(timezone.utc).isoformat()
                logger.info("[%s] Job complete! HLS CID: %s", job_id, hls_cid)
            else:
                job["status"] = "failed"
                job["error"] = "Failed to pin HLS output to IPFS"

        elif event_type == "job.failed":
            logger.error("[%s] Coconut job failed: %s", job_id, event.get("error"))
            job["status"] = "failed"
            job["error"] = event.get("error", "Unknown error")
            job["failedAt"] = datetime.now(timezone.utc).isoformat()

        save_job(staging_dir, job_id, job)

        # If this is a preview job, update the draft state
        if job.get("isPreview") and job.get("draftId"):
            _update_draft_preview(staging_dir, job)
        return {"received": True}

    except Exception as e:
        logger.error("[%s] Webhook processing error: %s", job_id, e)
        job["status"] = "failed"
        job["error"] = str(e)
        save_job(staging_dir, job_id, job)
        raise HTTPException(500, str(e))


@router.get("/job/{job_id}")
async def get_job_status(
    job_id: str,
    settings: Settings = Depends(get_settings),
):
    """Get transcoding job status."""
    job = load_job(Path(settings.staging_dir), job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs")
async def get_jobs(
    identity: str = Depends(require_auth),
    settings: Settings = Depends(get_settings),
):
    """List recent transcoding jobs."""
    jobs = list_jobs(Path(settings.staging_dir))
    return {"jobs": jobs}
