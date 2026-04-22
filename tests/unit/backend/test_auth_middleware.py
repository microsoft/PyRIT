# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the Entra ID auth middleware.
"""

from unittest.mock import MagicMock, patch

from pyrit.backend.middleware.auth import EntraAuthMiddleware


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
