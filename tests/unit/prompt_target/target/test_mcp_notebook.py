# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, chdir
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp import ClientSession
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

from pyrit.common.path import DOCS_CODE_PATH, HOME_PATH
from pyrit.prompt_target import MCPStdioServerConfig, MCPToolProvider


def _notebook_provider() -> MCPToolProvider:
    notebook = json.loads((DOCS_CODE_PATH / "targets" / "2_openai_responses_target.ipynb").read_text(encoding="utf-8"))
    source = next(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "code" and "mcp_tools =" in "".join(cell["source"])
    )
    nodes = [
        node
        for node in ast.parse(source).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        or isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id in {"notes_server", "mcp_tools"} for target in node.targets)
    ]
    namespace: dict[str, object] = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<notebook MCP setup>", "exec"), namespace)
    provider = namespace["mcp_tools"]
    assert isinstance(provider, MCPToolProvider)
    return provider


@pytest.mark.parametrize("working_directory", ["repository", "notebook", "unrelated"])
def test_notebook_mcp_setup_is_cwd_independent(*, working_directory: str, tmp_path: Path) -> None:
    directory = {"repository": HOME_PATH, "notebook": DOCS_CODE_PATH / "targets", "unrelated": tmp_path}
    with chdir(directory[working_directory]):
        provider = _notebook_provider()
        assert isinstance(provider.server_config, MCPStdioServerConfig)
        server = Path(provider.server_config.args[0])
        assert server.is_absolute() and server.is_file()
        assert server == DOCS_CODE_PATH / "targets" / "supporting_assets" / "notes_mcp_server.py"
        assert provider.server_config.args[1:] == ["--transport", "stdio"]


@pytest.mark.parametrize("fail_after_call", [False, True])
async def test_notebook_mcp_session_is_closed_after_execution_async(fail_after_call: bool) -> None:
    provider = await asyncio.to_thread(_notebook_provider)
    session = MagicMock(spec=ClientSession)
    session.list_tools = AsyncMock(
        return_value=ListToolsResult(
            tools=[
                Tool(
                    name="get_note",
                    description="Read a note",
                    input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
                )
            ]
        )
    )
    session.call_tool = AsyncMock(
        return_value=CallToolResult(
            content=[TextContent(type="text", text='{"text":"Welcome to the example notebook."}')],
            structured_content={"text": "Welcome to the example notebook."},
        )
    )
    lifecycle: list[str] = []

    @asynccontextmanager
    async def create_session_async() -> AsyncIterator[ClientSession]:
        lifecycle.append("enter")
        try:
            yield session
        finally:
            lifecycle.append("exit")

    failure = RuntimeError("notebook consumer failed")

    async def execute_async() -> None:
        async with provider.execution_scope_async():
            tools = await provider.get_tools_async()
            note = next(tool for tool in tools if tool.name == "get_note")
            result = await note.execute_async(arguments={"id": "welcome"})
            assert isinstance(result, dict)
            assert result["structured_content"] == {"text": "Welcome to the example notebook."}
            assert lifecycle == ["enter"]
            if fail_after_call:
                raise failure

    with patch.object(provider, "_create_session_async", create_session_async):
        if fail_after_call:
            with pytest.raises(RuntimeError, match="notebook consumer failed") as error:
                await execute_async()
            assert error.value is failure
        else:
            await execute_async()
    assert lifecycle == ["enter", "exit"]
    session.list_tools.assert_awaited_once()
    session.call_tool.assert_awaited_once_with(name="get_note", arguments={"id": "welcome"})
