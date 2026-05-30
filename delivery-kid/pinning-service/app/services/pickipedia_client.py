"""Pickipedia bot client — snapshots draft diagnostics to a wiki sub-page.

When delivery-kid's filesystem is wiped (rebuild, container churn), the
``draft.json`` for each in-flight upload goes with it. The wiki side then
loses the upload/finalize/preview log trail that was the whole point of
PR #81's diagnostics panel. To survive that, we mirror the logs onto a
``ReleaseDraft:{id}/diagnostics`` wiki sub-page at each terminal state
transition. The wiki page outlives delivery-kid's storage, and the
diagnostics-panel JS falls back to it whenever the live ``/draft-content``
fetch returns 404.

The bot identity reused here is the same Magent@magent BotPassword that
the Blue Railroad import tool already uses (configured via
``PICKIPEDIA_BOT_USER`` and ``PICKIPEDIA_BOT_PASSWORD`` env vars). When
those are unset (typical in dev) every snapshot call no-ops cleanly.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-initialised mwclient.Site singleton. Cached because each
# ``site.login()`` round-trips the wiki and we don't want to do that on
# every snapshot. ``_site_lock`` guards the init race when several
# webhooks fire concurrently.
_site = None
_site_lock = Lock()


def _parse_host(url: str) -> tuple[str, str]:
    """Split a wiki URL into (host, scheme) for mwclient.Site."""
    if url.startswith("https://"):
        return url[8:].rstrip("/"), "https"
    if url.startswith("http://"):
        return url[7:].rstrip("/"), "http"
    return url.rstrip("/"), "https"


def _get_site():
    """Return a logged-in mwclient.Site, or None if creds aren't configured."""
    global _site
    if _site is not None:
        return _site

    user = os.environ.get("PICKIPEDIA_BOT_USER", "Magent@magent")
    password = os.environ.get("PICKIPEDIA_BOT_PASSWORD")
    if not password:
        return None

    url = os.environ.get("PICKIPEDIA_URL", "https://pickipedia.xyz")
    host, scheme = _parse_host(url)

    with _site_lock:
        if _site is not None:
            return _site
        try:
            import mwclient
            site = mwclient.Site(host, scheme=scheme)
            site.login(user, password)
            _site = site
            logger.info("pickipedia_client: authenticated to %s as %s", host, user)
            return site
        except Exception as e:
            logger.error("pickipedia_client: authentication to %s failed: %s", host, e)
            return None


def _build_snapshot_payload(state) -> dict:
    """Project a ContentDraftState into the JSON payload we write to the wiki.

    Kept deliberately flat so the wiki-side renderer can consume it with
    the same shape it gets from ``/draft-content``.
    """
    return {
        "draft_id": state.draft_id,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "status": state.status,
        "preview_status": state.preview_status,
        "upload_log": state.upload_log,
        "finalize_log": state.finalize_log,
        "preview_log": state.preview_log,
    }


def snapshot_diagnostics(draft_id: str, payload: dict) -> bool:
    """Write the diagnostics payload to ``ReleaseDraft:{id}/diagnostics``.

    Returns True if the write succeeded (or the page already had identical
    content), False if creds are missing or the wiki call failed. Never
    raises — terminal-state hooks call this fire-and-forget; we don't want
    a wiki blip to mask the underlying upload outcome.
    """
    site = _get_site()
    if site is None:
        return False

    title = f"ReleaseDraft:{draft_id}/diagnostics"
    content = json.dumps(payload, indent=2, default=str)
    summary = f"diagnostics snapshot — status={payload.get('status', 'unknown')}"

    try:
        page = site.pages[title]
        existing = page.text() if page.exists else None
        if existing == content:
            return True
        page.save(content, summary=summary)
        logger.info("pickipedia_client: snapshotted %s (%d bytes)", title, len(content))
        return True
    except Exception as e:
        logger.error("pickipedia_client: snapshot failed for %s: %s", title, e)
        return False


async def snapshot_diagnostics_for_state_async(state) -> bool:
    """Async fire-and-forget snapshot from a ContentDraftState.

    Runs the sync mwclient call in a thread pool so we don't block the
    FastAPI event loop. Used by terminal-state hooks in the routes.
    """
    payload = _build_snapshot_payload(state)
    return await asyncio.to_thread(snapshot_diagnostics, state.draft_id, payload)


def snapshot_diagnostics_for_dict_async(draft_id: str, draft_data: dict):
    """Async fire-and-forget snapshot from a raw draft.json dict.

    Used by the Coconut webhook path, which mutates ``draft.json`` on disk
    rather than holding a ContentDraftState object. Returns the asyncio
    Task so the caller can ``create_task(...)`` it without awaiting.
    """
    payload = {
        "draft_id": draft_id,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "status": draft_data.get("status"),
        "preview_status": draft_data.get("preview_status"),
        "upload_log": draft_data.get("upload_log") or [],
        "finalize_log": draft_data.get("finalize_log") or [],
        "preview_log": draft_data.get("preview_log") or [],
    }
    return asyncio.to_thread(snapshot_diagnostics, draft_id, payload)
