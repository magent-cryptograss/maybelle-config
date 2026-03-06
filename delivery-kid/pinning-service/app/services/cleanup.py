"""Draft cleanup service - removes expired drafts and manages staging space."""

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_draft_expiry(draft_dir: Path) -> Optional[datetime]:
    """
    Read the expiration time from a draft's draft.json file.

    Returns None if the file doesn't exist or can't be parsed.
    """
    draft_json = draft_dir / "draft.json"
    if not draft_json.exists():
        return None

    try:
        with open(draft_json) as f:
            data = json.load(f)
        expires_str = data.get("expires_at")
        if expires_str:
            # Handle both ISO format with and without timezone
            if expires_str.endswith("Z"):
                expires_str = expires_str[:-1] + "+00:00"
            return datetime.fromisoformat(expires_str)
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"Could not parse draft.json in {draft_dir}: {e}")

    return None


def cleanup_expired_drafts(staging_dir: Path) -> tuple[int, int]:
    """
    Remove all expired drafts from the staging directory.

    Returns tuple of (drafts_checked, drafts_removed).
    """
    drafts_dir = staging_dir / "drafts"
    if not drafts_dir.exists():
        return (0, 0)

    now = datetime.now(timezone.utc)
    checked = 0
    removed = 0

    for draft_dir in drafts_dir.iterdir():
        if not draft_dir.is_dir():
            continue

        checked += 1
        expires_at = get_draft_expiry(draft_dir)

        # Remove if expired or if we can't determine expiry (orphaned draft)
        should_remove = False
        if expires_at is None:
            # No valid draft.json - this is an orphaned directory
            # Check if it's old (created more than 24 hours ago based on mtime)
            try:
                mtime = datetime.fromtimestamp(draft_dir.stat().st_mtime, tz=timezone.utc)
                age_hours = (now - mtime).total_seconds() / 3600
                if age_hours > 24:
                    should_remove = True
                    logger.info(f"Removing orphaned draft: {draft_dir.name} (age: {age_hours:.1f}h)")
            except OSError:
                pass
        elif expires_at < now:
            should_remove = True
            logger.info(f"Removing expired draft: {draft_dir.name}")

        if should_remove:
            try:
                shutil.rmtree(draft_dir)
                removed += 1
            except OSError as e:
                logger.error(f"Failed to remove draft {draft_dir.name}: {e}")

    return (checked, removed)


def get_staging_size_gb(staging_dir: Path) -> float:
    """
    Calculate total size of the staging directory in gigabytes.
    """
    total_bytes = 0
    try:
        for path in staging_dir.rglob("*"):
            if path.is_file():
                total_bytes += path.stat().st_size
    except OSError:
        pass
    return total_bytes / (1024 ** 3)


async def periodic_cleanup(staging_dir: Path, interval_seconds: int = 3600):
    """
    Async task that periodically cleans up expired drafts.

    Args:
        staging_dir: Path to the staging directory
        interval_seconds: How often to run cleanup (default: 1 hour)
    """
    logger.info(f"Starting periodic cleanup task (interval: {interval_seconds}s)")

    while True:
        try:
            checked, removed = cleanup_expired_drafts(staging_dir)
            size_gb = get_staging_size_gb(staging_dir)

            if removed > 0 or checked > 0:
                logger.info(
                    f"Cleanup complete: checked={checked}, removed={removed}, "
                    f"staging_size={size_gb:.2f}GB"
                )
            else:
                logger.debug(f"Cleanup: no drafts to check, staging_size={size_gb:.2f}GB")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

        await asyncio.sleep(interval_seconds)


def startup_cleanup(staging_dir: Path) -> None:
    """
    Run cleanup synchronously at startup.

    Clears any expired drafts that accumulated while service was down.
    """
    logger.info("Running startup cleanup...")
    try:
        checked, removed = cleanup_expired_drafts(staging_dir)
        size_gb = get_staging_size_gb(staging_dir)
        logger.info(
            f"Startup cleanup complete: checked={checked}, removed={removed}, "
            f"staging_size={size_gb:.2f}GB"
        )
    except Exception as e:
        logger.error(f"Error during startup cleanup: {e}")
