"""Wallet signature and HMAC token authentication."""

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Optional

from eth_account.messages import encode_defunct
from eth_account import Account
from fastapi import Request, HTTPException, Depends

from .config import get_settings, Settings


def create_upload_token(api_key: str, username: str, timestamp: int) -> str:
    """Create an HMAC upload token for wiki-authenticated users."""
    message = f"upload:{username}:{timestamp}"
    return hmac.new(api_key.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify_upload_token(token: str, username: str, timestamp: int, settings: Settings) -> bool:
    """Verify an HMAC upload token."""
    if not settings.api_key:
        return False
    expected = create_upload_token(settings.api_key, username, timestamp)
    if not hmac.compare_digest(token, expected):
        return False
    # Check timestamp freshness
    now_ms = int(time.time() * 1000)
    drift_ms = abs(now_ms - timestamp)
    max_drift_ms = settings.max_timestamp_drift_seconds * 1000
    return drift_ms <= max_drift_ms


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
    upload_token = request.headers.get("X-Upload-Token")
    if upload_token:
        username = request.headers.get("X-Upload-User", "")
        timestamp_str = request.headers.get("X-Upload-Timestamp", "")
        try:
            timestamp = int(timestamp_str)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=401,
                detail={"error": "Invalid upload timestamp"}
            )
        if not verify_upload_token(upload_token, username, timestamp, settings):
            raise HTTPException(
                status_code=401,
                detail={"error": "Invalid or expired upload token"}
            )
        return f"wiki:{username}"

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
