"""Media transcoding - audio (FLAC to OGG) and video (to HLS)."""

import asyncio
import json
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable


@dataclass
class TranscodeResult:
    success: bool
    output_path: Optional[Path] = None
    error: Optional[str] = None
    # Rich metadata about what was produced
    transcode_info: Optional[dict] = field(default=None)


async def probe_video(path: Path) -> Optional[dict]:
    """Use ffprobe to get video file metadata."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        return json.loads(stdout.decode())
    except Exception:
        return None


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
    progress_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    trim_start: Optional[float] = None,
    trim_end: Optional[float] = None,
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
        ]
        # Trim: -ss before -i for fast seek, -to after -i for end time
        if trim_start is not None:
            cmd.extend(["-ss", str(trim_start)])
        cmd.extend([
            "-i", str(input_path),
        ])
        if trim_end is not None:
            # -to is relative to -ss when -ss is before -i
            if trim_start is not None:
                cmd.extend(["-to", str(trim_end - trim_start)])
            else:
                cmd.extend(["-to", str(trim_end)])
        cmd.extend([
            # Video: H.264 for broad compatibility
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",  # Force 8-bit — 10-bit breaks Firefox
            # Audio: AAC
            "-c:a", "aac",
            "-b:a", "128k",
            # HLS output
            "-f", "hls",
            "-hls_time", "6",
            "-hls_list_size", "0",  # Keep all segments in playlist
            "-hls_segment_filename", str(output_dir / "segment_%03d.ts"),
            str(master_playlist),
        ])

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

        # Gather transcode metadata
        segments = sorted(output_dir.glob("segment_*.ts"))
        segment_sizes = {s.name: s.stat().st_size for s in segments}
        total_output_size = sum(segment_sizes.values())

        # Probe the first segment for output codec details
        output_probe = await probe_video(segments[0]) if segments else None
        output_streams = {}
        if output_probe:
            for stream in output_probe.get("streams", []):
                if stream["codec_type"] == "video":
                    output_streams["video"] = {
                        "codec": stream.get("codec_name"),
                        "profile": stream.get("profile"),
                        "pix_fmt": stream.get("pix_fmt"),
                        "width": stream.get("width"),
                        "height": stream.get("height"),
                    }
                elif stream["codec_type"] == "audio":
                    output_streams["audio"] = {
                        "codec": stream.get("codec_name"),
                        "sample_rate": stream.get("sample_rate"),
                        "channels": stream.get("channels"),
                    }

        # Probe the source for input details
        source_probe = await probe_video(input_path)
        source_info = {}
        if source_probe:
            fmt = source_probe.get("format", {})
            source_info["duration_seconds"] = float(fmt.get("duration", 0))
            source_info["size_bytes"] = int(fmt.get("size", 0))
            source_info["format"] = fmt.get("format_long_name")
            for stream in source_probe.get("streams", []):
                if stream["codec_type"] == "video":
                    source_info["video_codec"] = stream.get("codec_name")
                    source_info["pix_fmt"] = stream.get("pix_fmt")
                    source_info["width"] = stream.get("width")
                    source_info["height"] = stream.get("height")
                elif stream["codec_type"] == "audio":
                    source_info["audio_codec"] = stream.get("codec_name")

        transcode_info = {
            "method": "local-ffmpeg",
            "output_codec": output_streams.get("video", {}).get("codec"),
            "output_pix_fmt": output_streams.get("video", {}).get("pix_fmt"),
            "output_width": output_streams.get("video", {}).get("width"),
            "output_height": output_streams.get("video", {}).get("height"),
            "output_audio_codec": output_streams.get("audio", {}).get("codec"),
            "segment_count": len(segments),
            "total_output_size_bytes": total_output_size,
            "source": source_info,
            "ffmpeg_settings": {
                "video_codec": "libx264",
                "preset": "medium",
                "crf": 23,
                "pix_fmt": "yuv420p",
                "audio_codec": "aac",
                "audio_bitrate": "128k",
                "hls_segment_duration": 6,
            },
        }

        return TranscodeResult(
            success=True,
            output_path=output_dir,
            transcode_info=transcode_info,
        )

    except Exception as e:
        return TranscodeResult(success=False, error=str(e))
