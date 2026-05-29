# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import abc


class Authenticator(abc.ABC):
    """Abstract base class for authenticators."""

    token: str

    def refresh_token(self) -> str:
        """
        Refresh the authentication token synchronously.

        Returns:
            str: The refreshed authentication token.
        """
        raise NotImplementedError("Either refresh_token or refresh_token_async method must be implemented")

    async def refresh_token_async(self) -> str:
        """
        Refresh the authentication token asynchronously.

        Returns:
            str: The refreshed authentication token.
        """
        raise NotImplementedError("Either refresh_token or refresh_token_async method must be implemented")

    def get_token(self) -> str:
        """
        Get the current authentication token synchronously.

        Returns:
            str: The current authentication token.
        """
        raise NotImplementedError("Either get_token or get_token_async method must be implemented")

    async def get_token_async(self) -> str:
        """
        Get the current authentication token asynchronously.

        Returns:
            str: The current authentication token.
        """
        raise NotImplementedError("Either get_token or get_token_async method must be implemented")
