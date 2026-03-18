"""Wallet signature and HMAC token authentication."""

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Optional

from eth_account.messages import encode_defunct
from eth_account import Account
from fastapi import Request, HTTPException, Depends

from .config import get_settings, Settings

logger = logging.getLogger(__name__)


def create_upload_token(api_key: str, username: str, timestamp: int, action: str = "upload") -> str:
    """Create an HMAC token for wiki-authenticated users.

    Args:
        api_key: Shared secret between wiki and delivery-kid.
        username: Wiki username.
        timestamp: Millisecond timestamp.
        action: Token action prefix — "upload" for staging, "finalize" for pinning.
    """
    message = f"{action}:{username}:{timestamp}"
    return hmac.new(api_key.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_upload_token(token: str, username: str, timestamp: int, settings: Settings, action: str = "upload") -> bool:
    """Verify an HMAC token.

    Args:
        token: The HMAC token to verify.
        username: Wiki username claimed.
        timestamp: Millisecond timestamp claimed.
        settings: App settings.
        action: Expected action prefix — "upload" or "finalize".
    """
    if not settings.api_key:
        logger.warning("HMAC verify failed: no api_key configured")
        return False
    expected = create_upload_token(settings.api_key, username, timestamp, action=action)
    if not hmac.compare_digest(token, expected):
        logger.warning("HMAC verify failed: token mismatch for user=%s action=%s", username, action)
        return False
    # Check timestamp freshness
    now_ms = int(time.time() * 1000)
    drift_ms = abs(now_ms - timestamp)
    max_drift_ms = settings.max_timestamp_drift_seconds * 1000
    if drift_ms > max_drift_ms:
        logger.warning(
            "HMAC verify failed: token expired. drift=%dms (max=%dms), "
            "token_ts=%d, server_now=%d, user=%s, action=%s",
            drift_ms, max_drift_ms, timestamp, now_ms, username, action
        )
        return False
    return True


@dataclass
class AuthResult:
    valid: bool
    address: Optional[str] = None
    error: Optional[str] = None


def create_auth_message(timestamp: int) -> str:
    """Create the message that must be signed for authorization."""
    return f"Authorize Blue Railroad pinning\nTimestamp: {timestamp}"


def verify_signature(signature: str, timestamp: int, settings: Settings) -> AuthResult:
    """
    Verify a signed authorization message.

    Args:
        signature: The signature (0x...)
        timestamp: The timestamp that was signed (milliseconds)
        settings: App settings

    Returns:
        AuthResult with validation status
    """
    now_ms = int(time.time() * 1000)
    drift_ms = abs(now_ms - timestamp)
    max_drift_ms = settings.max_timestamp_drift_seconds * 1000

    if drift_ms > max_drift_ms:
        return AuthResult(
            valid=False,
            error=f"Timestamp too old or too far in future (drift: {drift_ms // 1000}s, max: {settings.max_timestamp_drift_seconds}s)"
        )

    # Recover signer address from signature
    message = create_auth_message(timestamp)
    try:
        message_hash = encode_defunct(text=message)
        address = Account.recover_message(message_hash, signature=signature)
    except Exception as e:
        return AuthResult(valid=False, error=f"Invalid signature: {e}")

    # Check if address is authorized
    authorized = settings.authorized_wallet_list
    if not authorized:
        # Dev mode: empty list means allow all wallets
        return AuthResult(valid=True, address=address)

    if address.lower() not in authorized:
        return AuthResult(
            valid=False,
            address=address,
            error=f"Wallet {address} is not authorized"
        )

    return AuthResult(valid=True, address=address)


def _verify_hmac_headers(request: Request, settings: Settings, action: str = "upload") -> str | None:
    """Verify HMAC upload/finalize token from request headers.

    Returns identity string on success, None if headers not present.
    Raises HTTPException on invalid token.
    """
    upload_token = request.headers.get("X-Upload-Token")
    if not upload_token:
        return None

    username = request.headers.get("X-Upload-User", "")
    timestamp_str = request.headers.get("X-Upload-Timestamp", "")
    try:
        timestamp = int(timestamp_str)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=401,
            detail={"error": "Invalid upload timestamp"}
        )

    if not verify_upload_token(upload_token, username, timestamp, settings, action=action):
        raise HTTPException(
            status_code=401,
            detail={"error": "Invalid or expired upload token"}
        )
    return f"wiki:{username}"


async def require_wallet_auth(
    request: Request,
    settings: Settings = Depends(get_settings)
) -> str:
    """
    FastAPI dependency that requires valid wallet signature.
    Returns the verified wallet address.
    """
    signature = request.headers.get("X-Signature")
    timestamp_str = request.headers.get("X-Timestamp")

    if not signature or not timestamp_str:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Missing authentication headers",
                "required": ["X-Signature", "X-Timestamp"]
            }
        )

    try:
        timestamp = int(timestamp_str)
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail={"error": "Invalid timestamp format"}
        )

    result = verify_signature(signature, timestamp, settings)
    if not result.valid:
        raise HTTPException(status_code=401, detail={"error": result.error})

    return result.address


async def require_auth(
    request: Request,
    settings: Settings = Depends(get_settings)
) -> str:
    """
    FastAPI dependency that accepts HMAC upload token, API key, or wallet signature.

    HMAC token auth: X-Upload-Token + X-Upload-User + X-Upload-Timestamp headers.
      Wiki generates these for authenticated users to upload directly.
    API key auth: X-API-Key header + optional X-Uploaded-By for identity.
    Wallet auth: X-Signature + X-Timestamp headers (existing flow).

    Returns an identity string.
    """
    # 1. HMAC upload token (wiki-issued, for direct browser uploads)
    identity = _verify_hmac_headers(request, settings, action="upload")
    if identity:
        return identity

    # 2. API key (server-to-server)
    api_key = request.headers.get("X-API-Key")
    if api_key:
        if not settings.api_key:
            raise HTTPException(
                status_code=500,
                detail={"error": "API key auth not configured on server"}
            )
        if api_key != settings.api_key:
            raise HTTPException(
                status_code=401,
                detail={"error": "Invalid API key"}
            )
        return request.headers.get("X-Uploaded-By", "api-user")

    # 3. Wallet signature
    return await require_wallet_auth(request, settings)


async def require_finalize_auth(
    request: Request,
    settings: Settings = Depends(get_settings)
) -> str:
    """
    FastAPI dependency for finalization endpoints.

    Requires a finalize-prefixed HMAC token (issued only to users with
    finalize-release permission), an API key, or a wallet signature.

    Returns an identity string.
    """
    # 1. HMAC finalize token (wiki-issued, for finalize-release users)
    identity = _verify_hmac_headers(request, settings, action="finalize")
    if identity:
        return identity

    # 2. API key (server-to-server — always allowed to finalize)
    api_key = request.headers.get("X-API-Key")
    if api_key:
        if not settings.api_key:
            raise HTTPException(
                status_code=500,
                detail={"error": "API key auth not configured on server"}
            )
        if api_key != settings.api_key:
            raise HTTPException(
                status_code=401,
                detail={"error": "Invalid API key"}
            )
        return request.headers.get("X-Uploaded-By", "api-user")

    # 3. Wallet signature
    return await require_wallet_auth(request, settings)
