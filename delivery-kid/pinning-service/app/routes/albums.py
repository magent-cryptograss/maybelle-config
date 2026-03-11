"""Album and pin management routes."""

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_wallet_auth
from ..services import ipfs

router = APIRouter()


@router.get("/local-pins")
async def list_local_pins():
    """List all locally pinned CIDs. Public endpoint for build-time fetching."""
    cids = await ipfs.get_local_pins()
    # Return as objects with 'cid' property for arthel compatibility
    pins = [{"cid": cid} for cid in cids]
    return {"pins": pins, "count": len(pins), "node": "delivery-kid"}


@router.delete("/unpin/{cid}")
async def unpin_cid(
    cid: str,
    wallet_address: str = Depends(require_wallet_auth)
):
    """
    Unpin a CID from both local IPFS and Pinata.

    Requires wallet authentication.
    """
    result = await ipfs.unpin(cid)

    if not result.success:
        raise HTTPException(
            status_code=500,
            detail=f"Unpin failed: {result.error}"
        )

    return {
        "success": True,
        "cid": cid,
        "local_unpinned": result.local_unpinned,
        "pinata_unpinned": result.pinata_unpinned,
        "message": f"Unpinned {cid}"
    }
