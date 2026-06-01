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
    """Submit a video to Coconut V2 for transcoding.

    V2 schema (verified against api.coconut.co/v2/jobs — HTTP 201 on each):

        {
          "input": {"url": "..."},
          "storage": {"service": "coconut"},          # Coconut hosts outputs
          "outputs": {"<format-spec>": {"path": "..."}},
          "notification": {"type": "http", "url": "..."}
        }

    The ``<format-spec>`` key encodes the container/quality (``mp4``,
    ``mp4:480p``, ``httpstream``). Output values are thin wrappers around
    ``path``; nested codec/bitrate ``video``/``audio`` blocks from V1 are
    rejected as ``output_param_not_valid``.

    With ``storage.service: "coconut"`` Coconut hosts the output for a
    retention window and posts the URLs in the notification callback.

    Two job shapes today:
    - **Preview** (``include_preview=True``): single ``mp4`` output. Light,
      lets the wiki render an inline player on the ReleaseDraft page.
    - **Finalize** (default): single ``httpstream`` output → HLS playlist
      tree. Quality variant / trim / codec customization isn't yet exposed
      because the V2 schema for those inside ``httpstream`` is still
      undocumented and we get HTTP 201 on the minimal shape. Falls back to
      Coconut's defaults for now.

    Args:
        source_url: Public URL of the source video.
        api_key: Coconut API key.
        webhook_url: URL Coconut will POST to on completion.
        qualities: V1-era list of output heights (e.g. ``[720, 480]``).
            Currently ignored on V2 — logged as a warning when non-default.
        trim_start: V1-era trim offset seconds. Currently ignored on V2.
        trim_end: V1-era trim end seconds. Currently ignored on V2.
        include_preview: Switches between preview-mp4 and finalize-httpstream
            output shapes (see above).

    Returns:
        Coconut API response dict (job id, status, outputs array, etc.)

    Raises:
        httpx.HTTPStatusError: If Coconut rejects the job. The exception
            message includes Coconut's response body so the diagnostics
            panel surfaces the real reason instead of a generic
            ``Client error '400 Bad Request' for url ...``.
    """
    if include_preview:
        outputs = {"mp4": {"path": "/preview.mp4"}}
    else:
        outputs = {"httpstream": {"hls": {"path": "/hls"}}}
        if qualities or trim_start is not None or trim_end is not None:
            logger.warning(
                "submit_to_coconut: V2 finalize path doesn't yet pass "
                "qualities/trim through to Coconut — using V2 defaults. "
                "Ignored: qualities=%s trim_start=%s trim_end=%s",
                qualities, trim_start, trim_end,
            )

    job_config = {
        "input": {"url": source_url},
        "storage": {"service": "coconut"},
        "outputs": outputs,
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


def _normalize_outputs(outputs) -> list[dict]:
    """Normalise Coconut V1 dict-shape and V2 array-shape into a single
    list of ``{"key": ..., "url": ..., ...}`` entries.

    - V1 webhook callback: ``{"hls_av1_720p": {"url": ...}, ...}``
    - V2 webhook callback: ``[{"key": "httpstream", "url": ..., ...}]``

    Coconut's outputs schema changed when V2 rolled out; both shapes may
    appear in flight until we've fully purged V1 references. Tolerate
    either rather than failing closed.
    """
    if isinstance(outputs, dict):
        return [{"key": k, **(v if isinstance(v, dict) else {})}
                for k, v in outputs.items()]
    if isinstance(outputs, list):
        return [o for o in outputs if isinstance(o, dict)]
    return []


async def download_hls_outputs(
    outputs,
    hls_dir: Path,
) -> None:
    """Download HLS playlists and segments from Coconut output URLs.

    Accepts both the V1 dict shape (``{"hls_av1_720p": {"url": ...}}``) and
    the V2 list shape (``[{"key": "httpstream", "url": ...}]``). For V2's
    single ``httpstream`` output, expects ``url`` to point at the master
    playlist; we then chase variants + segments from there.

    Args:
        outputs: Coconut webhook ``outputs`` field — dict (V1) or list (V2).
        hls_dir: Local directory to write files into.
    """
    hls_dir.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_outputs(outputs)

    async with httpx.AsyncClient(timeout=120.0) as client:
        for output in normalized:
            key = output.get("key", "")
            url = output.get("url")
            if not url:
                continue

            if key == "httpstream":
                # V2: single httpstream output points at the master playlist.
                # Pull it, then chase variant playlists + segments.
                logger.info("Downloading httpstream master from %s", url)
                resp = await client.get(url)
                if not resp.is_success:
                    logger.warning("Failed to download httpstream master: %s",
                                   resp.status_code)
                    continue
                (hls_dir / "master.m3u8").write_text(resp.text)
                await _download_hls_variants_from_master(client, resp.text, url, hls_dir)
            elif key == "hls_master":
                # V1 legacy.
                logger.info("Downloading hls_master from %s", url)
                resp = await client.get(url)
                if resp.is_success:
                    (hls_dir / "master.m3u8").write_text(resp.text)
            elif key.startswith("hls_av1_"):
                # V1 legacy: per-variant playlist.
                quality = key.replace("hls_av1_", "").replace("p", "")
                quality_dir = hls_dir / f"{quality}p"
                quality_dir.mkdir(parents=True, exist_ok=True)
                local_path = quality_dir / "playlist.m3u8"
                logger.info("Downloading %s from %s", key, url)
                resp = await client.get(url)
                if resp.is_success:
                    local_path.write_text(resp.text)
                    await _download_segments(client, resp.text, url, quality_dir)


async def _download_hls_variants_from_master(
    client: httpx.AsyncClient,
    master_text: str,
    master_url: str,
    hls_dir: Path,
) -> None:
    """For V2 httpstream: parse master.m3u8, fetch each variant playlist
    referenced by ``#EXT-X-STREAM-INF``, then fetch each variant's segments.

    Master entries look like::
        #EXT-X-STREAM-INF:BANDWIDTH=...,RESOLUTION=1280x720,...
        720p/playlist.m3u8

    The line after STREAM-INF is the (possibly-relative) variant URL.
    """
    from urllib.parse import urljoin

    lines = master_text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        if i + 1 >= len(lines):
            continue
        variant_rel = lines[i + 1].strip()
        if not variant_rel or variant_rel.startswith("#"):
            continue
        variant_url = urljoin(master_url, variant_rel)
        # Use the directory part as the local subdir (e.g. "720p")
        variant_subdir = variant_rel.rsplit("/", 1)[0] if "/" in variant_rel else ""
        variant_dir = (hls_dir / variant_subdir) if variant_subdir else hls_dir
        variant_dir.mkdir(parents=True, exist_ok=True)
        variant_playlist_name = variant_rel.rsplit("/", 1)[-1]
        try:
            resp = await client.get(variant_url)
            if not resp.is_success:
                logger.warning("Failed to fetch variant %s: %s",
                               variant_rel, resp.status_code)
                continue
            (variant_dir / variant_playlist_name).write_text(resp.text)
            await _download_segments(client, resp.text, variant_url, variant_dir)
        except Exception as e:
            logger.warning("Error fetching variant %s: %s", variant_rel, e)


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
