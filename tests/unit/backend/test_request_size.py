# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from pyrit.backend.main import app as backend_app
from pyrit.backend.middleware.error_handlers import register_error_handlers
from pyrit.backend.middleware.request_size import RequestSizeLimitMiddleware

_LIMIT = 16
_TOO_LARGE = {
    "type": "/errors/request-too-large",
    "title": "Content Too Large",
    "status": 413,
    "detail": "The request is larger than the backend accepts.",
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(RequestSizeLimitMiddleware)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    @app.get("/items")
    async def items(q: str = "") -> dict[str, int]:
        return {"size": len(q)}

    with (
        patch.object(RequestSizeLimitMiddleware, "MAX_BODY_BYTES", _LIMIT),
        patch.object(RequestSizeLimitMiddleware, "MAX_URL_LENGTH", 64),
        TestClient(app) as test_client,
    ):
        yield test_client


def test_body_within_limit_reaches_handler(client: TestClient) -> None:
    response = client.post("/echo", content=b"a" * _LIMIT)

    assert response.status_code == 200
    assert response.json() == {"size": _LIMIT}


@pytest.mark.parametrize(
    "content",
    [b"a" * (_LIMIT + 1), iter([b"a" * _LIMIT, b"b"])],
    ids=["declared", "streamed"],
)
def test_body_over_limit_returns_problem(client: TestClient, content: bytes | Iterator[bytes]) -> None:
    response = client.post("/echo", content=content)

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == _TOO_LARGE


def test_long_url_returns_414(client: TestClient) -> None:
    response = client.get("/items", params={"q": "x" * 64})

    assert response.status_code == 414
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {**_TOO_LARGE, "title": "URI Too Long", "status": 414}


def test_url_within_limit_reaches_handler(client: TestClient) -> None:
    response = client.get("/items", params={"q": "x" * 8})

    assert response.status_code == 200
    assert response.json() == {"size": 8}


def test_backend_app_registers_middleware() -> None:
    assert RequestSizeLimitMiddleware in [middleware.cls for middleware in backend_app.user_middleware]


def test_backend_app_returns_413_for_streamed_body_over_limit(compatibility_headers: dict[str, str]) -> None:
    client = TestClient(backend_app, headers={**compatibility_headers, "Content-Type": "application/json"})

    with patch.object(RequestSizeLimitMiddleware, "MAX_BODY_BYTES", _LIMIT):
        response = client.post("/api/attacks", content=iter([b"{" * _LIMIT, b"}"]))

    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == _TOO_LARGE
