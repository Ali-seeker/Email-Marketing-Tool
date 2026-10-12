"""Smoke test checking basic application import and SAFE_BY_DEFAULT configuration."""

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app


def test_live_mode_defaults_to_false() -> None:
    """Verify that SAFE BY DEFAULT is active: LIVE_MODE must default to False."""
    settings = Settings(_env_file=None)
    assert settings.LIVE_MODE is False

    cached_settings = get_settings()
    # Even if environment has values, ensure default field definition is False
    assert Settings.model_fields["LIVE_MODE"].default is False
    assert cached_settings.LIVE_MODE is False


def test_app_health_endpoint() -> None:
    """Verify the FastAPI application imports cleanly and exposes live_mode=False on health."""
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["live_mode"] is False
