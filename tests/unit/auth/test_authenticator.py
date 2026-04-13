# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.auth.authenticator import Authenticator


class ConcreteAuthenticator(Authenticator):
    """Minimal concrete subclass for testing the ABC."""

    def __init__(self) -> None:
        self.token = "test-token"


@pytest.fixture
def authenticator():
    return ConcreteAuthenticator()


def test_authenticator_is_abstract():
    assert hasattr(Authenticator, "__abstractmethods__") is False or len(Authenticator.__abstractmethods__) == 0
    # Authenticator has no abstract methods (uses NotImplementedError pattern instead)


def test_refresh_token_raises_not_implemented(authenticator):
    with pytest.raises(NotImplementedError, match="refresh_token"):
        authenticator.refresh_token()


@pytest.mark.asyncio
async def test_refresh_token_async_raises_not_implemented(authenticator):
    with pytest.raises(NotImplementedError, match="refresh_token"):
        await authenticator.refresh_token_async()


def test_get_token_raises_not_implemented(authenticator):
    with pytest.raises(NotImplementedError, match="get_token"):
        authenticator.get_token()


@pytest.mark.asyncio
async def test_get_token_async_raises_not_implemented(authenticator):
    with pytest.raises(NotImplementedError, match="get_token"):
        await authenticator.get_token_async()


def test_token_attribute_can_be_set(authenticator):
    assert authenticator.token == "test-token"
    authenticator.token = "new-token"
    assert authenticator.token == "new-token"
