# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from typing import Any, Optional

import jwt

from pyrit.auth.authenticator import Authenticator

logger = logging.getLogger(__name__)


class ManualCopilotAuthenticator(Authenticator):
    """
    Simple authenticator that uses a manually-provided access token for Microsoft Copilot.

    This authenticator is useful for testing or environments where browser automation is not
    possible. Users can obtain the access token from browser DevTools and provide it directly.

    How to obtain the access token:
        1. Open the Copilot webapp (e.g., https://m365.cloud.microsoft/chat) in a browser.
        2. Open DevTools (F12 or Ctrl+Shift+I).
        3. Go to the Network tab.
        4. Filter by "Socket" connections or search for "Chathub".
        5. Start typing in the chat to initiate a WebSocket connection.
        6. Look for the latest WebSocket connection to ``substrate.office.com/m365Copilot/Chathub``.
        7. You may find the ``access_token`` in the request URL or in the request payload.

    Note:
        After expiration (typically about 60 minutes), you will need to obtain a new token manually.
    """

    #: Environment variable name for the Copilot access token
    ACCESS_TOKEN_ENV_VAR: str = "COPILOT_ACCESS_TOKEN"

    def __init__(self, *, access_token: Optional[str] = None) -> None:
        """
        Initialize the ManualCopilotAuthenticator with a pre-obtained access token.

        Args:
            access_token (Optional[str]): A valid JWT access token for Microsoft Copilot.
                This token can be obtained from browser DevTools when connected to Copilot.
                If None, the token will be read from the ``COPILOT_ACCESS_TOKEN`` environment variable.

        Raises:
            ValueError: If no access token is provided either directly or via the environment variable.
            ValueError: If the provided access token cannot be decoded as a JWT or is missing required claims.
        """
        super().__init__()

        resolved_token = access_token or os.getenv(self.ACCESS_TOKEN_ENV_VAR)
        if not resolved_token:
            raise ValueError(
                f"access_token must be provided or {self.ACCESS_TOKEN_ENV_VAR} environment variable must be set."
            )

        self._access_token = resolved_token

        try:
            self._claims: dict[str, Any] = jwt.decode(
                resolved_token, algorithms=["RS256"], options={"verify_signature": False}
            )
        except jwt.exceptions.DecodeError as e:
            raise ValueError(f"Failed to decode access_token as JWT: {e}") from e

        required_claims = ["tid", "oid"]
        missing_claims = [claim for claim in required_claims if claim not in self._claims]

        if missing_claims:
            raise ValueError(
                f"Access token is missing required claims: {missing_claims}. "
                "Ensure you're using a valid Copilot access token."
            )

    async def get_token_async(self) -> str:
        """
        Get the current authentication token.

        Returns:
            str: The access token provided during initialization.
        """
        return self._access_token

    def get_token(self) -> str:
        """
        Get the current authentication token synchronously.

        Returns:
            str: The access token provided during initialization.
        """
        return self._access_token

    async def get_claims(self) -> dict[str, Any]:
        """
        Get the JWT claims from the access token.

        Returns:
            dict[str, Any]: The JWT claims decoded from the access token.
        """
        return self._claims

    async def refresh_token_async(self) -> str:
        """
        Not supported by this authenticator.

        Raises:
            RuntimeError: Always raised as manual tokens cannot be refreshed automatically.
        """
        raise RuntimeError(
            "Manual token cannot be refreshed automatically. "
            "Please obtain a new token from browser DevTools and create a new authenticator instance."
        )

    def refresh_token(self) -> str:
        """
        Not supported by this authenticator.

        Raises:
            RuntimeError: Always raised as manual tokens cannot be refreshed automatically.
        """
        raise RuntimeError(
            "Manual token cannot be refreshed automatically. "
            "Please obtain a new token from browser DevTools and create a new authenticator instance."
        )
