"""Media transcoding - audio (FLAC to OGG) and video (to HLS)."""

import asyncio
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable


@dataclass
class TranscodeResult:
    success: bool
    output_path: Optional[Path] = None
    error: Optional[str] = None


async def transcode_flac_to_ogg(
    input_path: Path,
    output_path: Path,
    quality: int = 6,
    metadata: Optional[dict[str, str]] = None,
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None
) -> TranscodeResult:
    """
    Transcode a FLAC file to OGG Vorbis.

    Args:
        input_path: Path to input FLAC file
        output_path: Path for output OGG file
        quality: OGG quality (0-10, default 6 ≈ 192kbps)
        metadata: Optional dict of metadata tags to embed (KEY: VALUE)
        progress_callback: Optional async callback for progress updates

    Returns:
        TranscodeResult with success status and output path
    """
    if not input_path.exists():
        return TranscodeResult(success=False, error=f"Input file not found: {input_path}")

    # Check for ffmpeg
    if not shutil.which("ffmpeg"):
        return TranscodeResult(success=False, error="ffmpeg not found")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        await progress_callback(f"Transcoding {input_path.name}")

    try:
        # Build ffmpeg command
        cmd = [
            "ffmpeg",
            "-i", str(input_path),
            "-c:a", "libvorbis",
            "-q:a", str(quality),
        ]

        # Add metadata tags
        if metadata:
            for key, value in metadata.items():
                cmd.extend(["-metadata", f"{key}={value}"])

        cmd.extend([
            "-y",  # Overwrite output
            str(output_path)
        ])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown ffmpeg error"
            return TranscodeResult(success=False, error=error_msg)

        if not output_path.exists():
            return TranscodeResult(success=False, error="Output file not created")

        return TranscodeResult(success=True, output_path=output_path)

    except Exception as e:
        return TranscodeResult(success=False, error=str(e))


async def transcode_album_directory(
    input_dir: Path,
    output_dir: Path,
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None
) -> tuple[bool, list[Path], list[str]]:
    """
    Transcode all FLAC files in a directory to OGG.

    Args:
        input_dir: Directory containing FLAC files
        output_dir: Directory for OGG output
        progress_callback: Optional callback for progress updates

    Returns:
        Tuple of (success, list of output paths, list of errors)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    flac_files = sorted(input_dir.glob("*.flac"))
    if not flac_files:
        flac_files = sorted(input_dir.glob("*.FLAC"))

    if not flac_files:
        return (False, [], ["No FLAC files found in directory"])

    outputs = []
    errors = []

    for i, flac_path in enumerate(flac_files):
        ogg_name = flac_path.stem + ".ogg"
        ogg_path = output_dir / ogg_name

        if progress_callback:
            await progress_callback(f"Transcoding {i+1}/{len(flac_files)}: {flac_path.name}")

        result = await transcode_flac_to_ogg(flac_path, ogg_path)

        if result.success and result.output_path:
            outputs.append(result.output_path)
        else:
            errors.append(f"{flac_path.name}: {result.error}")

    success = len(outputs) > 0 and len(errors) == 0
    return (success, outputs, errors)


async def transcode_video_to_hls(
    input_path: Path,
    output_dir: Path,
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None
) -> TranscodeResult:
    """
    Transcode a video file to HLS (HTTP Live Streaming) format.

    Creates a directory with master.m3u8 and segment files,
    suitable for streaming via IPFS gateway.

    Args:
        input_path: Path to input video file
        output_dir: Directory to write HLS output (master.m3u8 + segments)
        progress_callback: Optional async callback for progress updates

    Returns:
        TranscodeResult with success status and output directory path
    """
    if not input_path.exists():
        return TranscodeResult(success=False, error=f"Input file not found: {input_path}")

    if not shutil.which("ffmpeg"):
        return TranscodeResult(success=False, error="ffmpeg not found")

    output_dir.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        await progress_callback(f"Transcoding {input_path.name} to HLS")

    try:
        # Build ffmpeg HLS command
        # - Multiple quality renditions for adaptive streaming
        # - 6-second segments
        # - master playlist
        master_playlist = output_dir / "master.m3u8"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            # Video: H.264 for broad compatibility
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            # Audio: AAC
            "-c:a", "aac",
            "-b:a", "128k",
            # HLS output
            "-f", "hls",
            "-hls_time", "6",
            "-hls_list_size", "0",  # Keep all segments in playlist
            "-hls_segment_filename", str(output_dir / "segment_%03d.ts"),
            str(master_playlist),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        _, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown ffmpeg error"
            return TranscodeResult(success=False, error=error_msg)

        if not master_playlist.exists():
            return TranscodeResult(success=False, error="master.m3u8 not created")

        return TranscodeResult(success=True, output_path=output_dir)

    except Exception as e:
        return TranscodeResult(success=False, error=str(e))
