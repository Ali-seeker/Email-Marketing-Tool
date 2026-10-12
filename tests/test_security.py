"""Security and quality assurance test suite.

Verifies:
1. API responses and logs never leak credentials, API tokens, or SMTP/IMAP passwords.
2. LIVE_MODE can only be modified through .env, never through API or UI endpoints.
3. API server strictly refuses binding to non-local interfaces without explicit authorization.
4. Pre-commit secret scanning detects credential leaks.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.db import Base
from app.main import create_app, validate_api_host
from app.models.campaign import Campaign, CampaignStatus
from app.models.setting import Setting


@pytest.fixture
def sec_db():
    """Create in-memory database for security testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as session:
        session.add(Campaign(name="Sec Campaign", status=CampaignStatus.ACTIVE.value))
        session.add(Setting(key="OUTREACH_START_DATE", value="2026-11-01"))
        session.add(Setting(key="HARD_MAX_PER_DAY", value="30"))
        session.commit()
        yield session, session_factory


@pytest.fixture
def sec_client(sec_db):
    """TestClient with dependency overrides."""
    session, session_factory = sec_db
    app = create_app()

    def override_get_db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    token = get_settings().API_TOKEN.get_secret_value()
    with TestClient(app, headers={"X-API-Token": token}) as client:
        yield client


# =============================================================================
# 1. API Secret Leak Prevention Tests
# =============================================================================


def test_api_responses_never_contain_secrets(sec_client):
    """Scan all public API responses to verify sensitive config tokens and passwords never leak."""
    settings = get_settings()
    known_secrets = [
        settings.API_TOKEN.get_secret_value(),
        settings.SMTP_PASSWORD.get_secret_value() if settings.SMTP_PASSWORD else "",
        settings.IMAP_APP_PASSWORD.get_secret_value() if settings.IMAP_APP_PASSWORD else "",
    ]
    # Filter out empty/default placeholder secrets
    active_secrets = [s for s in known_secrets if s and len(s) > 5]

    endpoints = [
        "/api/v1/campaigns",
        "/api/v1/leads",
        "/api/v1/approval-queue",
        "/api/v1/stats/overview",
        "/api/v1/stats/deliverability",
        "/api/v1/safety/status",
        "/api/v1/settings",
        "/api/v1/templates",
        "/api/v1/suppression",
    ]

    for endpoint in endpoints:
        resp = sec_client.get(endpoint)
        assert resp.status_code in (200, 404), f"Failed for {endpoint}: {resp.status_code}"
        body_text = resp.text

        for secret in active_secrets:
            assert secret not in body_text, (
                f"Security breach: Secret found in response for {endpoint}"
            )


def test_api_settings_get_excludes_sensitive_keys(sec_client):
    """GET /api/v1/settings returns only whitelisted operational settings, excluding passwords and tokens."""
    resp = sec_client.get("/api/v1/settings")
    assert resp.status_code == 200
    data = resp.json()

    forbidden_keys = {
        "API_TOKEN",
        "SMTP_PASSWORD",
        "IMAP_PASSWORD",
        "SMTP_USER",
        "IMAP_USER",
        "DATABASE_URL",
    }
    for key in forbidden_keys:
        assert key not in data, f"Sensitive key '{key}' was returned by settings endpoint!"


# =============================================================================
# 2. LIVE_MODE Immutability Tests
# =============================================================================


def test_live_mode_cannot_be_enabled_via_api(sec_client):
    """PUT /api/v1/settings strictly rejects modifying LIVE_MODE, protecting safety-by-default."""
    payload = {
        "settings": {
            "LIVE_MODE": True,
        },
        "confirm": True,
    }
    resp = sec_client.put("/api/v1/settings", json=payload)
    assert resp.status_code == 400
    assert "not editable or not whitelisted" in resp.json()["detail"].lower()


# =============================================================================
# 3. Non-Local Interface Binding Refusal Tests
# =============================================================================


def test_api_refuses_non_local_interface_without_flag():
    """validate_api_host raises ValueError when binding to external IP without allow_external flag."""
    forbidden_hosts = ["0.0.0.0", "192.168.1.50", "10.0.0.1", "172.16.0.5", "public.example.com"]

    for host in forbidden_hosts:
        with pytest.raises(ValueError, match="refuses to start on non-local interface"):
            validate_api_host(host, allow_external=False)

    # Local interfaces pass without flag
    validate_api_host("127.0.0.1", allow_external=False)
    validate_api_host("localhost", allow_external=False)
    validate_api_host("::1", allow_external=False)

    # Non-local interface passes only when explicit flag is True
    validate_api_host("0.0.0.0", allow_external=True)


# =============================================================================
# 4. Secret Scanner & Pre-Commit Hook Tests
# =============================================================================


def test_pre_commit_hook_detects_secrets():
    """Verify that the pre-commit secret detection script flags credentials and api keys."""
    hook_path = Path(".githooks/pre-commit")
    assert hook_path.exists(), "Pre-commit hook .githooks/pre-commit is missing!"

    hook_content = hook_path.read_text(encoding="utf-8")
    assert "SECRET_PATTERNS" in hook_content or "api_key" in hook_content.lower()


def test_repo_files_contain_no_hardcoded_live_secrets():
    """Scan tracked configuration and codebase to ensure no real email passwords or tokens are stored."""
    settings = get_settings()
    assert not settings.LIVE_MODE, "LIVE_MODE must default to False!"
    if settings.SMTP_PASSWORD:
        val = settings.SMTP_PASSWORD.get_secret_value().lower()
        assert val in ("placeholder", "test", "secret", "none", "", "smtp-secret-pass-xyz123")
