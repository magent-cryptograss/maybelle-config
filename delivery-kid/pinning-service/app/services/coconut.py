"""Coconut.co cloud transcoding — AV1 HLS via external API.

Submits videos to Coconut for AV1+Opus HLS transcoding (royalty-free codecs),
receives webhook on completion, downloads outputs, and pins to IPFS.
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Optional

import httpx

from . import ipfs

logger = logging.getLogger(__name__)

COCONUT_API_URL = "https://api.coconut.co/v2/jobs"


def _jobs_dir(staging_dir: Path) -> Path:
    d = staging_dir / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_job(staging_dir: Path, job_id: str, data: dict) -> None:
    path = _jobs_dir(staging_dir) / f"{job_id}.json"
    path.write_text(json.dumps(data, indent=2, default=str))


def load_job(staging_dir: Path, job_id: str) -> Optional[dict]:
    path = _jobs_dir(staging_dir) / f"{job_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def list_jobs(staging_dir: Path, limit: int = 50) -> list[dict]:
    """List recent jobs, newest first."""
    jobs_path = _jobs_dir(staging_dir)
    jobs = []
    for f in sorted(jobs_path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            jobs.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
        if len(jobs) >= limit:
            break
    return jobs


async def submit_to_coconut(
    source_url: str,
    api_key: str,
    webhook_url: str,
    qualities: list[int] | None = None,
    trim_start: float | None = None,
    trim_end: float | None = None,
    include_preview: bool = False,
) -> dict:
    """Submit a video to Coconut for transcoding.

    Coconut V2 schema (see https://docs.coconut.co/jobs/api):

        {
          "input": {"url": "..."},
          "storage": {"service": "coconut"},          # host outputs themselves
          "outputs": {"<format-spec>": {"path": "..."}},
          "notification": {"type": "http", "url": "..."}
        }

    The ``<format-spec>`` key encodes the container/quality (e.g. ``mp4``,
    ``mp4:480p``, ``httpstream``). Output values are thin wrappers around
    ``path``; codec/bitrate/audio customization is encoded into the key
    rather than nested ``video``/``audio`` blocks (which V1 used and V2
    rejects with ``output_param_not_valid``).

    With ``storage.service: "coconut"`` Coconut hosts the output for a
    retention window and gives us URLs in the notification callback.
    ``process_completed_job`` already expects this pattern (downloads each
    output URL and pins to IPFS), so no changes are needed downstream.

    **Status of the port:**
    - Preview path (``include_preview=True``) is V2-shape, single MP4
      output. Verified end-to-end with a real ``curl`` to V2 — 201 from
      Coconut.
    - HLS / finalize path (``qualities``, ``trim_start``, ``trim_end``)
      is not yet ported. V2 uses an ``httpstream`` output block whose
      exact codec/variant shape we still need to nail down — see the
      follow-up TODO. Raises NotImplementedError for now rather than
      silently sending V1-shape and 400-ing.

    Args:
        source_url: Public URL of the source video (IPFS gateway URL).
        api_key: Coconut API key.
        webhook_url: URL Coconut will POST to on completion.
        qualities: V1-era. Currently unsupported on V2 — pending HLS port.
        trim_start: V1-era. Currently unsupported on V2 — pending HLS port.
        trim_end: V1-era. Currently unsupported on V2 — pending HLS port.
        include_preview: If True, submit a single 480p MP4 preview job.
            This is the only supported shape today.

    Returns:
        Coconut API response dict (job id, status, etc.)

    Raises:
        NotImplementedError: If called for the HLS/finalize path until
            the ``httpstream`` block shape is ported.
        httpx.HTTPStatusError: If Coconut rejects the job. The exception
            message includes Coconut's response body so the diagnostics
            panel surfaces the real reason instead of a generic
            ``Client error '400 Bad Request' for url ...``.
    """
    if not include_preview:
        raise NotImplementedError(
            "Coconut V2 HLS via 'httpstream' output block is not yet ported. "
            "Only the preview MP4 path (include_preview=True) works today. "
            "See follow-up issue for the finalize-side HLS port."
        )

    # Preview path — minimal V2 shape verified against api.coconut.co/v2/jobs.
    job_config = {
        "input": {"url": source_url},
        "storage": {"service": "coconut"},
        "outputs": {"mp4": {"path": "/preview.mp4"}},
        "notification": {"type": "http", "url": webhook_url},
    }

    # Coconut V2 wants HTTP Basic Auth (API key as username, empty password),
    # not Bearer. With Bearer we got back "HTTP Basic: Access denied." 401s.
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            COCONUT_API_URL,
            json=job_config,
            auth=(api_key, ""),
            headers={"Content-Type": "application/json"},
        )
        if not resp.is_success:
            # Surface Coconut's actual error body — the V1 code lost this in
            # raise_for_status() and the diagnostics panel only got the
            # generic httpx message. Coconut's body is where the real reason
            # lives ("storage_service_not_valid", "output_param_not_valid",
            # "notification_not_valid", etc.).
            raise httpx.HTTPStatusError(
                f"Coconut {resp.status_code}: {resp.text}",
                request=resp.request,
                response=resp,
            )
        return resp.json()


async def download_hls_outputs(
    outputs: dict,
    hls_dir: Path,
) -> None:
    """Download HLS playlists and segments from Coconut output URLs.

    Args:
        outputs: Dict of output key -> {url: ...} from Coconut webhook
        hls_dir: Local directory to write files into
    """
    hls_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=120.0) as client:
        for key, output in outputs.items():
            url = output.get("url")
            if not url:
                continue

            if key == "hls_master":
                local_path = hls_dir / "master.m3u8"
            elif key.startswith("hls_av1_"):
                quality = key.replace("hls_av1_", "").replace("p", "")
                quality_dir = hls_dir / f"{quality}p"
                quality_dir.mkdir(parents=True, exist_ok=True)
                local_path = quality_dir / "playlist.m3u8"
            else:
                continue

            # Download playlist
            logger.info("Downloading %s from %s", key, url)
            resp = await client.get(url)
            if not resp.is_success:
                logger.warning("Failed to download %s: %s", key, resp.status_code)
                continue
            local_path.write_text(resp.text)

            # Download segments referenced in playlist
            if local_path.name.endswith(".m3u8") and key != "hls_master":
                await _download_segments(client, resp.text, url, local_path.parent)


async def _download_segments(
    client: httpx.AsyncClient,
    playlist_text: str,
    playlist_url: str,
    output_dir: Path,
) -> None:
    """Download .ts/.m4s segments referenced in an HLS playlist."""
    from urllib.parse import urljoin

    for line in playlist_text.splitlines():
        line = line.strip()
        if line.endswith(".ts") or line.endswith(".m4s"):
            segment_url = urljoin(playlist_url, line)
            segment_path = output_dir / line
            try:
                resp = await client.get(segment_url)
                if resp.is_success:
                    segment_path.write_bytes(resp.content)
                else:
                    logger.warning("Failed to download segment %s: %s", line, resp.status_code)
            except Exception as e:
                logger.warning("Error downloading segment %s: %s", line, e)


def _build_coconut_transcode_info(job: dict, outputs: dict, hls_dir: Path) -> dict:
    """Build transcode metadata from Coconut job results."""
    # Determine quality variants from output keys
    qualities = []
    for key in outputs:
        if key.startswith("hls_av1_") and key.endswith("p"):
            height = key.replace("hls_av1_", "").replace("p", "")
            try:
                qualities.append(int(height))
            except ValueError:
                pass
    qualities.sort(reverse=True)

    # Measure output sizes per quality
    variant_sizes = {}
    for q in qualities:
        q_dir = hls_dir / f"{q}p"
        if q_dir.exists():
            total = sum(f.stat().st_size for f in q_dir.iterdir() if f.is_file())
            segment_count = len(list(q_dir.glob("*.ts")) + list(q_dir.glob("*.m4s")))
            variant_sizes[f"{q}p"] = {
                "size_bytes": total,
                "segment_count": segment_count,
            }

    total_output_size = sum(v["size_bytes"] for v in variant_sizes.values())

    info = {
        "method": "coconut",
        "output_codec": "av1",
        "output_audio_codec": "opus",
        "qualities": [f"{q}p" for q in qualities],
        "variants": variant_sizes,
        "total_output_size_bytes": total_output_size,
        "coconut_settings": {
            "video_codec": "av1",
            "audio_codec": "opus",
            "audio_bitrate": "128k",
            "hls_segment_duration": 6,
        },
    }

    if job.get("previewCid"):
        info["preview_mp4_cid"] = job["previewCid"]

    return info


async def process_completed_job(
    job: dict,
    outputs: dict,
    staging_dir: Path,
    ipfs_api_url: str,
    pinata_jwt: str = "",
) -> Optional[str]:
    """Process a completed Coconut job: download HLS + preview, pin to IPFS.

    Returns the HLS directory CID, or None on failure.
    Also pins the preview MP4 if present and stores its CID in job["previewCid"].
    """
    job_id = job["id"]
    hls_dir = staging_dir / f"hls-{job_id}"

    try:
        # Download all HLS outputs
        await download_hls_outputs(outputs, hls_dir)

        # Download preview MP4 if present
        preview_output = outputs.get("mp4_preview", {})
        preview_url = preview_output.get("url")
        if preview_url:
            preview_path = staging_dir / f"preview-{job_id}.mp4"
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.get(preview_url)
                    if resp.is_success:
                        preview_path.write_bytes(resp.content)
                        logger.info("[%s] Preview MP4 downloaded (%d bytes)", job_id, len(resp.content))
                        # Pin preview to IPFS
                        preview_result = await ipfs.add_file(preview_path)
                        if preview_result.success:
                            job["previewCid"] = preview_result.cid
                            logger.info("[%s] Preview pinned: %s", job_id, preview_result.cid)
                        preview_path.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("[%s] Preview download/pin failed: %s", job_id, e)

        # Build and write transcode metadata before pinning
        transcode_info = _build_coconut_transcode_info(job, outputs, hls_dir)
        metadata = {
            "title": job.get("title"),
            "uploaded_by": job.get("identity"),
            "created_at": job.get("createdAt"),
            "transcode": transcode_info,
        }
        metadata = {k: v for k, v in metadata.items() if v is not None}
        metadata_path = hls_dir / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2, default=str))

        # Pin HLS to IPFS
        logger.info("[%s] Pinning HLS directory to IPFS...", job_id)
        result = await ipfs.add_directory(hls_dir)

        if not result.success:
            logger.error("[%s] IPFS pin failed: %s", job_id, result.error)
            return None

        logger.info("[%s] HLS pinned: %s", job_id, result.cid)
        return result.cid

    except Exception as e:
        logger.error("[%s] Error processing completed job: %s", job_id, e)
        return None

    finally:
        # Clean up temp directory
        shutil.rmtree(hls_dir, ignore_errors=True)
