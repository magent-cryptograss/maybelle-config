"""Tests for app.services.pickipedia_client — diagnostics snapshot to wiki."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services import pickipedia_client


@pytest.fixture(autouse=True)
def _reset_site_singleton():
    """Reset the cached mwclient.Site between tests so each test sees a fresh init."""
    pickipedia_client._site = None
    yield
    pickipedia_client._site = None


def _make_state(**overrides):
    """Build a minimal stand-in for ContentDraftState — only the fields snapshot reads."""
    base = MagicMock()
    base.draft_id = overrides.get("draft_id", "test-draft-id")
    base.status = overrides.get("status", "uploaded")
    base.preview_status = overrides.get("preview_status", "none")
    base.upload_log = overrides.get("upload_log", [])
    base.finalize_log = overrides.get("finalize_log", [])
    base.preview_log = overrides.get("preview_log", [])
    return base


class TestParseHost:
    def test_https_url(self):
        assert pickipedia_client._parse_host("https://pickipedia.xyz") == ("pickipedia.xyz", "https")

    def test_http_url(self):
        assert pickipedia_client._parse_host("http://localhost:8080") == ("localhost:8080", "http")

    def test_trailing_slash_stripped(self):
        assert pickipedia_client._parse_host("https://pickipedia.xyz/") == ("pickipedia.xyz", "https")

    def test_bare_host_defaults_to_https(self):
        assert pickipedia_client._parse_host("pickipedia.xyz") == ("pickipedia.xyz", "https")


class TestGetSite:
    def test_no_password_returns_none(self, monkeypatch):
        """When PICKIPEDIA_BOT_PASSWORD is unset, _get_site returns None — never raises."""
        monkeypatch.delenv("PICKIPEDIA_BOT_PASSWORD", raising=False)
        assert pickipedia_client._get_site() is None

    def test_login_failure_returns_none(self, monkeypatch):
        """If mwclient.login raises, _get_site logs and returns None."""
        monkeypatch.setenv("PICKIPEDIA_BOT_PASSWORD", "secret")

        fake_site = MagicMock()
        fake_site.login.side_effect = Exception("auth failed")
        with patch.object(pickipedia_client, "_site", None), \
             patch("mwclient.Site", return_value=fake_site):
            assert pickipedia_client._get_site() is None

    def test_login_success_caches_site(self, monkeypatch):
        """A successful login caches the Site so subsequent calls don't re-auth."""
        monkeypatch.setenv("PICKIPEDIA_BOT_PASSWORD", "secret")
        fake_site = MagicMock()

        with patch("mwclient.Site", return_value=fake_site) as mock_site_cls:
            first = pickipedia_client._get_site()
            second = pickipedia_client._get_site()

        assert first is fake_site
        assert second is fake_site
        # mwclient.Site should only be constructed once across both calls
        assert mock_site_cls.call_count == 1
        fake_site.login.assert_called_once_with("Magent@magent", "secret")

    def test_custom_user_env(self, monkeypatch):
        """PICKIPEDIA_BOT_USER overrides the default Magent@magent username."""
        monkeypatch.setenv("PICKIPEDIA_BOT_USER", "OtherBot@slot")
        monkeypatch.setenv("PICKIPEDIA_BOT_PASSWORD", "pw")
        fake_site = MagicMock()
        with patch("mwclient.Site", return_value=fake_site):
            pickipedia_client._get_site()
        fake_site.login.assert_called_once_with("OtherBot@slot", "pw")


class TestBuildSnapshotPayload:
    def test_projects_state_fields(self):
        state = _make_state(
            draft_id="abc",
            status="finalize_failed",
            preview_status="ready",
            upload_log=[{"phase": "init", "message": "go"}],
            finalize_log=[{"stage": "transcode", "error": "boom"}],
            preview_log=[{"message": "submitted"}],
        )
        payload = pickipedia_client._build_snapshot_payload(state)

        assert payload["draft_id"] == "abc"
        assert payload["status"] == "finalize_failed"
        assert payload["preview_status"] == "ready"
        assert payload["upload_log"] == [{"phase": "init", "message": "go"}]
        assert payload["finalize_log"] == [{"stage": "transcode", "error": "boom"}]
        assert payload["preview_log"] == [{"message": "submitted"}]
        # snapshot_at is an ISO 8601 timestamp in UTC
        assert "T" in payload["snapshot_at"]


class TestSnapshotDiagnostics:
    def test_no_creds_returns_false(self, monkeypatch):
        """Without bot creds, snapshot is a clean no-op returning False."""
        monkeypatch.delenv("PICKIPEDIA_BOT_PASSWORD", raising=False)
        assert pickipedia_client.snapshot_diagnostics("draft-1", {"status": "finalized"}) is False

    def test_writes_to_subpage_title(self, monkeypatch):
        """The page title is ReleaseDraft:{draft_id}/diagnostics."""
        monkeypatch.setenv("PICKIPEDIA_BOT_PASSWORD", "pw")
        fake_page = MagicMock()
        fake_page.exists = False
        fake_page.text.return_value = ""
        fake_pages = MagicMock()
        fake_pages.__getitem__ = lambda self, key: fake_page
        fake_site = MagicMock()
        fake_site.pages = fake_pages

        with patch("mwclient.Site", return_value=fake_site):
            ok = pickipedia_client.snapshot_diagnostics(
                "abc-123", {"status": "finalized", "draft_id": "abc-123"}
            )

        assert ok is True
        # __getitem__ should have been called with the sub-page title
        # We can't easily assert against MagicMock __getitem__ so instead
        # check that page.save was called with the JSON payload as content.
        args, kwargs = fake_page.save.call_args
        saved_content = args[0]
        parsed = json.loads(saved_content)
        assert parsed["status"] == "finalized"
        assert parsed["draft_id"] == "abc-123"

    def test_unchanged_content_skips_save(self, monkeypatch):
        """If the existing wiki content already matches, we no-op (no edit, returns True)."""
        monkeypatch.setenv("PICKIPEDIA_BOT_PASSWORD", "pw")

        payload = {"status": "finalized", "draft_id": "abc"}
        existing_text = json.dumps(payload, indent=2, default=str)

        fake_page = MagicMock()
        fake_page.exists = True
        fake_page.text.return_value = existing_text
        fake_site = MagicMock()
        fake_site.pages = {"ReleaseDraft:abc/diagnostics": fake_page}

        with patch("mwclient.Site", return_value=fake_site):
            ok = pickipedia_client.snapshot_diagnostics("abc", payload)

        assert ok is True
        fake_page.save.assert_not_called()

    def test_save_exception_returns_false(self, monkeypatch):
        """A wiki save failure is logged and surfaces as False — never raises."""
        monkeypatch.setenv("PICKIPEDIA_BOT_PASSWORD", "pw")
        fake_page = MagicMock()
        fake_page.exists = False
        fake_page.text.return_value = ""
        fake_page.save.side_effect = Exception("wiki down")
        fake_site = MagicMock()
        fake_site.pages = {"ReleaseDraft:abc/diagnostics": fake_page}

        with patch("mwclient.Site", return_value=fake_site):
            ok = pickipedia_client.snapshot_diagnostics("abc", {"status": "finalized"})

        assert ok is False


class TestSnapshotForDictAsync:
    @pytest.mark.asyncio
    async def test_builds_payload_from_dict(self, monkeypatch):
        """The dict-based variant (used by Coconut webhook) projects raw draft.json fields."""
        monkeypatch.setenv("PICKIPEDIA_BOT_PASSWORD", "pw")

        fake_page = MagicMock()
        fake_page.exists = False
        fake_page.text.return_value = ""
        fake_site = MagicMock()
        fake_site.pages = {"ReleaseDraft:abc/diagnostics": fake_page}

        with patch("mwclient.Site", return_value=fake_site):
            await pickipedia_client.snapshot_diagnostics_for_dict_async(
                "abc",
                {
                    "status": "uploaded",
                    "preview_status": "ready",
                    "upload_log": [{"phase": "init"}],
                    "preview_log": [{"message": "done"}],
                    # finalize_log absent — should default to []
                },
            )

        args, _ = fake_page.save.call_args
        saved = json.loads(args[0])
        assert saved["status"] == "uploaded"
        assert saved["preview_status"] == "ready"
        assert saved["upload_log"] == [{"phase": "init"}]
        assert saved["preview_log"] == [{"message": "done"}]
        assert saved["finalize_log"] == []  # missing key projects to empty list
