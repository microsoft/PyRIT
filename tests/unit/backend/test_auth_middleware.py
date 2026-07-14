# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the Entra ID auth middleware.
"""

from unittest.mock import AsyncMock, MagicMock, patch

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


async def test_resolve_excess_groups_no_overage_returns_empty():
    """Claims without a groups overage pointer return [] and make no HTTP request."""
    middleware = _make_middleware()
    claims = {"_claim_sources": {"src1": {"endpoint": "https://graph.microsoft.com/x"}}}  # no _claim_names.groups

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient") as mock_client:
        result = await middleware._resolve_excess_groups_async(claims, "the-token")

    assert result == []
    mock_client.assert_not_called()


async def test_resolve_excess_groups_ignores_token_endpoint():
    """The Graph request uses the trusted constant URL, never the token-supplied endpoint."""
    middleware = _make_middleware()
    claims = {
        "_claim_names": {"groups": "src1"},
        "_claim_sources": {"src1": {"endpoint": "https://evil.com/steal"}},
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"value": ["group-1", "group-2"]}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient", return_value=mock_client_cm):
        result = await middleware._resolve_excess_groups_async(claims, "the-token")

    assert result == ["group-1", "group-2"]
    posted_url = mock_client.post.call_args.args[0]
    assert posted_url == EntraAuthMiddleware._GRAPH_MEMBER_OBJECTS_URL
    assert "evil.com" not in posted_url


async def test_resolve_excess_groups_stops_on_untrusted_pagination_link():
    """An untrusted @odata.nextLink halts pagination without issuing the follow-up GET."""
    middleware = _make_middleware()
    claims = {
        "_claim_names": {"groups": "src1"},
        "_claim_sources": {"src1": {"endpoint": "https://graph.microsoft.com/x"}},
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"value": ["group-1"], "@odata.nextLink": "https://evil.com/next"}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("pyrit.backend.middleware.auth.httpx.AsyncClient", return_value=mock_client_cm):
        result = await middleware._resolve_excess_groups_async(claims, "the-token")

    assert result == ["group-1"]
    mock_client.get.assert_not_called()
