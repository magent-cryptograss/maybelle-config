"""Media file analysis using FFprobe."""

import asyncio
import json
import re
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AudioAnalysis:
    """Result of audio file analysis."""
    success: bool
    original_filename: str
    detected_title: str = ""
    format: str = ""
    duration_seconds: float = 0.0
    sample_rate: int = 0
    bit_depth: Optional[int] = None
    channels: int = 0
    size_bytes: int = 0
    error: Optional[str] = None


@dataclass
class MediaAnalysis:
    """Result of general media file analysis (audio, video, image, or other)."""
    success: bool
    original_filename: str
    detected_title: str = ""
    media_type: str = ""  # "audio", "video", "image", "other"
    format: str = ""
    duration_seconds: Optional[float] = None
    # Audio properties
    sample_rate: Optional[int] = None
    bit_depth: Optional[int] = None
    channels: Optional[int] = None
    # Video properties
    width: Optional[int] = None
    height: Optional[int] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    # Common
    size_bytes: int = 0
    creation_time: Optional[str] = None  # ISO 8601 from container metadata
    error: Optional[str] = None


def extract_title_from_filename(filename: str) -> str:
    """
    Extract a clean track title from a filename.

    Handles common patterns like:
    - "01 - Track Name.flac"
    - "01. Track Name.flac"
    - "01_Track_Name.flac"
    - "Track Name.flac"
    """
    # Remove extension
    name = Path(filename).stem

    # Remove leading track numbers with various separators
    # Patterns: "01 - ", "01. ", "01_", "01-", etc.
    name = re.sub(r'^\d+[\s._-]+', '', name)

    # Replace underscores with spaces
    name = name.replace('_', ' ')

    # Clean up multiple spaces
    name = re.sub(r'\s+', ' ', name).strip()

    return name or filename


def format_name_from_codec(codec_name: str) -> str:
    """Convert FFprobe codec name to friendly format name."""
    codec_map = {
        'flac': 'FLAC',
        'wav': 'WAV',
        'pcm_s16le': 'WAV',
        'pcm_s24le': 'WAV',
        'pcm_s32le': 'WAV',
        'mp3': 'MP3',
        'aac': 'AAC',
        'vorbis': 'OGG',
        'opus': 'OPUS',
        'alac': 'ALAC',
    }
    return codec_map.get(codec_name.lower(), codec_name.upper())


async def analyze_audio_file(file_path: Path) -> AudioAnalysis:
    """
    Analyze an audio file using FFprobe.

    Returns detailed metadata about the audio file.
    """
    if not file_path.exists():
        return AudioAnalysis(
            success=False,
            original_filename=file_path.name,
            error=f"File not found: {file_path}"
        )

    if not shutil.which("ffprobe"):
        return AudioAnalysis(
            success=False,
            original_filename=file_path.name,
            error="ffprobe not found"
        )

    try:
        # Run ffprobe to get JSON output
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(file_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "FFprobe failed"
            return AudioAnalysis(
                success=False,
                original_filename=file_path.name,
                error=error_msg
            )

        data = json.loads(stdout.decode())

        # Find audio stream
        audio_stream = None
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "audio":
                audio_stream = stream
                break

        if not audio_stream:
            return AudioAnalysis(
                success=False,
                original_filename=file_path.name,
                error="No audio stream found"
            )

        format_info = data.get("format", {})

        # Extract duration (prefer format duration, fall back to stream)
        duration = 0.0
        if "duration" in format_info:
            duration = float(format_info["duration"])
        elif "duration" in audio_stream:
            duration = float(audio_stream["duration"])

        # Extract sample rate
        sample_rate = int(audio_stream.get("sample_rate", 0))

        # Extract bit depth (not all formats have this)
        bit_depth = None
        if "bits_per_raw_sample" in audio_stream:
            bit_depth = int(audio_stream["bits_per_raw_sample"])
        elif "bits_per_sample" in audio_stream:
            bit_depth = int(audio_stream["bits_per_sample"])

        # Extract channels
        channels = int(audio_stream.get("channels", 0))

        # Get codec/format name
        codec_name = audio_stream.get("codec_name", "")
        format_name = format_name_from_codec(codec_name)

        # Get file size
        size_bytes = int(format_info.get("size", 0))
        if size_bytes == 0:
            size_bytes = file_path.stat().st_size

        return AudioAnalysis(
            success=True,
            original_filename=file_path.name,
            detected_title=extract_title_from_filename(file_path.name),
            format=format_name,
            duration_seconds=duration,
            sample_rate=sample_rate,
            bit_depth=bit_depth,
            channels=channels,
            size_bytes=size_bytes
        )

    except json.JSONDecodeError as e:
        return AudioAnalysis(
            success=False,
            original_filename=file_path.name,
            error=f"Failed to parse FFprobe output: {e}"
        )
    except Exception as e:
        return AudioAnalysis(
            success=False,
            original_filename=file_path.name,
            error=str(e)
        )


async def analyze_directory(directory: Path) -> list[AudioAnalysis]:
    """
    Analyze all audio files in a directory.

    Returns list of analysis results sorted by filename.
    """
    audio_extensions = {'.flac', '.wav', '.mp3', '.ogg', '.m4a', '.aac', '.opus'}

    audio_files = [
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in audio_extensions
    ]

    # Sort by filename for consistent ordering
    audio_files.sort(key=lambda f: f.name.lower())

    # Analyze all files concurrently
    results = await asyncio.gather(*[
        analyze_audio_file(f) for f in audio_files
    ])

    return list(results)


# --- General media analysis (audio + video + images) ---

VIDEO_EXTENSIONS = {'.mp4', '.webm', '.mov', '.mkv', '.avi', '.ts'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg'}
AUDIO_EXTENSIONS = {'.flac', '.wav', '.mp3', '.ogg', '.m4a', '.aac', '.opus'}

VIDEO_FORMAT_MAP = {
    'h264': 'H.264',
    'h265': 'H.265',
    'hevc': 'H.265',
    'vp8': 'VP8',
    'vp9': 'VP9',
    'av1': 'AV1',
    'theora': 'Theora',
}


def video_format_name(codec_name: str) -> str:
    """Convert FFprobe video codec name to friendly format name."""
    return VIDEO_FORMAT_MAP.get(codec_name.lower(), codec_name.upper())


def detect_media_type(file_path: Path) -> str:
    """Detect media type from file extension."""
    ext = file_path.suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    elif ext in AUDIO_EXTENSIONS:
        return "audio"
    elif ext in IMAGE_EXTENSIONS:
        return "image"
    return "other"


def container_format_name(file_path: Path) -> str:
    """Get a friendly container format name from extension."""
    ext_map = {
        '.mp4': 'MP4', '.webm': 'WebM', '.mov': 'MOV', '.mkv': 'MKV',
        '.avi': 'AVI', '.ts': 'MPEG-TS',
        '.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG',
        '.webp': 'WebP', '.gif': 'GIF', '.svg': 'SVG',
    }
    return ext_map.get(file_path.suffix.lower(), file_path.suffix.upper().lstrip('.'))


async def analyze_media_file(file_path: Path) -> MediaAnalysis:
    """
    Analyze any media file using FFprobe.

    Returns detailed metadata about audio, video, or image files.
    """
    if not file_path.exists():
        return MediaAnalysis(
            success=False,
            original_filename=file_path.name,
            error=f"File not found: {file_path}"
        )

    media_type = detect_media_type(file_path)

    # Images don't need FFprobe
    if media_type == "image":
        return MediaAnalysis(
            success=True,
            original_filename=file_path.name,
            detected_title=extract_title_from_filename(file_path.name),
            media_type="image",
            format=container_format_name(file_path),
            size_bytes=file_path.stat().st_size,
        )

    if not shutil.which("ffprobe"):
        return MediaAnalysis(
            success=False,
            original_filename=file_path.name,
            error="ffprobe not found"
        )

    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(file_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "FFprobe failed"
            return MediaAnalysis(
                success=False,
                original_filename=file_path.name,
                error=error_msg
            )

        data = json.loads(stdout.decode())
        format_info = data.get("format", {})

        # Find streams by type
        video_stream = None
        audio_stream = None
        for stream in data.get("streams", []):
            codec_type = stream.get("codec_type")
            if codec_type == "video" and not video_stream:
                video_stream = stream
            elif codec_type == "audio" and not audio_stream:
                audio_stream = stream

        # Duration
        duration = None
        if "duration" in format_info:
            duration = float(format_info["duration"])
        elif video_stream and "duration" in video_stream:
            duration = float(video_stream["duration"])
        elif audio_stream and "duration" in audio_stream:
            duration = float(audio_stream["duration"])

        # File size
        size_bytes = int(format_info.get("size", 0))
        if size_bytes == 0:
            size_bytes = file_path.stat().st_size

        result = MediaAnalysis(
            success=True,
            original_filename=file_path.name,
            detected_title=extract_title_from_filename(file_path.name),
            media_type=media_type,
            duration_seconds=duration,
            size_bytes=size_bytes,
        )

        # Creation time from container metadata (common in phone/camera video)
        tags = format_info.get("tags", {})
        creation_time = tags.get("creation_time") or tags.get("Creation_time")
        if creation_time:
            result.creation_time = creation_time

        # Video properties
        if video_stream:
            result.width = int(video_stream.get("width", 0)) or None
            result.height = int(video_stream.get("height", 0)) or None
            result.video_codec = video_format_name(video_stream.get("codec_name", ""))
            result.format = container_format_name(file_path)

        # Audio properties
        if audio_stream:
            result.audio_codec = format_name_from_codec(audio_stream.get("codec_name", ""))
            result.sample_rate = int(audio_stream.get("sample_rate", 0)) or None
            result.channels = int(audio_stream.get("channels", 0)) or None
            if "bits_per_raw_sample" in audio_stream:
                result.bit_depth = int(audio_stream["bits_per_raw_sample"])
            elif "bits_per_sample" in audio_stream:
                result.bit_depth = int(audio_stream["bits_per_sample"])
            # For audio-only files, use the codec as the format name
            if not video_stream:
                result.format = format_name_from_codec(audio_stream.get("codec_name", ""))

        return result

    except json.JSONDecodeError as e:
        return MediaAnalysis(
            success=False,
            original_filename=file_path.name,
            error=f"Failed to parse FFprobe output: {e}"
        )
    except Exception as e:
        return MediaAnalysis(
            success=False,
            original_filename=file_path.name,
            error=str(e)
        )


async def analyze_media_directory(directory: Path) -> list[MediaAnalysis]:
    """
    Analyze all media files in a directory.

    Returns list of analysis results sorted by filename.
    """
    all_extensions = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS | IMAGE_EXTENSIONS

    media_files = [
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in all_extensions
    ]

    media_files.sort(key=lambda f: f.name.lower())

    results = await asyncio.gather(*[
        analyze_media_file(f) for f in media_files
    ])

    return list(results)
