"""Wallet signature authentication."""

import time
from dataclasses import dataclass
from typing import Optional

from eth_account.messages import encode_defunct
from eth_account import Account
from fastapi import Request, HTTPException, Depends

from .config import get_settings, Settings


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
    FastAPI dependency that accepts either API key or wallet signature.

    API key auth: X-API-Key header + optional X-Uploaded-By for identity.
    Wallet auth: X-Signature + X-Timestamp headers (existing flow).

    Returns an identity string (wallet address or X-Uploaded-By value).
    """
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

    # Fall back to wallet auth
    return await require_wallet_auth(request, settings)
