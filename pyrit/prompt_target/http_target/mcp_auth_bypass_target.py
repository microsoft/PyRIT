# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
from typing import Any, Optional

import httpx

from pyrit.models import (
    MessagePiece,
    construct_response_from_request,
)
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.utils import limit_requests_per_minute

logger = logging.getLogger(__name__)


class MCPAuthBypassTarget(PromptTarget):
    """
    MCPAuthBypassTarget tests MCP server endpoints for authentication bypass vulnerabilities.
    Implements OWASP MCP-07 (Insufficient Authentication/Authorization) testing.

    Args:
        mcp_server_url (str): The base URL of the MCP server endpoint.
        bypass_technique (str): One of: no_auth, empty_token, malformed_token, role_escalation
        mcp_method (str): The MCP JSON-RPC method to test. Defaults to tools/list.
        timeout (int): Request timeout in seconds. Defaults to 30.
        max_requests_per_minute (int, Optional): Rate limit for requests.
    """

    BYPASS_TECHNIQUES = ["no_auth", "empty_token", "malformed_token", "role_escalation"]

    def __init__(
        self,
        mcp_server_url: str,
        bypass_technique: str = "no_auth",
        mcp_method: str = "tools/list",
        timeout: int = 30,
        max_requests_per_minute: Optional[int] = None,
    ) -> None:
        """
        Initialize MCPAuthBypassTarget.

        Args:
            mcp_server_url: The base URL of the MCP server endpoint.
            bypass_technique: The auth bypass technique to use.
            mcp_method: The MCP JSON-RPC method to test.
            timeout: Request timeout in seconds.
            max_requests_per_minute: Rate limit for requests.

        Raises:
            ValueError: If bypass_technique is not a valid option.
        """
        if bypass_technique not in self.BYPASS_TECHNIQUES:
            raise ValueError(
                f"Invalid bypass_technique '{bypass_technique}'. "
                f"Must be one of: {self.BYPASS_TECHNIQUES}"
            )
        super().__init__(
            max_requests_per_minute=max_requests_per_minute,
            endpoint=mcp_server_url,
        )
        self.mcp_server_url = mcp_server_url
        self.bypass_technique = bypass_technique
        self.mcp_method = mcp_method
        self.timeout = timeout

    def _build_headers(self) -> dict[str, str]:
        base_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.bypass_technique == "no_auth":
            return base_headers
        if self.bypass_technique == "empty_token":
            return {**base_headers, "Authorization": "Bearer "}
        if self.bypass_technique == "malformed_token":
            return {**base_headers, "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.invalid"}
        if self.bypass_technique == "role_escalation":
            tampered = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ0ZXN0Iiwicm9sZSI6ImFkbWluIn0."
            return {**base_headers, "Authorization": f"Bearer {tampered}"}
        return base_headers

    def _build_jsonrpc_payload(self, prompt: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": 1, "method": self.mcp_method, "params": {"prompt": prompt}}

    def _evaluate_response(self, status_code: int, response_body: str) -> str:
        if status_code == 200:
            return (
                f"[VULNERABILITY DETECTED] MCP-07 Auth Bypass succeeded using "
                f"'{self.bypass_technique}'. Server returned HTTP 200.\nResponse: {response_body[:500]}"
            )
        if status_code in (401, 403):
            return (
                f"[SECURE] Server correctly rejected with HTTP {status_code} "
                f"using '{self.bypass_technique}'.\nResponse: {response_body[:200]}"
            )
        return (
            f"[INVESTIGATE] Unexpected HTTP {status_code} "
            f"using '{self.bypass_technique}'.\nResponse: {response_body[:200]}"
        )

    def _validate_request(self, *, message) -> None:
        """
        Validate the request message. MCP target accepts all text messages.

        Raises:
            ValueError: If the message is None or empty.
        """
        if not message:
            raise ValueError("Message cannot be None or empty.")

    @limit_requests_per_minute
    async def send_prompt_async(self, *, prompt_request: MessagePiece) -> MessagePiece:
        """
        Send a prompt to the MCP server using the configured auth bypass technique.

        Args:
            prompt_request: The prompt request to send.

        Returns:
            MessagePiece: The response containing bypass test results.
        """
        prompt_text = prompt_request.converted_value
        headers = self._build_headers()
        payload = self._build_jsonrpc_payload(prompt_text)
        logger.info(f"MCPAuthBypassTarget: Testing '{self.bypass_technique}' against {self.mcp_server_url}")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.mcp_server_url, headers=headers, content=json.dumps(payload))
                result = self._evaluate_response(response.status_code, response.text)
        except httpx.TimeoutException:
            result = f"[ERROR] Request timed out after {self.timeout}s"
        except httpx.ConnectError as e:
            result = f"[ERROR] Connection failed to {self.mcp_server_url}: {e}"
        except Exception as e:
            result = f"[ERROR] Unexpected error: {type(e).__name__}: {e}"
        return construct_response_from_request(request=prompt_request, response_text_pieces=[result])
