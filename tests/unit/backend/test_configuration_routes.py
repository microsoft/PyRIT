# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for backend configuration file routes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core.exceptions import AzureError
from fastapi import HTTPException, status
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.middleware.auth import AuthenticatedUser, require_admin
from pyrit.backend.models.configuration import EnvironmentFileContent
from pyrit.backend.routes.configuration import (
    _audit_configuration_access,
    _get_configuration_file_service,
    _get_environment_file_service,
)
from pyrit.backend.services.configuration_file_service import ConfigurationFileConflictError, ConfigurationFileService
from pyrit.backend.services.environment_file_service import EnvironmentFileService


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI app."""
    app.dependency_overrides[require_admin] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_configuration_services_resolve_from_application_state() -> None:
    """Test service resolution from startup state and environment fallback."""
    request = MagicMock()
    configuration_service = ConfigurationFileService(config_file_value="config.yaml")
    environment_service = EnvironmentFileService(resolved_env_files=[])
    request.app.state.configuration_file_service = configuration_service
    request.app.state.environment_file_service = environment_service

    assert _get_configuration_file_service(request) is configuration_service
    assert _get_environment_file_service(request) is environment_service

    request.app.state.configuration_file_service = None
    with patch.dict("os.environ", {"PYRIT_CONFIG_FILE": "fallback.yaml"}):
        assert _get_configuration_file_service(request).source == "fallback.yaml"


def test_get_environment_file_service_raises_when_unavailable() -> None:
    """Test that missing startup state produces a service-unavailable response."""
    request = MagicMock()
    request.app.state.environment_file_service = None

    with pytest.raises(HTTPException) as exc_info:
        _get_environment_file_service(request)

    assert exc_info.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_require_admin_allows_admin_user() -> None:
    """Test that authenticated administrators can use configuration routes."""
    request = MagicMock()
    request.state.user = AuthenticatedUser(
        oid="admin-1",
        name="Admin User",
        email="admin@example.com",
        groups=["admin-group"],
        is_admin=True,
    )

    require_admin(request)


def test_require_admin_rejects_non_admin_user() -> None:
    """Test that authenticated non-administrators cannot use configuration routes."""
    request = MagicMock()
    request.state.user = AuthenticatedUser(
        oid="user-1",
        name="Test User",
        email="test@example.com",
        groups=["allowed-group"],
    )

    with pytest.raises(HTTPException, match="Administrator access is required") as exc_info:
        require_admin(request)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_get_configuration_file_returns_content(client: TestClient) -> None:
    """Test reading configuration contents through the API."""
    service = MagicMock(spec=ConfigurationFileService)
    service.read_with_version_async = AsyncMock(return_value=("operator: alice\n", "version-1"))
    service.source = "C:/Users/test/.pyrit/config.yaml"
    with patch("pyrit.backend.routes.configuration._get_configuration_file_service", return_value=service):
        response = client.get("/api/config")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "content": "operator: alice\n",
        "source": "C:/Users/test/.pyrit/config.yaml",
        "version": "version-1",
    }


def test_update_configuration_file_persists_content(client: TestClient) -> None:
    """Test replacing configuration contents through the API."""
    service = MagicMock(spec=ConfigurationFileService)
    service.update_async = AsyncMock(return_value="version-2")
    service.source = "https://account.blob.core.windows.net/config/config.yaml"
    with patch("pyrit.backend.routes.configuration._get_configuration_file_service", return_value=service):
        response = client.put("/api/config", json={"content": "operator: bob\n", "version": "version-1"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "content": "operator: bob\n",
        "source": "https://account.blob.core.windows.net/config/config.yaml",
        "version": "version-2",
    }
    service.update_async.assert_awaited_once_with("operator: bob\n", expected_version="version-1")


def test_get_configuration_file_returns_empty_bootstrap_when_missing(client: TestClient) -> None:
    """Test that a missing configuration source can be bootstrapped through the API."""
    service = MagicMock(
        read_with_version_async=AsyncMock(return_value=("", "missing-version")),
        source="C:/missing/.pyrit_conf",
    )
    with patch("pyrit.backend.routes.configuration._get_configuration_file_service", return_value=service):
        response = client.get("/api/config")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "content": "",
        "source": "C:/missing/.pyrit_conf",
        "version": "missing-version",
    }


def test_update_configuration_file_returns_409_for_stale_version(client: TestClient) -> None:
    """Test that updating a changed configuration source returns a conflict."""
    service = MagicMock(
        update_async=AsyncMock(side_effect=ConfigurationFileConflictError("Configuration changed; reload it")),
        source="config.yaml",
    )
    with patch("pyrit.backend.routes.configuration._get_configuration_file_service", return_value=service):
        response = client.put("/api/config", json={"content": "operator: bob\n", "version": "stale"})

    assert response.status_code == status.HTTP_409_CONFLICT


def test_update_configuration_file_returns_400_for_invalid_content(client: TestClient) -> None:
    """Test that invalid configuration content produces a client error."""
    service = MagicMock(spec=ConfigurationFileService)
    service.update_async = AsyncMock(side_effect=ValueError("Invalid YAML configuration"))
    with patch("pyrit.backend.routes.configuration._get_configuration_file_service", return_value=service):
        response = client.put(
            "/api/config",
            json={"content": "operator: [unterminated\n", "version": "version-1"},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid YAML configuration"


def test_list_environment_files_returns_configured_files(client: TestClient) -> None:
    """Test listing environment files through the API."""
    item = EnvironmentFileContent(
        id="0",
        name=".env",
        path="C:/Users/test/.pyrit/.env",
        content="",
        exists=True,
    )
    service = MagicMock(list_async=AsyncMock(return_value=[item]))
    with patch("pyrit.backend.routes.configuration._get_environment_file_service", return_value=service):
        response = client.get("/api/config/env-files")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"items": [item.model_dump()]}


def test_get_environment_file_returns_selected_content(client: TestClient) -> None:
    """Test reading one selected environment source through the API."""
    item = EnvironmentFileContent(
        id="akv:0",
        name="AKV: bootstrap",
        path="https://vault.vault.azure.net/secrets/bootstrap",
        content="API_KEY=value\n",
        exists=True,
        version="version-1",
    )
    service = MagicMock(read_async=AsyncMock(return_value=item))
    with patch("pyrit.backend.routes.configuration._get_environment_file_service", return_value=service):
        response = client.get("/api/config/env-files/akv%3A0")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == item.model_dump()
    service.read_async.assert_awaited_once_with(file_id="akv:0")


def test_get_environment_file_returns_404_for_unknown_id(client: TestClient) -> None:
    """Test that reading an unknown environment source returns 404."""
    service = MagicMock(read_async=AsyncMock(side_effect=KeyError("missing")))
    with patch("pyrit.backend.routes.configuration._get_environment_file_service", return_value=service):
        response = client.get("/api/config/env-files/missing")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_update_environment_file_persists_selected_file(client: TestClient) -> None:
    """Test updating a selected environment file through the API."""
    item = EnvironmentFileContent(
        id="1",
        name=".env.local",
        path="C:/Users/test/.pyrit/.env.local",
        content="API_KEY=new\n",
        exists=True,
        version="version-2",
    )
    service = MagicMock(update_async=AsyncMock(return_value=item))
    with patch("pyrit.backend.routes.configuration._get_environment_file_service", return_value=service):
        response = client.put(
            "/api/config/env-files/1",
            json={"content": "API_KEY=new\n", "version": "version-1"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == item.model_dump()
    service.update_async.assert_awaited_once_with(
        file_id="1",
        content="API_KEY=new\n",
        expected_version="version-1",
    )


def test_update_environment_file_returns_400_for_versioned_akv_source(client: TestClient) -> None:
    """Test that immutable AKV secret versions produce an actionable client error."""
    service = MagicMock(
        update_async=AsyncMock(side_effect=ValueError("Versioned Azure Key Vault environment sources are read-only"))
    )
    with patch("pyrit.backend.routes.configuration._get_environment_file_service", return_value=service):
        response = client.put(
            "/api/config/env-files/akv%3A0",
            json={"content": "API_KEY=new\n", "version": "version-1"},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Versioned Azure Key Vault environment sources are read-only"


def test_update_environment_file_returns_404_for_unknown_id(client: TestClient) -> None:
    """Test that updating an unknown environment source returns 404."""
    service = MagicMock(update_async=AsyncMock(side_effect=KeyError("missing")))
    with patch("pyrit.backend.routes.configuration._get_environment_file_service", return_value=service):
        response = client.put(
            "/api/config/env-files/missing",
            json={"content": "API_KEY=new\n", "version": "version-1"},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_get_configuration_file_maps_azure_error_to_503(client: TestClient) -> None:
    service = MagicMock(spec=ConfigurationFileService)
    service.read_with_version_async = AsyncMock(side_effect=AzureError("credential details"))
    service.source = "https://account.blob.core.windows.net/config/config.yaml"

    with patch("pyrit.backend.routes.configuration._get_configuration_file_service", return_value=service):
        response = client.get("/api/config")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["detail"] == "Configuration storage is temporarily unavailable"
    assert "credential details" not in response.text


def test_configuration_read_writes_secret_free_audit_record(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = MagicMock(spec=ConfigurationFileService)
    service.read_with_version_async = AsyncMock(return_value=("SECRET=value\n", "version-1"))
    service.source = "C:/config/.pyrit_conf"

    with (
        caplog.at_level("INFO", logger="pyrit.backend.routes.configuration"),
        patch("pyrit.backend.routes.configuration._get_configuration_file_service", return_value=service),
    ):
        response = client.get("/api/config")

    assert response.status_code == status.HTTP_200_OK
    assert "oid=local-development" in caplog.text
    assert "action=read-config" in caplog.text
    assert "outcome=success" in caplog.text
    assert "SECRET=value" not in caplog.text


def test_configuration_audit_includes_authenticated_user_name(caplog: pytest.LogCaptureFixture) -> None:
    request = MagicMock()
    request.state.user = AuthenticatedUser(
        oid="admin-1",
        name="Admin User\nInjected",
        email="admin@example.com",
        groups=["admin-group"],
        is_admin=True,
    )

    with caplog.at_level("INFO", logger="pyrit.backend.routes.configuration"):
        _audit_configuration_access(
            request=request,
            action="read-config",
            source="C:/config/.pyrit_conf",
            outcome="success",
        )

    assert "oid=admin-1" in caplog.text
    assert "user_name='Admin User\\nInjected'" in caplog.text
    assert "Admin User\nInjected" not in caplog.text
