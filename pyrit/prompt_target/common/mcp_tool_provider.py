# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import PaginatedRequestParams
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from pyrit.prompt_target.common.tool_provider import Tool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from mcp.types import CallToolResult
    from mcp.types import Tool as MCPToolDefinition


class MCPStreamableHTTPServerConfig(BaseModel):
    """Configuration for an MCP Streamable HTTP server."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["http", "streamable-http"] = "http"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("MCP Streamable HTTP server URL must start with http:// or https://")
        return value


class MCPStdioServerConfig(BaseModel):
    """Configuration for an MCP stdio server process."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["stdio"] = "stdio"
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: Path | None = None


MCPServerConfig = Annotated[MCPStreamableHTTPServerConfig | MCPStdioServerConfig, Field(discriminator="type")]


class MCPConfig(BaseModel):
    """Validated VS Code-style MCP server configuration."""

    model_config = ConfigDict(extra="forbid")

    servers: dict[str, MCPServerConfig] = Field(
        validation_alias=AliasChoices("servers", "mcpServers"),
    )

    @model_validator(mode="before")
    @classmethod
    def _infer_legacy_transport(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        raw_servers = value.get("servers", value.get("mcpServers"))
        if not isinstance(raw_servers, Mapping):
            return value

        servers: dict[object, object] = {}
        for name, raw_server in raw_servers.items():
            if isinstance(raw_server, Mapping) and "type" not in raw_server:
                server = dict(raw_server)
                server["type"] = "stdio" if "command" in server else "http"
                servers[name] = server
            else:
                servers[name] = raw_server
        key = "servers" if "servers" in value else "mcpServers"
        return {**value, key: servers}


class _MCPTool(Tool):
    """An executable tool discovered from an MCP server."""

    def __init__(self, *, provider: MCPToolProvider, definition: MCPToolDefinition) -> None:
        super().__init__(
            name=definition.name,
            description=definition.description or "",
            parameters=dict(definition.inputSchema),
        )
        self._provider = provider

    async def execute_async(self, *, arguments: dict[str, object]) -> object:
        return await self._provider.call_tool_async(name=self.name, arguments=arguments)


class MCPToolProvider:
    """Discover and execute tools exposed by one MCP server."""

    _HTTP_TIMEOUT_SECONDS = 30.0
    _HTTP_READ_TIMEOUT_SECONDS = 300.0

    def __init__(
        self,
        *,
        server_name: str,
        server_config: MCPServerConfig,
    ) -> None:
        """
        Initialize the provider for one MCP server.

        Raises:
            ValueError: If the server name is empty.
        """
        if not server_name.strip():
            raise ValueError("MCP server name must not be empty")

        self._server_name = server_name
        self._server_config = server_config
        self._tools: list[MCPToolDefinition] | None = None
        self._active_session: ContextVar[ClientSession | None] = ContextVar(
            f"{self.__class__.__name__}.{server_name}.active_session",
            default=None,
        )

    @classmethod
    def from_config_file(
        cls,
        *,
        config_path: str | Path,
        server_name: str,
    ) -> MCPToolProvider:
        """
        Create a provider from a VS Code-style ``mcp.json`` configuration.

        Both the ``servers`` key used by VS Code and the legacy ``mcpServers``
        key are accepted. Streamable HTTP and stdio servers are supported.

        Returns:
            A provider configured for the selected server.

        Raises:
            FileNotFoundError: If the configuration file does not exist.
            ValueError: If the configuration or selected server is invalid.
        """
        path = Path(config_path)
        try:
            raw_config: object = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FileNotFoundError(f"MCP config file not found: {path}") from None
        except json.JSONDecodeError as exc:
            raise ValueError(f"MCP config file is not valid JSON: {path}") from exc

        return cls.from_config(config=MCPConfig.model_validate(raw_config), server_name=server_name)

    @classmethod
    def from_config(
        cls,
        *,
        config: MCPConfig | Mapping[str, object],
        server_name: str,
    ) -> MCPToolProvider:
        """
        Create a provider from an in-memory VS Code-style MCP configuration.

        Returns:
            A provider configured for the selected server.

        Raises:
            ValueError: If the configuration or selected server is invalid.
        """
        parsed_config = config if isinstance(config, MCPConfig) else MCPConfig.model_validate(config)
        server = parsed_config.servers.get(server_name)
        if server is None:
            available = ", ".join(sorted(parsed_config.servers)) or "<none>"
            raise ValueError(f"MCP server '{server_name}' was not found. Available servers: {available}")

        return cls(server_name=server_name, server_config=server)

    @property
    def server_name(self) -> str:
        """The configured MCP server name."""
        return self._server_name

    @property
    def server_config(self) -> MCPServerConfig:
        """The validated MCP server configuration."""
        return self._server_config

    @property
    def server_url(self) -> str | None:
        """The configured URL, or ``None`` for a stdio server."""
        if isinstance(self._server_config, MCPStreamableHTTPServerConfig):
            return self._server_config.url
        return None

    @property
    def identifier(self) -> dict[str, object]:
        """Non-secret MCP server configuration that affects behavior."""
        return {
            "type": self.__class__.__name__,
            "server_name": self._server_name,
            "transport": self._server_config.type,
        }

    @asynccontextmanager
    async def _create_session_async(self) -> AsyncIterator[ClientSession]:
        if isinstance(self._server_config, MCPStreamableHTTPServerConfig):
            timeout = httpx.Timeout(self._HTTP_TIMEOUT_SECONDS, read=self._HTTP_READ_TIMEOUT_SECONDS)
            async with httpx.AsyncClient(headers=self._server_config.headers, timeout=timeout) as http_client:
                async with streamable_http_client(self._server_config.url, http_client=http_client) as (
                    read_stream,
                    write_stream,
                    _,
                ):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        yield session
            return

        server = StdioServerParameters(
            command=self._server_config.command,
            args=self._server_config.args,
            env=self._server_config.env,
            cwd=self._server_config.cwd,
        )
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    @asynccontextmanager
    async def execution_scope_async(self) -> AsyncIterator[None]:
        """Keep one MCP session open for a target send."""
        async with self._create_session_async() as session:
            token = self._active_session.set(session)
            try:
                yield
            finally:
                self._active_session.reset(token)

    async def list_tools_async(self, *, refresh: bool = False) -> list[MCPToolDefinition]:
        """
        List MCP tools, caching the result for the provider lifetime.

        Returns:
            The tools advertised by the server.

        Raises:
            ValueError: If the server advertises a tool name more than once.
        """
        if self._tools is not None and not refresh:
            return list(self._tools)

        active_session = self._active_session.get()
        if active_session is not None:
            tools = await self._list_tools_from_session_async(session=active_session)
        else:
            async with self._create_session_async() as session:
                tools = await self._list_tools_from_session_async(session=session)

        names = [tool.name for tool in tools]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"MCP server '{self._server_name}' returned duplicate tool names: {duplicates}")

        self._tools = tools
        return list(tools)

    async def call_tool_async(self, *, name: str, arguments: dict[str, object]) -> dict[str, object]:
        """
        Execute an MCP tool.

        Returns:
            The MCP call result as JSON-compatible data.

        Raises:
            KeyError: If the requested tool was not advertised by the server.
        """
        known_tools = await self.list_tools_async()
        if name not in {tool.name for tool in known_tools}:
            available = sorted(tool.name for tool in known_tools)
            raise KeyError(f"MCP tool '{name}' is not available. Available tools: {available}")

        active_session = self._active_session.get()
        if active_session is not None:
            result = await active_session.call_tool(name=name, arguments=arguments)
        else:
            async with self._create_session_async() as session:
                result = await session.call_tool(name=name, arguments=arguments)

        return self._serialize_call_result(result=result)

    async def _list_tools_from_session_async(self, *, session: ClientSession) -> list[MCPToolDefinition]:
        tools: list[MCPToolDefinition] = []
        cursor: str | None = None
        while True:
            result = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
            tools.extend(result.tools)
            cursor = result.nextCursor
            if cursor is None:
                return tools

    async def get_tools_async(self) -> list[Tool]:
        """
        Discover executable MCP tools.

        Returns:
            Tools that carry both their definitions and execution behavior.
        """
        return [_MCPTool(provider=self, definition=definition) for definition in await self.list_tools_async()]

    @staticmethod
    def _serialize_call_result(*, result: CallToolResult) -> dict[str, object]:
        serialized: dict[str, object] = {
            "content": [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in result.content],
            "is_error": result.isError,
        }
        if result.structuredContent is not None:
            serialized["structured_content"] = result.structuredContent
        return serialized
