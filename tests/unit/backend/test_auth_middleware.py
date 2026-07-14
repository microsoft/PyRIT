# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the Entra ID auth middleware.
"""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.backend.middleware.auth import EntraAuthMiddleware


def _make_middleware() -> EntraAuthMiddleware:
    with patch.dict("os.environ", {"ENTRA_TENANT_ID": "", "ENTRA_CLIENT_ID": ""}, clear=False):
        return EntraAuthMiddleware(MagicMock())


def test_validate_token_returns_none_when_jwks_client_is_none():
    """Test that _validate_token returns (None, {}) when _jwks_client is None."""
    mock_app = MagicMock()
    with patch.dict(
        "os.environ",
        {"ENTRA_TENANT_ID": "", "ENTRA_CLIENT_ID": ""},
        clear=False,
    ):
        middleware = EntraAuthMiddleware(mock_app)

    # Confirm _jwks_client is None (because tenant/client are empty)
    assert middleware._jwks_client is None

    user, claims = middleware._validate_token("some.fake.token")

    assert user is None
    assert claims == {}


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://graph.microsoft.com/v1.0/me/getMemberObjects", True),
        ("https://graph.microsoft.com/v1.0/me/memberOf?$skiptoken=abc", True),
        ("http://graph.microsoft.com/v1.0/me/getMemberObjects", False),  # not https
        ("https://evil.com/v1.0/me/getMemberObjects", False),  # wrong host
        ("https://graph.microsoft.com.evil.com/x", False),  # suffix spoof
        ("https://graph.microsoft.com@evil.com/x", False),  # userinfo spoof
        ("", False),
    ],
)
def test_is_trusted_graph_url(url, expected):
    """Only HTTPS Microsoft Graph hosts are trusted to receive the forwarded token."""
    assert _make_middleware()._is_trusted_graph_url(url) is expected


async def test_resolve_excess_groups_refuses_untrusted_endpoint():
    """An untrusted _claim_sources endpoint returns [] without making any HTTP request."""
    middleware = _make_middleware()
    claims = {"_claim_sources": {"src1": {"endpoint": "https://evil.com/steal"}}}

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient") as mock_client:
        result = await middleware._resolve_excess_groups_async(claims, "the-token")

    assert result == []
    mock_client.assert_not_called()
