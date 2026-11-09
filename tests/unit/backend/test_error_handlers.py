# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for backend error handler middleware.
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from pyrit.backend.middleware import register_error_handlers
from pyrit.registry.file_document_storage import DocumentStorageUnavailableError, DocumentUnreadableError


class TestErrorHandlers:
    """Tests for RFC 7807 error handlers."""

    @pytest.fixture
    def app(self) -> FastAPI:
        """Create a FastAPI app with error handlers registered.

        Returns:
            FastAPI: The test app.
        """
        app = FastAPI()
        register_error_handlers(app)
        return app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        """Create a test client.

        Args:
            app: The FastAPI app.

        Returns:
            TestClient: The test client.
        """
        return TestClient(app, raise_server_exceptions=False)

    def test_validation_error_returns_422(self, app: FastAPI, client: TestClient) -> None:
        """Test that validation errors return 422 with RFC 7807 format."""

        class TestInput(BaseModel):
            name: str
            age: int

        @app.post("/test")
        async def test_endpoint(data: TestInput) -> dict:
            return {"ok": True}

        response = client.post("/test", json={"name": 123})  # Missing age, wrong type

        assert response.status_code == 422
        data = response.json()
        assert data["type"] == "/errors/validation-error"
        assert data["title"] == "Validation Error"
        assert data["status"] == 422
        assert "errors" in data

    def test_validation_error_includes_field_details(self, app: FastAPI, client: TestClient) -> None:
        """Test that validation errors include field-level details."""

        class TestInput(BaseModel):
            name: str

        @app.post("/test")
        async def test_endpoint(data: TestInput) -> dict:
            return {"ok": True}

        response = client.post("/test", json={})  # Missing required field

        data = response.json()
        assert "errors" in data
        assert len(data["errors"]) > 0
        # Check field error structure
        error = data["errors"][0]
        assert "field" in error
        assert "message" in error

    def test_value_error_returns_400(self, app: FastAPI, client: TestClient) -> None:
        """Test that ValueError returns 400 with RFC 7807 format."""

        @app.get("/test")
        async def test_endpoint() -> dict:
            raise ValueError("Invalid input value")

        response = client.get("/test")

        assert response.status_code == 400
        data = response.json()
        assert data["type"] == "/errors/bad-request"
        assert data["title"] == "Bad Request"
        assert data["status"] == 400
        assert "Invalid input value" in data["detail"]

    def test_file_not_found_error_returns_404(self, app: FastAPI, client: TestClient) -> None:
        """Test that FileNotFoundError returns 404 with RFC 7807 format."""

        @app.get("/test")
        async def test_endpoint() -> dict:
            raise FileNotFoundError("Resource not found")

        response = client.get("/test")

        assert response.status_code == 404
        data = response.json()
        assert data["type"] == "/errors/not-found"
        assert data["title"] == "Not Found"
        assert data["status"] == 404

    def test_permission_error_returns_403(self, app: FastAPI, client: TestClient) -> None:
        """Test that PermissionError returns 403 with RFC 7807 format."""

        @app.get("/test")
        async def test_endpoint() -> dict:
            raise PermissionError("Access denied")

        response = client.get("/test")

        assert response.status_code == 403
        data = response.json()
        assert data["type"] == "/errors/forbidden"
        assert data["title"] == "Forbidden"
        assert data["status"] == 403

    def test_storage_failure_returns_503_and_names_the_location_only_in_the_log(
        self, app: FastAPI, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test that the log names the storage location and the response doesn't."""
        error = DocumentStorageUnavailableError(
            label="Scenario preset", source="/srv/presets", reason="Permission denied"
        )

        @app.get("/test")
        async def test_endpoint() -> dict:
            raise error

        with caplog.at_level(logging.DEBUG, logger="pyrit.backend.middleware.error_handlers"):
            response = client.get("/test")

        assert response.status_code == 503
        assert response.json() == {
            "type": "/errors/storage-unavailable",
            "title": "Service Unavailable",
            "status": 503,
            "detail": "Scenario preset storage is unavailable",
            "instance": "/test",
        }
        assert "Scenario preset storage at /srv/presets is unavailable: Permission denied (GET /test)" in (
            caplog.messages
        )
        assert any(record.exc_info and record.exc_info[1] is error for record in caplog.records)

    def test_unreadable_document_returns_503_with_the_reason(self, app: FastAPI, client: TestClient) -> None:
        """Test that one document that can't be read is named with its reason."""

        @app.get("/test")
        async def test_endpoint() -> dict:
            raise DocumentUnreadableError(
                label="Scenario preset",
                source="/srv/presets",
                name="nightly",
                file_name="nightly.json",
                reason="Not a regular file",
            )

        response = client.get("/test")

        assert response.status_code == 503
        assert response.json()["detail"] == "Scenario preset 'nightly' can't be read: Not a regular file"
        assert "/srv/presets" not in response.text

    def test_not_implemented_error_returns_501(self, app: FastAPI, client: TestClient) -> None:
        """Test that NotImplementedError returns 501 with RFC 7807 format."""

        @app.get("/test")
        async def test_endpoint() -> dict:
            raise NotImplementedError("Feature not yet implemented")

        response = client.get("/test")

        assert response.status_code == 501
        data = response.json()
        assert data["type"] == "/errors/not-implemented"
        assert data["title"] == "Not Implemented"
        assert data["status"] == 501

    def test_generic_exception_returns_500(self, app: FastAPI, client: TestClient) -> None:
        """Test that unexpected exceptions return 500 with RFC 7807 format."""

        @app.get("/test")
        async def test_endpoint() -> dict:
            raise RuntimeError("Something went wrong")

        response = client.get("/test")

        assert response.status_code == 500
        data = response.json()
        assert data["type"] == "/errors/internal-error"
        assert data["title"] == "Internal Server Error"
        assert data["status"] == 500
        # Should not leak internal error details
        assert "An unexpected error occurred" in data["detail"]

    def test_error_response_includes_instance(self, app: FastAPI, client: TestClient) -> None:
        """Test that error responses include the request path as instance."""

        @app.get("/api/v1/test/resource")
        async def test_endpoint() -> dict:
            raise ValueError("Test error")

        response = client.get("/api/v1/test/resource")

        data = response.json()
        assert data["instance"] == "/api/v1/test/resource"

    def test_error_excludes_none_fields(self, app: FastAPI, client: TestClient) -> None:
        """Test that None fields are excluded from error response."""

        @app.get("/test")
        async def test_endpoint() -> dict:
            raise ValueError("Test error")

        response = client.get("/test")

        data = response.json()
        # 'errors' should not be present for non-validation errors
        assert "errors" not in data
