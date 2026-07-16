# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the security headers middleware.
"""

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from pyrit.backend.middleware.security_headers import SecurityHeadersMiddleware


def _build_client(*, dev_mode: bool) -> TestClient:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, dev_mode=dev_mode)

    @app.get("/api/test")
    async def api_route() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/")
    async def frontend_route() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/docs")
    async def docs_route() -> JSONResponse:
        return JSONResponse({"ok": True})

    return TestClient(app)


class TestSecurityHeadersMiddleware:
    """Tests for SecurityHeadersMiddleware."""

    @pytest.fixture
    def prod_client(self) -> TestClient:
        return _build_client(dev_mode=False)

    @pytest.fixture
    def dev_client(self) -> TestClient:
        return _build_client(dev_mode=True)

    def test_common_headers_applied_to_all_responses(self, prod_client: TestClient) -> None:
        response = prod_client.get("/api/test")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in response.headers["Permissions-Policy"]

    def test_api_path_uses_api_csp_and_no_store(self, prod_client: TestClient) -> None:
        response = prod_client.get("/api/test")
        assert response.headers["Content-Security-Policy"] == SecurityHeadersMiddleware._API_CSP
        assert response.headers["Cache-Control"] == "no-store"

    def test_frontend_path_uses_frontend_csp(self, prod_client: TestClient) -> None:
        response = prod_client.get("/")
        assert response.headers["Content-Security-Policy"] == SecurityHeadersMiddleware._FRONTEND_CSP
        assert "Cache-Control" not in response.headers or response.headers["Cache-Control"] != "no-store"

    def test_docs_path_in_dev_mode_skips_csp(self, dev_client: TestClient) -> None:
        response = dev_client.get("/docs")
        assert "Content-Security-Policy" not in response.headers

    def test_docs_path_in_prod_mode_still_gets_frontend_csp(self, prod_client: TestClient) -> None:
        response = prod_client.get("/docs")
        assert response.headers["Content-Security-Policy"] == SecurityHeadersMiddleware._FRONTEND_CSP

    def test_hsts_set_in_prod_mode(self, prod_client: TestClient) -> None:
        response = prod_client.get("/")
        assert "Strict-Transport-Security" in response.headers

    def test_hsts_omitted_in_dev_mode(self, dev_client: TestClient) -> None:
        response = dev_client.get("/")
        assert "Strict-Transport-Security" not in response.headers
