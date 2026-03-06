"""Audio file analysis using FFprobe."""

import asyncio
import json
import re
import shutil
from pathlib import Path
from dataclasses import dataclass
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
