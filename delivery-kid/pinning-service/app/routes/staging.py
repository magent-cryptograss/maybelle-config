"""Serve staged draft files for preview (e.g., video embed on ReleaseDraft pages).

Files are served from the staging directory at /drafts/{draft_id}/upload/{filename}.
Requires a valid upload token (any logged-in wiki user). Does NOT check draft
ownership — the unguessable UUID is sufficient access control for preview.

Supports HTTP range requests for video seeking via FastAPI's FileResponse.

Auth can be provided via headers (standard require_auth flow) OR via query
parameters (?token=...&user=...&timestamp=...) so that <video src="...">
tags work without JavaScript fetch gymnastics.
"""

import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ..auth import require_auth, verify_upload_token
from ..config import get_settings, Settings

router = APIRouter(prefix="/staging", tags=["staging"])

# Ensure common media types are registered (Python's default registry
# misses some of these on minimal Linux installs)
_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}
for _ext, _mime in _MEDIA_TYPES.items():
    mimetypes.add_type(_mime, _ext)


async def require_staging_auth(
    request: Request,
    token: Optional[str] = Query(None),
    user: Optional[str] = Query(None),
    timestamp: Optional[str] = Query(None),
    settings: Settings = Depends(get_settings),
) -> str:
    """Authenticate via headers (standard) or query params (for <video src>).

    Query param auth uses the same HMAC verification as header auth,
    just sourced from ?token=&user=&timestamp= instead of X-Upload-* headers.
    """
    # Try header auth first (X-Upload-Token, X-API-Key, or X-Signature).
    # If it fails, fall through to query param auth below.
    try:
        return await require_auth(request, settings)
    except HTTPException:
        pass

    # Fall back to query param auth
    if token and user and timestamp:
        try:
            ts = int(timestamp)
        except (ValueError, TypeError):
            raise HTTPException(status_code=401, detail="Invalid timestamp")

        if verify_upload_token(token, user, ts, settings, action="upload"):
            return f"wiki:{user}"

    raise HTTPException(
        status_code=401,
        detail="Authentication required (via headers or query params)"
    )


@router.get("/drafts/{draft_id}/{filename}")
async def get_staging_file(
    draft_id: str,
    filename: str,
    identity: str = Depends(require_staging_auth),
    settings: Settings = Depends(get_settings),
):
    """Serve a file from a staging draft for preview.

    Used by the ReleaseDraft page to embed video/audio players.
    """
    # Sanitize path components to prevent traversal
    if ".." in draft_id or "/" in draft_id or ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid path")

    file_path = Path(settings.staging_dir) / "drafts" / draft_id / "upload" / filename

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Verify the resolved path is still within staging (belt-and-suspenders)
    try:
        file_path.resolve().relative_to(Path(settings.staging_dir).resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    return FileResponse(
        path=file_path,
        media_type=content_type,
        filename=filename,
    )
