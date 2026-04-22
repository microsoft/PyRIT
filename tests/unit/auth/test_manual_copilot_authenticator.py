# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
from unittest.mock import patch

import jwt as pyjwt
import pytest

from pyrit.auth.manual_copilot_authenticator import ManualCopilotAuthenticator


def _make_jwt(claims: dict) -> str:
    """Create an unsigned JWT with the given claims for testing."""
    return pyjwt.encode(claims, key="secret", algorithm="HS256")


VALID_CLAIMS = {"tid": "tenant-id-123", "oid": "object-id-456", "sub": "user"}
VALID_TOKEN = _make_jwt(VALID_CLAIMS)


def test_init_with_valid_token():
    auth = ManualCopilotAuthenticator(access_token=VALID_TOKEN)
    assert auth.get_token() == VALID_TOKEN


def test_init_reads_from_env_var_when_no_token_provided():
    with patch.dict(os.environ, {ManualCopilotAuthenticator.ACCESS_TOKEN_ENV_VAR: VALID_TOKEN}):
        auth = ManualCopilotAuthenticator()
        assert auth.get_token() == VALID_TOKEN


def test_init_raises_when_no_token_and_no_env_var():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="access_token must be provided"):
            ManualCopilotAuthenticator()


def test_init_raises_for_invalid_jwt():
    with pytest.raises(ValueError, match="Failed to decode access_token as JWT"):
        ManualCopilotAuthenticator(access_token="not-a-valid-jwt")


def test_init_raises_when_missing_tid_claim():
    token = _make_jwt({"oid": "object-id-456"})
    with pytest.raises(ValueError, match="missing required claims"):
        ManualCopilotAuthenticator(access_token=token)


def test_init_raises_when_missing_oid_claim():
    token = _make_jwt({"tid": "tenant-id-123"})
    with pytest.raises(ValueError, match="missing required claims"):
        ManualCopilotAuthenticator(access_token=token)


def test_init_raises_when_missing_both_required_claims():
    token = _make_jwt({"sub": "user"})
    with pytest.raises(ValueError, match="missing required claims"):
        ManualCopilotAuthenticator(access_token=token)


def test_get_token_returns_access_token():
    auth = ManualCopilotAuthenticator(access_token=VALID_TOKEN)
    assert auth.get_token() == VALID_TOKEN


@pytest.mark.asyncio
async def test_get_token_async_returns_access_token():
    auth = ManualCopilotAuthenticator(access_token=VALID_TOKEN)
    result = await auth.get_token_async()
    assert result == VALID_TOKEN


@pytest.mark.asyncio
async def test_get_claims_returns_decoded_claims():
    auth = ManualCopilotAuthenticator(access_token=VALID_TOKEN)
    claims = await auth.get_claims()
    assert claims["tid"] == "tenant-id-123"
    assert claims["oid"] == "object-id-456"


def test_refresh_token_raises_runtime_error():
    auth = ManualCopilotAuthenticator(access_token=VALID_TOKEN)
    with pytest.raises(RuntimeError, match="Manual token cannot be refreshed"):
        auth.refresh_token()


@pytest.mark.asyncio
async def test_refresh_token_async_raises_runtime_error():
    auth = ManualCopilotAuthenticator(access_token=VALID_TOKEN)
    with pytest.raises(RuntimeError, match="Manual token cannot be refreshed"):
        await auth.refresh_token_async()


def test_direct_token_takes_precedence_over_env_var():
    other_token = _make_jwt({"tid": "other-tenant", "oid": "other-oid"})
    with patch.dict(os.environ, {ManualCopilotAuthenticator.ACCESS_TOKEN_ENV_VAR: other_token}):
        auth = ManualCopilotAuthenticator(access_token=VALID_TOKEN)
        assert auth.get_token() == VALID_TOKEN
