# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Stdio-transport client for the Model Context Protocol (MCP).

This module is the wire-protocol half of PyRIT's MCP integration. It
sits below :class:`~pyrit.tools.MCPToolBackend` (which composes one
:class:`MCPClient` per configured server and handles cross-server
routing) and above the upstream ``mcp`` Python SDK (which owns the
JSON-RPC framing, capability negotiation, and asyncio task plumbing).

The three :class:`MCPServerSpec` variants describe *where* the server
runs. Only :class:`LocalMCPServerSpec` is implemented in this commit:

* :class:`LocalMCPServerSpec` — spawn the server as a child process and
  speak JSON-RPC over its stdin/stdout.
* :class:`RemoteMCPServerSpec` — HTTP/SSE transport against a hosted
  server. Stub: ``connect_async`` raises ``NotImplementedError``.
* :class:`DockerMCPServerSpec` — stdio over ``docker run -i`` against a
  hardened sandbox container. Stub: ``connect_async`` raises
  ``NotImplementedError``. Implementation lands in the follow-up
  sandbox PR.

The stub variants are intentionally part of the type union today so
downstream code can be written against the eventual API without
forcing a Union expansion later.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

if TYPE_CHECKING:
    from pyrit.tools.models import ToolCall

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalMCPServerSpec:
    """
    Spec for an MCP server spawned as a child process and reached via
    stdio JSON-RPC.

    Attributes:
        command (str): The interpreter or binary to exec (e.g. ``"python"``).
        args (tuple[str, ...]): Arguments passed to *command*, in order.
        env (dict[str, str] | None): Environment overlay for the child
            process. ``None`` (default) inherits PyRIT's environment.
        name_prefix (str | None): When set, every tool advertised by the
            server is registered as ``f"{name_prefix}{tool_name}"`` in
            the parent :class:`~pyrit.tools.MCPToolBackend`. Used to
            disambiguate two servers that expose the same tool name.
        timeout_seconds (float): Per-call timeout, enforced by
            :meth:`MCPClient.dispatch_async`. Defaults to 30 seconds.
    """

    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    name_prefix: str | None = None
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class RemoteMCPServerSpec:
    """
    Spec for an MCP server reached over HTTP / SSE. **Not implemented**
    in this PR — :meth:`MCPClient.connect_async` raises
    :class:`NotImplementedError`. Tracked by ``# TODO(mcp-http-transport)``.

    Attributes:
        url (str): The base URL of the MCP server.
        name_prefix (str | None): Same semantics as
            :attr:`LocalMCPServerSpec.name_prefix`.
        timeout_seconds (float): Per-call timeout.
    """

    url: str
    name_prefix: str | None = None
    timeout_seconds: float = 30.0


# TODO(sandbox-provider) — DockerMCPServerSpec stub here; implementation lands in follow-up PR.
@dataclass(frozen=True)
class DockerMCPServerSpec:
    """
    Spec for an MCP server hosted inside a hardened Docker container.

    **NOT IMPLEMENTED IN THIS PR.** Reached via stdio over ``docker run -i``.

    Expected behavior in the follow-up sandbox PR:

    * One container per spec instance, managed by a process-wide
      ``SandboxPool``.
    * Image is built lazily, keyed by ``sha256(Dockerfile + build_context)``,
      and cached across attacks; no rebuild unless missing or explicitly
      overridden.
    * Container is recreated from the cached image at attack and scenario
      boundaries (filesystem returns to baseline every time).
    * Network access governed by ``NetworkProfile`` (default ``"none"`` =
      ``--network=none``).
    * Container runs as a non-root UID with ``--cap-drop=ALL``, a read-only
      root filesystem, and an in-container MCP server exposing
      ``run_shell(cmd, timeout_seconds)``.

    Attributes:
        image (str): Docker image tag (e.g. ``"pyrit-sandbox:base"``).
        network_profile (str): ``NetworkProfile`` name; ``"none"`` (default)
            launches the container with ``--network=none``.
        name_prefix (str | None): Same semantics as
            :attr:`LocalMCPServerSpec.name_prefix`.
        timeout_seconds (float): Per-call timeout.

    Future fields (deferred to the follow-up sandbox PR): ``memory_limit``,
    ``cpu_limit``, ``pids_limit``, ``env``, ``mounts``, ``command_override``.
    """

    image: str
    network_profile: str = "none"
    name_prefix: str | None = None
    timeout_seconds: float = 30.0


MCPServerSpec = LocalMCPServerSpec | RemoteMCPServerSpec | DockerMCPServerSpec


def _to_input_schema_dict(input_schema: Any) -> dict[str, Any]:
    """
    Coerce the SDK's tool ``inputSchema`` (pydantic model or dict) into a plain dict.

    Returns:
        dict[str, Any]: A plain-dict copy of *input_schema*, or an empty
            object schema when *input_schema* is None or of an unrecognized type.
    """
    if input_schema is None:
        return {"type": "object", "properties": {}}
    if hasattr(input_schema, "model_dump"):
        return input_schema.model_dump()
    if isinstance(input_schema, dict):
        return dict(input_schema)
    return {"type": "object", "properties": {}}


def _flatten_content(content: list[Any]) -> str:
    """
    Concatenate the text portions of an MCP ``CallToolResult.content`` list.

    Returns:
        str: Concatenated ``.text`` values from each content item, in order.
    """
    pieces: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text is not None:
            pieces.append(text)
        elif isinstance(item, dict) and "text" in item:
            pieces.append(item["text"])
    return "".join(pieces)


class MCPClient:
    """
    A single MCP-server session.

    The client owns the lifetime of one server's transport stack and
    exposes a uniform :meth:`dispatch_async` regardless of which
    :class:`MCPServerSpec` variant it was constructed from. Composition
    across multiple servers (routing, schema aggregation, allow-lists)
    is the responsibility of :class:`~pyrit.tools.MCPToolBackend`.

    Lifecycle:

    * :meth:`connect_async` spawns the subprocess (for
      :class:`LocalMCPServerSpec`), runs the MCP handshake, and caches
      ``tools/list`` results.
    * :meth:`dispatch_async` issues one ``tools/call`` and returns a
      structured envelope (success or error).
    * :meth:`close_async` tears down the transport stack.

    The class is usable as an async context manager.
    """

    def __init__(self, *, spec: MCPServerSpec) -> None:
        """
        Initialize the client around *spec*. Does not connect; call
        :meth:`connect_async` (or use the async context-manager form) to start
        the transport stack.
        """
        self._spec = spec
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self._tools: list[Any] = []

    @property
    def spec(self) -> MCPServerSpec:
        """The :class:`MCPServerSpec` this client was constructed with."""
        return self._spec

    @property
    def schemas(self) -> list[dict[str, Any]]:
        """
        JSON schemas for every tool the server advertises.

        Each schema is shaped ``{"name", "description", "parameters"}``.
        The optional :attr:`LocalMCPServerSpec.name_prefix` is applied
        here so a backend that owns this client sees the prefixed name.
        """
        prefix = getattr(self._spec, "name_prefix", None) or ""
        return [
            {
                "name": f"{prefix}{tool.name}",
                "description": tool.description or "",
                "parameters": _to_input_schema_dict(tool.inputSchema),
            }
            for tool in self._tools
        ]

    @property
    def tool_names(self) -> list[str]:
        """Tool names with the spec's :attr:`name_prefix` applied."""
        return [s["name"] for s in self.schemas]

    def _strip_prefix(self, name: str) -> str:
        prefix = getattr(self._spec, "name_prefix", None) or ""
        if prefix and name.startswith(prefix):
            return name[len(prefix) :]
        return name

    async def connect_async(self) -> None:
        """Establish the transport, run the handshake, and cache schemas."""
        if isinstance(self._spec, RemoteMCPServerSpec):
            raise NotImplementedError(
                "HTTP/SSE transport ships in a follow-up PR. "
                "RemoteMCPServerSpec is declared today so user code can target the eventual API."
            )
        if isinstance(self._spec, DockerMCPServerSpec):
            raise NotImplementedError(
                "Docker sandbox transport ships in a follow-up PR. "
                "DockerMCPServerSpec runs the MCP server inside a hardened "
                "Debian container reached via stdio over `docker run -i`, "
                "managed by a process-wide SandboxPool with image caching and "
                "per-attack container recreation."
            )

        assert isinstance(self._spec, LocalMCPServerSpec)
        params = StdioServerParameters(
            command=self._spec.command,
            args=list(self._spec.args),
            env=self._spec.env,
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        result = await session.list_tools()
        self._session = session
        self._tools = list(result.tools)

    async def close_async(self) -> None:
        """Tear down the transport stack. Idempotent; safe to call before connect."""
        try:
            await self._stack.aclose()
        except Exception as ex:  # noqa: BLE001 — close should never raise into the caller.
            logger.warning("Error tearing down MCP client stack: %s", ex)
        finally:
            self._stack = AsyncExitStack()
            self._session = None
            self._tools = []

    async def __aenter__(self) -> MCPClient:
        """
        Connect the transport stack and return *self*.

        Returns:
            MCPClient: *self*, ready to dispatch tool calls.
        """
        await self.connect_async()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """Tear down the transport stack."""
        await self.close_async()

    async def dispatch_async(self, call: ToolCall) -> dict[str, Any]:
        """
        Issue one ``tools/call`` and return a structured envelope.

        Envelope shape:

        * Success: ``{"is_error": False, "content": str, "tool": name}``.
        * Timeout: ``{"is_error": True, "error": "tool_timeout", "tool": name, ...}``.
        * Server-reported error: ``{"is_error": True, "error": "tool_execution_failed", "tool": name, ...}``.

        Tool-side failures are converted to envelopes; only programmer
        errors (calling before :meth:`connect_async`) raise.

        Args:
            call (ToolCall): The call to dispatch. The advertised
                ``name_prefix`` (if any) is stripped before contacting the server.

        Returns:
            dict[str, Any]: One of the envelope shapes documented above.

        Raises:
            RuntimeError: When the client has not been connected.
        """
        if self._session is None:
            raise RuntimeError("MCPClient is not connected; call connect_async first.")

        server_side_name = self._strip_prefix(call.name)
        timeout = getattr(self._spec, "timeout_seconds", 30.0)
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(server_side_name, arguments=dict(call.arguments)),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "MCP tool '%s' timed out after %.2fs",
                call.name,
                timeout,
            )
            return {
                "is_error": True,
                "error": "tool_timeout",
                "tool": call.name,
                "timeout_seconds": timeout,
            }
        except Exception as ex:  # noqa: BLE001 — wrap and surface as envelope.
            logger.warning(
                "MCP tool '%s' raised %s: %s",
                call.name,
                type(ex).__name__,
                ex,
            )
            return {
                "is_error": True,
                "error": "tool_execution_failed",
                "tool": call.name,
                "detail": str(ex),
            }

        content_text = _flatten_content(list(result.content))
        is_error = bool(getattr(result, "isError", False))
        envelope: dict[str, Any] = {
            "is_error": is_error,
            "content": content_text,
            "tool": call.name,
        }
        if is_error:
            envelope["error"] = "tool_execution_failed"
        return envelope


__all__ = [
    "DockerMCPServerSpec",
    "LocalMCPServerSpec",
    "MCPClient",
    "MCPServerSpec",
    "RemoteMCPServerSpec",
]
