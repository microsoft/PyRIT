# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import uuid
from typing import Any, Literal
from urllib.parse import urljoin

import httpx

from pyrit.models import (
    Message,
    construct_response_from_request,
)
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.prompt_target.common.utils import limit_requests_per_minute

logger = logging.getLogger(__name__)

RPC_METHOD_NOT_FOUND = -32601


class A2ATarget(PromptTarget):
    """
    A PromptTarget for interacting with agents speaking the Agent-to-Agent (A2A) protocol.

    The Agent-to-Agent protocol defines task-based message exchange over JSON-RPC 2.0.
    This target natively supports both protocol revisions:
    - Spec 0.3+: uses `message/send` with message parts discriminator `kind: "text"`.
    - Spec 0.2: uses `tasks/send` with message parts discriminator `type: "text"`.

    By default (`dialect="auto"`), the target attempts the current 0.3 specification first.
    If the target agent responds with JSON-RPC error code -32601 (Method not found),
    it automatically falls back to 0.2 and pins that dialect for subsequent turns.

    Multi-turn conversation sessions are maintained by associating PyRIT conversation IDs
    with active server task IDs, ensuring session continuity during iterative red-teaming.
    """

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            input_modalities=frozenset({frozenset(["text"])}),
        )
    )

    def __init__(
        self,
        *,
        endpoint: str,
        auth_token: str | None = None,
        api_key: str | None = None,
        api_key_header: str = "X-API-Key",
        dialect: Literal["auto", "v03", "v02"] = "auto",
        max_requests_per_minute: int | None = None,
        custom_configuration: TargetConfiguration | None = None,
        **httpx_client_kwargs: Any,
    ) -> None:
        """
        Initialize the A2ATarget.

        Args:
            endpoint (str): The target URL of the A2A agent endpoint.
            auth_token (str, Optional): Bearer token for HTTP Authorization header.
            api_key (str, Optional): Custom API key for the agent.
            api_key_header (str): Header name for the API key (defaults to "X-API-Key").
            dialect (Literal["auto", "v03", "v02"]): A2A protocol dialect to use. Defaults to "auto".
            max_requests_per_minute (int, Optional): Rate limit cap for requests per minute.
            custom_configuration (TargetConfiguration, Optional): Custom target capabilities override.
            **httpx_client_kwargs: Additional keyword arguments passed to httpx.AsyncClient.
        """
        super().__init__(
            endpoint=endpoint,
            max_requests_per_minute=max_requests_per_minute,
            custom_configuration=custom_configuration,
        )

        self._endpoint = endpoint.rstrip("/")
        self._auth_token = auth_token
        self._api_key = api_key
        self._api_key_header = api_key_header
        self._dialect: Literal["auto", "v03", "v02"] = dialect
        self._conversation_tasks: dict[str, str] = {}
        self._httpx_client_kwargs = httpx_client_kwargs

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        if self._api_key:
            headers[self._api_key_header] = self._api_key
        return headers

    def _build_payload(self, dialect: Literal["v03", "v02"], message_text: str, task_id: str | None) -> dict[str, Any]:
        rpc_id = str(uuid.uuid4())
        if dialect == "v03":
            params: dict[str, Any] = {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": message_text}],
                }
            }
            if task_id:
                params["task_id"] = task_id
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "method": "message/send",
                "params": params,
            }
        # Dialect v0.2
        effective_id = task_id or str(uuid.uuid4())
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tasks/send",
            "params": {
                "id": effective_id,
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": message_text}],
                },
            },
        }

    @staticmethod
    def _extract_response_text(result_or_error: dict[str, Any]) -> str:
        """
        Extract the conversational text or refusal from an A2A JSON-RPC response.

        Returns:
            str: Extracted response content or refusal description.
        """
        if "error" in result_or_error and result_or_error["error"] is not None:
            err = result_or_error["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return f"[A2A Refusal] {err_msg}"

        result = result_or_error.get("result")
        if not result or not isinstance(result, dict):
            return str(result) if result is not None else ""

        # Check message parts (spec standard)
        msg = result.get("message")
        if isinstance(msg, dict):
            parts = msg.get("parts")
            if isinstance(parts, list):
                texts = [str(part.get("text", "")) for part in parts if isinstance(part, dict) and "text" in part]
                if texts:
                    return "\n".join(texts)

        # Check artifacts
        artifacts = result.get("artifacts")
        if isinstance(artifacts, list):
            texts = []
            for art in artifacts:
                if isinstance(art, dict):
                    parts = art.get("parts")
                    if isinstance(parts, list):
                        for part in parts:
                            if isinstance(part, dict) and "text" in part:
                                texts.append(str(part.get("text", "")))
            if texts:
                return "\n".join(texts)

        # Check status message
        status = result.get("status")
        if isinstance(status, dict) and "message" in status:
            return str(status["message"])

        return str(result)

    @staticmethod
    def _extract_task_id(result: dict[str, Any] | None) -> str | None:
        if not result or not isinstance(result, dict):
            return None
        # In v0.3 it is often taskId or task_id; in v0.2 it is id
        return result.get("taskId") or result.get("task_id") or result.get("id")

    @limit_requests_per_minute
    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        if not normalized_conversation:
            raise ValueError("No conversation provided to A2ATarget.")

        latest_message = normalized_conversation[-1]
        message_piece = latest_message.get_piece()
        val = message_piece.converted_value
        if val is None:
            val = message_piece.original_value
        prompt_text = str(val)
        conversation_id = message_piece.conversation_id or ""

        task_id = self._conversation_tasks.get(conversation_id)
        current_dialect: Literal["v03", "v02"] = "v02" if self._dialect == "v02" else "v03"
        payload = self._build_payload(dialect=current_dialect, message_text=prompt_text, task_id=task_id)
        headers = self._build_headers()

        async with httpx.AsyncClient(**self._httpx_client_kwargs) as client:
            resp = await client.post(self._endpoint, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            # Handle automatic dialect fallback if method not found
            if (
                self._dialect == "auto"
                and current_dialect == "v03"
                and isinstance(data, dict)
                and data.get("error", {}).get("code") == RPC_METHOD_NOT_FOUND
            ):
                logger.info("A2A method message/send returned -32601; falling back to tasks/send (dialect 0.2)")
                current_dialect = "v02"
                payload = self._build_payload(dialect=current_dialect, message_text=prompt_text, task_id=task_id)
                retry_resp = await client.post(self._endpoint, json=payload, headers=headers)
                retry_resp.raise_for_status()
                data = retry_resp.json()
                self._dialect = "v02"
            elif self._dialect == "auto":
                self._dialect = current_dialect

        extracted_text = self._extract_response_text(data)

        # Update active task id for this conversation
        if isinstance(data, dict) and "result" in data:
            new_task_id = self._extract_task_id(data.get("result"))
            if new_task_id:
                self._conversation_tasks[conversation_id] = new_task_id

        response_entry = construct_response_from_request(
            request=message_piece,
            response_text_pieces=[extracted_text],
        )
        return [response_entry]

    async def get_agent_card_async(self) -> dict[str, Any]:
        """
        Discover and retrieve the Agent Card manifest from standard discovery paths.

        Checks `/.well-known/agent-card.json` first, falling back to `/agent.json`.

        Returns:
            dict[str, Any]: The parsed JSON Agent Card metadata.
        """
        paths = ["/.well-known/agent-card.json", "/agent.json"]
        headers = self._build_headers()
        base_url = self._endpoint.split("/tasks")[0].split("/message")[0]

        async with httpx.AsyncClient(**self._httpx_client_kwargs) as client:
            for p in paths:
                target_url = urljoin(base_url, p)
                try:
                    resp = await client.get(target_url, headers=headers)
                    if resp.status_code == 200:
                        return resp.json()
                except Exception as exc:
                    logger.debug(f"Failed to fetch agent card from {target_url}: {exc}")
        return {}
