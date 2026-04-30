"""IPFS pinning service - local kubo + Pinata backup."""

import httpx
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from ..config import get_settings


@dataclass
class PinResult:
    success: bool
    cid: Optional[str] = None
    error: Optional[str] = None
    pinata_success: bool = False


async def add_directory(directory_path: Path) -> PinResult:
    """
    Add a directory to IPFS and pin it.
    Returns the CID of the directory.
    """
    settings = get_settings()

    # Build multipart form for directory upload
    # IPFS API expects files with their relative paths
    files = []
    for file_path in directory_path.rglob("*"):
        if file_path.is_file():
            relative_path = file_path.relative_to(directory_path)
            files.append(
                ("file", (str(relative_path), open(file_path, "rb")))
            )

    if not files:
        return PinResult(success=False, error="No files in directory")

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            # Add files to IPFS with wrap-with-directory
            response = await client.post(
                f"{settings.ipfs_api_url}/api/v0/add",
                files=files,
                params={
                    "recursive": "true",
                    "wrap-with-directory": "true",
                    "pin": "true",
                }
            )

            if response.status_code != 200:
                return PinResult(
                    success=False,
                    error=f"IPFS add failed: {response.status_code} {response.text}"
                )

            # Response is newline-delimited JSON, last line is the directory
            lines = response.text.strip().split("\n")
            import json
            last_entry = json.loads(lines[-1])
            cid = last_entry.get("Hash")

            if not cid:
                return PinResult(success=False, error="No CID in IPFS response")

            # Pin to Pinata as backup
            pinata_success = False
            if settings.pinata_jwt:
                pinata_success = await pin_to_pinata(cid)

            return PinResult(
                success=True,
                cid=cid,
                pinata_success=pinata_success
            )

    except Exception as e:
        return PinResult(success=False, error=f"IPFS error: {e}")
    finally:
        # Close all file handles
        for _, (_, f) in files:
            f.close()


async def add_file(file_path: Path) -> PinResult:
    """Add a single file to IPFS and pin it."""
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            with open(file_path, "rb") as f:
                response = await client.post(
                    f"{settings.ipfs_api_url}/api/v0/add",
                    files={"file": (file_path.name, f)},
                    params={"pin": "true"}
                )

            if response.status_code != 200:
                return PinResult(
                    success=False,
                    error=f"IPFS add failed: {response.status_code}"
                )

            import json
            data = json.loads(response.text)
            cid = data.get("Hash")

            if not cid:
                return PinResult(success=False, error="No CID in response")

            # Pin to Pinata as backup
            pinata_success = False
            if settings.pinata_jwt:
                pinata_success = await pin_to_pinata(cid)

            return PinResult(
                success=True,
                cid=cid,
                pinata_success=pinata_success
            )

    except Exception as e:
        return PinResult(success=False, error=f"IPFS error: {e}")


async def pin_to_pinata(cid: str) -> bool:
    """Pin an existing CID to Pinata for redundancy."""
    settings = get_settings()

    if not settings.pinata_jwt:
        return False

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.pinata.cloud/pinning/pinByHash",
                headers={
                    "Authorization": f"Bearer {settings.pinata_jwt}",
                    "Content-Type": "application/json"
                },
                json={"hashToPin": cid}
            )
            return response.status_code == 200
    except Exception:
        return False


async def pin_cid(cid: str) -> PinResult:
    """Pin an existing CID to the local IPFS node."""
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{settings.ipfs_api_url}/api/v0/pin/add",
                params={"arg": cid}
            )
            if response.status_code == 200:
                return PinResult(success=True, cid=cid)
            else:
                return PinResult(
                    success=False,
                    error=f"IPFS pin/add failed: {response.status_code} {response.text[:100]}"
                )
    except Exception as e:
        return PinResult(success=False, error=str(e))


async def list_directory(cid: str) -> list[dict]:
    """
    List the immediate children of a directory CID.

    Returns a list of {name, cid, size, type} dicts. Type matches Kubo's
    UnixFS encoding: 1=Directory, 2=File. Empty list on any failure.
    """
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.ipfs_api_url}/api/v0/ls",
                params={"arg": cid},
            )
            if response.status_code != 200:
                return []
            import json
            data = json.loads(response.text)
            objects = data.get("Objects") or []
            if not objects:
                return []
            entries = []
            for link in objects[0].get("Links", []) or []:
                entries.append({
                    "name": link.get("Name", ""),
                    "cid": link.get("Hash", ""),
                    "size": link.get("Size", 0),
                    "type": link.get("Type", 0),
                })
            return entries
    except Exception:
        return []


async def get_local_pins() -> list[str]:
    """Get list of all locally pinned CIDs."""
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.ipfs_api_url}/api/v0/pin/ls",
                params={"type": "recursive"}
            )

            if response.status_code != 200:
                return []

            import json
            data = json.loads(response.text)
            return list(data.get("Keys", {}).keys())

    except Exception:
        return []


@dataclass
class UnpinResult:
    success: bool
    local_unpinned: bool = False
    pinata_unpinned: bool = False
    error: Optional[str] = None


async def unpin(cid: str) -> UnpinResult:
    """
    Unpin a CID from both local IPFS and Pinata.
    """
    settings = get_settings()
    local_unpinned = False
    pinata_unpinned = False
    errors = []

    # Unpin from local IPFS
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.ipfs_api_url}/api/v0/pin/rm",
                params={"arg": cid}
            )
            if response.status_code == 200:
                local_unpinned = True
            else:
                errors.append(f"Local unpin failed: {response.status_code} {response.text[:100]}")
    except Exception as e:
        errors.append(f"Local unpin error: {e}")

    # Unpin from Pinata
    if settings.pinata_jwt:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.delete(
                    f"https://api.pinata.cloud/pinning/unpin/{cid}",
                    headers={
                        "Authorization": f"Bearer {settings.pinata_jwt}"
                    }
                )
                if response.status_code == 200:
                    pinata_unpinned = True
                elif response.status_code == 404:
                    # Not pinned on Pinata, that's fine
                    pinata_unpinned = True
                else:
                    errors.append(f"Pinata unpin failed: {response.status_code}")
        except Exception as e:
            errors.append(f"Pinata unpin error: {e}")

    success = local_unpinned  # Consider success if local unpin worked
    return UnpinResult(
        success=success,
        local_unpinned=local_unpinned,
        pinata_unpinned=pinata_unpinned,
        error="; ".join(errors) if errors else None
    )
