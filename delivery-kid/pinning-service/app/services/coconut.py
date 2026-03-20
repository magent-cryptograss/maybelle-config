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
) -> dict:
    """Submit a video to Coconut for AV1 HLS transcoding.

    Args:
        source_url: Public URL of the source video (IPFS gateway URL)
        api_key: Coconut API key
        webhook_url: URL Coconut will POST to on completion
        qualities: List of output heights for HLS variants. Each gets its
            own AV1+Opus stream. Common values: 2160 (4K), 1080, 720, 480, 360.
            Default [720, 480]. Higher values increase Coconut processing time
            and cost. Can be passed from the UI via ContentFinalizeRequest's
            transcoding_qualities field.

    Returns:
        Coconut API response dict
    """
    if qualities is None:
        qualities = [720, 480]

    # Build output config — AV1 video + Opus audio for each quality tier
    outputs = {}
    for q in qualities:
        key = f"hls_av1_{q}p"
        output = {
            "path": f"/output/{q}p/playlist.m3u8",
            "video": {
                "codec": "av1",
                "height": q,
                "bitrate": "4000k" if q >= 1080 else "2000k" if q >= 720 else "1000k",
            },
            "audio": {
                "codec": "opus",
                "bitrate": "128k",
            },
            "hls": {
                "segment_duration": 6,
            },
        }
        # Coconut trim: offset (start seconds) + duration (length seconds)
        if trim_start is not None:
            output["offset"] = trim_start
        if trim_start is not None and trim_end is not None:
            output["duration"] = trim_end - trim_start
        elif trim_end is not None:
            output["duration"] = trim_end
        outputs[key] = output

    # Master playlist
    outputs["hls_master"] = {
        "path": "/output/master.m3u8",
        "hls": {
            "master": True,
            "variants": [f"hls_av1_{q}p" for q in qualities],
        },
    }

    job_config = {
        "input": {"url": source_url},
        "outputs": outputs,
        "webhook": webhook_url,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            COCONUT_API_URL,
            json=job_config,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
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


async def process_completed_job(
    job: dict,
    outputs: dict,
    staging_dir: Path,
    ipfs_api_url: str,
    pinata_jwt: str = "",
) -> Optional[str]:
    """Process a completed Coconut job: download HLS, pin to IPFS.

    Returns the HLS directory CID, or None on failure.
    """
    job_id = job["id"]
    hls_dir = staging_dir / f"hls-{job_id}"

    try:
        # Download all HLS outputs
        await download_hls_outputs(outputs, hls_dir)

        # Pin to IPFS
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
