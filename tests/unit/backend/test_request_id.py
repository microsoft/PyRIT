# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the X-Request-ID middleware.
"""

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from pyrit.backend.middleware.request_id import RequestIdMiddleware


class TestRequestIdMiddleware:
    """Tests for RequestIdMiddleware."""

    @pytest.fixture
    def app(self) -> FastAPI:
        """Create a FastAPI app with request ID middleware.

        Returns:
            FastAPI: The test app.
        """
        app = FastAPI()
        app.add_middleware(RequestIdMiddleware)

        @app.get("/test")
        async def test_endpoint(request: Request) -> JSONResponse:
            return JSONResponse({"request_id": request.state.request_id})

        return app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        """Create a test client.

        Args:
            app: The FastAPI app.

        Returns:
            TestClient: The test client.
        """
        return TestClient(app)

    def test_generates_request_id_when_header_absent(self, client: TestClient) -> None:
        """Middleware generates a UUID request ID when no header is provided."""
        response = client.get("/test")

        assert response.status_code == 200
        request_id = response.headers["X-Request-ID"]
        # Should be a valid UUID
        uuid.UUID(request_id)  # raises ValueError if invalid

    def test_uses_client_request_id_when_provided(self, client: TestClient) -> None:
        """Middleware uses the client-provided X-Request-ID header."""
        custom_id = "frontend-abc-123"
        response = client.get("/test", headers={"X-Request-ID": custom_id})

        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == custom_id
        assert response.json()["request_id"] == custom_id

    def test_request_id_available_on_request_state(self, client: TestClient) -> None:
        """Middleware stores request_id on request.state for route handlers."""
        response = client.get("/test")

        body = response.json()
        assert body["request_id"] == response.headers["X-Request-ID"]

    def test_response_always_includes_header(self, client: TestClient) -> None:
        """Every response includes the X-Request-ID header."""
        for _ in range(3):
            response = client.get("/test")
            assert "X-Request-ID" in response.headers
