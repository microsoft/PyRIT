# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Contract tests for authentication utilities used by azure-ai-evaluation.

The azure-ai-evaluation red team module uses:
- get_azure_openai_auth: Called in _utils/strategy_utils.py to authenticate
  OpenAIChatTarget for tense/translation converter strategies.
"""

from pyrit.auth import get_azure_openai_auth


class TestAuthContract:
    """Validate authentication utility availability."""

    def test_get_azure_openai_auth_is_callable(self):
        """strategy_utils.py calls get_azure_openai_auth() for OpenAI target auth."""
        assert callable(get_azure_openai_auth)
