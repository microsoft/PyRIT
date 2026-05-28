# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pyrit.tools.backend import ToolBackend

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pyrit.tools.models import ToolCall

logger = logging.getLogger(__name__)


class LocalToolBackend(ToolBackend):
    """
    In-process :class:`~pyrit.tools.ToolBackend` backed by a name -> ``async def``
    mapping. Useful for unit tests and for embedding small tools inside the
    PyRIT process without standing up an MCP server.

    "Local" here means tools run in PyRIT's own Python process — no
    subprocess, no IPC, no wire protocol. Contrast with
    :class:`~pyrit.tools.MCPToolBackend` (lands in C3), which proxies
    dispatch through one or more MCP servers reached via JSON-RPC.

    The backend dispatches sequentially in declaration order. Tool-side
    failures (raised exceptions, missing names, allow-list rejections)
    are converted into structured error envelopes so the tool loop can
    forward them back to the model as ``function_call_output`` content
    rather than aborting the conversation.
    """

    def __init__(
        self,
        *,
        callables: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]],
        schemas: list[dict[str, Any]] | None = None,
        allowed_tools: set[str] | None = None,
        fail_on_missing_function: bool = True,
    ) -> None:
        """
        Initialize the backend.

        Args:
            callables (dict[str, Callable[[dict[str, Any]], Awaitable[Any]]]):
                Map from tool name to an ``async def`` that accepts a parsed
                arguments dict and returns the tool result. Results are
                serialized by the tool loop via :func:`json.dumps`.
            schemas (list[dict[str, Any]] | None): JSON-schema descriptors
                injected into the target's request body. Defaults to an empty
                list when omitted.
            allowed_tools (set[str] | None): Optional allow-list of tool
                names; calls whose name is not in this set surface as
                ``tool_not_allowed`` envelopes without invoking the callable.
                Defaults to None (no allow-list; every registered tool is
                callable).
            fail_on_missing_function (bool): When True (default), an unknown
                tool name raises :class:`KeyError`. When False, the backend
                returns a ``tool_not_registered`` envelope so the model can
                recover.
        """
        self._callables = dict(callables)
        self._schemas: list[dict[str, Any]] = list(schemas) if schemas is not None else []
        self._allowed_tools = set(allowed_tools) if allowed_tools is not None else None
        self._fail_on_missing_function = fail_on_missing_function

    @property
    def schemas(self) -> list[dict[str, Any]]:
        """The JSON-schema descriptors for the tools in this backend."""
        return list(self._schemas)

    async def dispatch_async(self, call: ToolCall) -> dict[str, Any]:
        """
        Dispatch a single tool call. Tool failures are converted into
        structured envelopes; only configuration errors (missing tool with
        ``fail_on_missing_function=True``) propagate as exceptions.

        Args:
            call (ToolCall): The call to dispatch.

        Returns:
            dict[str, Any]: The tool's result, or a structured error envelope.

        Raises:
            KeyError: When the tool name is not registered and
                ``fail_on_missing_function=True``.
        """
        if self._allowed_tools is not None and call.name not in self._allowed_tools:
            logger.info("Rejecting disallowed tool call: %s", call.name)
            return {
                "error": "tool_not_allowed",
                "tool": call.name,
                "allowed_tools": sorted(self._allowed_tools),
            }

        fn = self._callables.get(call.name)
        if fn is None:
            if self._fail_on_missing_function:
                raise KeyError(f"Tool '{call.name}' is not registered.")
            available = sorted(self._callables.keys())
            logger.warning("Tool '%s' not registered. Available: %s", call.name, available)
            return {
                "error": "tool_not_registered",
                "tool": call.name,
                "available_tools": available,
            }

        try:
            result = await fn(call.arguments)
        except Exception as ex:
            logger.warning("Tool '%s' raised %s: %s", call.name, type(ex).__name__, ex)
            return {
                "error": "tool_execution_failed",
                "tool": call.name,
                "detail": str(ex),
            }
        return result if isinstance(result, dict) else {"result": result}
