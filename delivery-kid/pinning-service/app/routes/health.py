"""Health check endpoints."""

import httpx
from fastapi import APIRouter

from ..config import get_settings

router = APIRouter()


@router.get("/health")
async def health_check():
    """Basic health check."""
    settings = get_settings()

    # Check IPFS connectivity
    ipfs_ok = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{settings.ipfs_api_url}/api/v0/id"
            )
            ipfs_ok = response.status_code == 200
    except Exception:
        ipfs_ok = False

    return {
        "status": "ok" if ipfs_ok else "degraded",
        "node": settings.node_name,
        "ipfs": "connected" if ipfs_ok else "disconnected"
    }


@router.get("/version")
async def version():
    """Return service version info."""
    import os
    return {
        "service": "delivery-kid-pinning",
        "commit": os.environ.get("GIT_COMMIT", "unknown"),
        "build_time": os.environ.get("BUILD_TIME", "unknown")
    }


@router.get("/time")
async def server_time():
    """Return current server time in milliseconds."""
    import time
    return {"time": int(time.time() * 1000)}
