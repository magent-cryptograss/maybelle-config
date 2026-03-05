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
