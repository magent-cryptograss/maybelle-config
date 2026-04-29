"""Pydantic models for general content drafts (video, audio, arbitrary files)."""

import secrets
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
    creation_time: Optional[str] = Field(default=None, description="ISO 8601 creation time from container metadata")


class ContentDraftState(BaseModel):
    """Internal state of a content draft, saved to disk as draft.json."""
    draft_id: str
    draft_type: str = Field(default="content", description="Draft type: 'content' (vs 'album')")
    created_at: datetime
    expires_at: Optional[datetime] = None  # Legacy field, no longer used for expiry
    uploaded_by: str = Field(description="Wallet address that created the draft")
    files: list[ContentFile] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict, description="User-supplied metadata")
    # Lifecycle status. Persisted on disk so the wiki page can show what
    # happened even after a refresh or a process crash.
    #   awaiting_upload — /init called, no bytes yet
    #   uploading       — file POST in flight
    #   uploaded        — files saved + analyzed; preview may be running
    #   upload_failed   — file POST raised; see upload_log
    #   finalizing      — finalize SSE in progress
    #   finalized       — pin successful
    #   finalize_failed — finalize SSE errored; draft dir kept for forensics
    status: str = Field(default="uploaded", description="Draft lifecycle status")
    # Upload-stage progress trail (init, file received, analyzed, errored).
    upload_log: list[dict] = Field(default_factory=list,
                                   description="Upload-stage progress entries: [{ts, phase, message, error?}]")
    # Finalize-stage progress trail mirrored from the SSE generator so the
    # AV1/Coconut/local-fallback decision and any failure are visible after
    # the SSE connection closes.
    finalize_log: list[dict] = Field(default_factory=list,
                                     description="Finalize-stage progress entries: [{ts, stage, message, progress?, error?}]")
    # Preview transcoding (background, after upload)
    preview_token: str = Field(default_factory=lambda: secrets.token_urlsafe(32),
                               description="One-time token for Coconut to fetch source video from staging")
    preview_status: str = Field(default="none", description="none, pending, processing, ready, failed")
    preview_job_id: Optional[str] = Field(default=None, description="Coconut job ID for preview transcode")
    preview_cid: Optional[str] = Field(default=None, description="IPFS CID of AV1 HLS output")
    preview_mp4_cid: Optional[str] = Field(default=None, description="IPFS CID of 480p H.264 preview MP4")
    # Progress trail captured from Coconut webhook events. Surfaced to the
    # draft page by /draft-content so the user can see what's happening
    # during transcoding instead of staring at "Preview is being transcoded..."
    # for minutes. Capped at PREVIEW_LOG_MAX entries to keep draft.json small.
    preview_log: list[dict] = Field(default_factory=list,
                                    description="Recent progress entries: [{ts, message, progress?}]")


class ContentDraftResponse(BaseModel):
    """Response returned when creating or retrieving a content draft."""
    draft_id: str
    draft_type: str = "content"
    files: list[ContentFile] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    commit: str = Field(default="unknown", description="Git commit hash of the build that created this draft")
    status: str = Field(default="uploaded", description="Lifecycle status")
    upload_log: list[dict] = Field(default_factory=list, description="Upload-stage progress entries")
    finalize_log: list[dict] = Field(default_factory=list, description="Finalize-stage progress entries")
    preview_status: str = Field(default="none", description="none, pending, processing, ready, failed")
    preview_cid: Optional[str] = Field(default=None, description="IPFS CID of AV1 HLS output")
    preview_mp4_cid: Optional[str] = Field(default=None, description="IPFS CID of 480p preview MP4")
    preview_token: Optional[str] = Field(default=None, description="One-time token (returned only on init)")
    preview_log: list[dict] = Field(default_factory=list, description="Recent progress entries from Coconut webhook")


class ContentFinalizeRequest(BaseModel):
    """Request body for finalizing a content draft."""
    title: Optional[str] = None
    description: Optional[str] = None
    file_type: Optional[str] = Field(default=None, description="MIME type override (e.g., video/webm)")
    metadata: dict = Field(default_factory=dict, description="Arbitrary metadata for Release page")
    transcode_hls: bool = Field(default=False, description="Transcode video to HLS before pinning (legacy, use transcoding_strategy)")
    transcoding_strategy: str = Field(
        default="auto",
        description="Transcoding strategy for video: 'auto' (Coconut first, local fallback), 'coconut', 'local', 'none'"
    )
    subsequent_to: Optional[str] = Field(default=None, description="CID this content supersedes")
    transcoding_qualities: Optional[list[int]] = Field(
        default=None,
        description="Output video heights for HLS transcoding, e.g. [1080, 720, 480]. "
                    "Default [720, 480]. Common values: 2160 (4K), 1080, 720, 480, 360."
    )
    trim_start_seconds: Optional[float] = Field(
        default=None, description="Start time in seconds for trimming the video"
    )
    trim_end_seconds: Optional[float] = Field(
        default=None, description="End time in seconds for trimming the video"
    )
    preserve_original: bool = Field(
        default=False, description="Save the original source file to permanent storage instead of deleting it"
    )
