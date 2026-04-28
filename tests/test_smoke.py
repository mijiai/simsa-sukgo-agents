from starlette.testclient import TestClient

from src.config.settings import Settings, get_settings
from src.mcp.server import create_mcp_server


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.mcp_host == "0.0.0.0"
    assert settings.mcp_port == 8000
    assert settings.azure_storage_blob_container == "simsasukgo"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_health_endpoint_returns_ok() -> None:
    mcp = create_mcp_server()
    app = mcp.http_app(transport="sse")
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
