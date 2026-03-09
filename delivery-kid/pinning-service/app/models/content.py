"""Pydantic models for general content drafts (video, audio, arbitrary files)."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ContentFile(BaseModel):
    """Analyzed file information returned after upload."""
    original_filename: str
    detected_title: str
    media_type: str = Field(description="Media category: audio, video, image, or other")
    format: str = Field(description="Format name (MP4, WebM, FLAC, etc.)")
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
    size_bytes: int


class ContentDraftState(BaseModel):
    """Internal state of a content draft, saved to disk as draft.json."""
    draft_id: str
    draft_type: str = Field(default="content", description="Draft type: 'content' (vs 'album')")
    created_at: datetime
    expires_at: datetime
    uploaded_by: str = Field(description="Wallet address that created the draft")
    files: list[ContentFile]
    metadata: dict = Field(default_factory=dict, description="User-supplied metadata")


class ContentDraftResponse(BaseModel):
    """Response returned when creating or retrieving a content draft."""
    draft_id: str
    draft_type: str = "content"
    expires_at: datetime
    files: list[ContentFile]
    metadata: dict = Field(default_factory=dict)


class ContentFinalizeRequest(BaseModel):
    """Request body for finalizing a content draft."""
    title: Optional[str] = None
    description: Optional[str] = None
    file_type: Optional[str] = Field(default=None, description="MIME type override (e.g., video/webm)")
    metadata: dict = Field(default_factory=dict, description="Arbitrary metadata for Release page")
    transcode_hls: bool = Field(default=False, description="Transcode video to HLS before pinning")
    subsequent_to: Optional[str] = Field(default=None, description="CID this content supersedes")
