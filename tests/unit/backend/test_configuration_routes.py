# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for backend configuration file routes."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from pyrit.backend.main import app
from pyrit.backend.services.configuration_file_service import ConfigurationFileService


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


def test_get_configuration_file_returns_content(client: TestClient) -> None:
    """Test reading configuration contents through the API."""
    service = MagicMock(spec=ConfigurationFileService)
    service.read_async = AsyncMock(return_value="operator: alice\n")
    service.source = "C:/Users/test/.pyrit/config.yaml"
    with patch("pyrit.backend.routes.configuration._get_configuration_file_service", return_value=service):
        response = client.get("/api/config")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "content": "operator: alice\n",
        "source": "C:/Users/test/.pyrit/config.yaml",
    }


def test_update_configuration_file_persists_content(client: TestClient) -> None:
    """Test replacing configuration contents through the API."""
    service = MagicMock(spec=ConfigurationFileService)
    service.read_async = AsyncMock()
    service.update_async = AsyncMock()
    service.source = "https://account.blob.core.windows.net/config/config.yaml"
    with patch("pyrit.backend.routes.configuration._get_configuration_file_service", return_value=service):
        response = client.put("/api/config", json={"content": "operator: bob\n"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "content": "operator: bob\n",
        "source": "https://account.blob.core.windows.net/config/config.yaml",
    }
    service.update_async.assert_awaited_once_with("operator: bob\n")


def test_get_configuration_file_returns_404_when_missing(client: TestClient) -> None:
    """Test that a missing configuration source returns 404."""
    service = MagicMock(read_async=AsyncMock(side_effect=FileNotFoundError))
    with patch("pyrit.backend.routes.configuration._get_configuration_file_service", return_value=service):
        response = client.get("/api/config")

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_list_environment_files_returns_configured_files(client: TestClient) -> None:
    """Test listing environment files through the API."""
    item = {
        "id": "0",
        "name": ".env",
        "path": "C:/Users/test/.pyrit/.env",
        "content": "",
        "exists": True,
    }
    service = MagicMock(list_async=AsyncMock(return_value=[item]))
    with patch("pyrit.backend.routes.configuration._get_environment_file_service", return_value=service):
        response = client.get("/api/config/env-files")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"items": [item]}


def test_get_environment_file_returns_selected_content(client: TestClient) -> None:
    """Test reading one selected environment source through the API."""
    item = {
        "id": "akv:0",
        "name": "AKV: bootstrap",
        "path": "https://vault.vault.azure.net/secrets/bootstrap",
        "content": "API_KEY=value\n",
        "exists": True,
    }
    service = MagicMock(read_async=AsyncMock(return_value=item))
    with patch("pyrit.backend.routes.configuration._get_environment_file_service", return_value=service):
        response = client.get("/api/config/env-files/akv%3A0")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == item
    service.read_async.assert_awaited_once_with(file_id="akv:0")


def test_update_environment_file_persists_selected_file(client: TestClient) -> None:
    """Test updating a selected environment file through the API."""
    item = {
        "id": "1",
        "name": ".env.local",
        "path": "C:/Users/test/.pyrit/.env.local",
        "content": "API_KEY=new\n",
        "exists": True,
    }
    service = MagicMock(update_async=AsyncMock(return_value=item))
    with patch("pyrit.backend.routes.configuration._get_environment_file_service", return_value=service):
        response = client.put("/api/config/env-files/1", json={"content": "API_KEY=new\n"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == item
    service.update_async.assert_awaited_once_with(file_id="1", content="API_KEY=new\n")


def test_update_environment_file_returns_400_for_versioned_akv_source(client: TestClient) -> None:
    """Test that immutable AKV secret versions produce an actionable client error."""
    service = MagicMock(
        update_async=AsyncMock(side_effect=ValueError("Versioned Azure Key Vault environment sources are read-only"))
    )
    with patch("pyrit.backend.routes.configuration._get_environment_file_service", return_value=service):
        response = client.put("/api/config/env-files/akv%3A0", json={"content": "API_KEY=new\n"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Versioned Azure Key Vault environment sources are read-only"
