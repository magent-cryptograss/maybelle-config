"""Pydantic models for multi-step album upload drafts."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class DraftFile(BaseModel):
    """Analyzed file information returned after upload."""
    original_filename: str
    detected_title: str
    format: str = Field(description="Audio format (FLAC, WAV, MP3, OGG, etc.)")
    duration_seconds: float
    sample_rate: int
    bit_depth: Optional[int] = None  # Not all formats have bit depth
    channels: int
    size_bytes: int


class DraftState(BaseModel):
    """Internal state of a draft album, saved to disk as draft.json."""
    draft_id: str
    created_at: datetime
    expires_at: datetime
    uploaded_by: str = Field(description="Wallet address that created the draft")
    files: list[DraftFile]


class DraftResponse(BaseModel):
    """Response returned when creating or retrieving a draft."""
    draft_id: str
    expires_at: datetime
    files: list[DraftFile]
    commit: str = Field(default="unknown", description="Git commit hash of the build that created this draft")


class FinalizeTrack(BaseModel):
    """Track information for finalization request."""
    filename: str = Field(description="Original filename to identify the track")
    title: str = Field(description="User-edited title for the track")
    tags: Optional[dict[str, str]] = Field(default=None, description="Per-track tags (COMPOSER, COMMENT, etc.)")


class FinalizeRequest(BaseModel):
    """Request body for finalizing a draft album."""
    album_title: str
    artist: str
    year: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[dict[str, str]] = Field(default=None, description="Custom tags to embed in audio files (KEY=VALUE)")
    tracks: list[FinalizeTrack] = Field(description="Tracks in desired order")
