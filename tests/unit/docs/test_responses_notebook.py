# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
import sys
from collections.abc import Iterator
from contextlib import chdir
from pathlib import Path
from unittest.mock import AsyncMock, patch

import jupytext
import pytest
from mcp.client import stdio

from pyrit.common.path import DOCS_CODE_PATH, HOME_PATH
from pyrit.prompt_target import MCPStdioServerConfig, MCPToolProvider

_NOTEBOOK_PATH = DOCS_CODE_PATH / "targets" / "2_openai_responses_target.ipynb"


def _load_notebook_provider() -> MCPToolProvider:
    notebook = jupytext.read(_NOTEBOOK_PATH)
    cell = next(cell for cell in notebook.cells if cell.cell_type == "code" and "mcp_tools =" in cell.source)
    setup_nodes = [
        node
        for node in ast.parse(cell.source).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        or (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "mcp_tools" for target in node.targets)
        )
    ]
    namespace: dict[str, object] = {}
    # Run the notebook's provider setup without loading credentials or calling a model.
    exec(compile(ast.Module(body=setup_nodes, type_ignores=[]), str(_NOTEBOOK_PATH), "exec"), namespace)
    provider = namespace["mcp_tools"]
    assert isinstance(provider, MCPToolProvider)
    return provider


def test_responses_notebook_sources_match() -> None:
    notebook = jupytext.read(_NOTEBOOK_PATH)
    script = jupytext.read(_NOTEBOOK_PATH.with_suffix(".py"))
    assert [(cell.cell_type, cell.source) for cell in notebook.cells] == [
        (cell.cell_type, cell.source) for cell in script.cells
    ]


@pytest.fixture(params=["repository", "notebook", "unrelated"])
def notebook_provider(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[MCPToolProvider]:
    directory = {
        "repository": HOME_PATH,
        "notebook": _NOTEBOOK_PATH.parent,
        "unrelated": tmp_path,
    }[request.param]
    with chdir(directory):
        provider = _load_notebook_provider()
        assert isinstance(provider.server_config, MCPStdioServerConfig)
        assert provider.server_config.command == sys.executable
        assert Path(provider.server_config.args[0]).is_absolute()
        assert Path(provider.server_config.args[0]).is_file()
        yield provider


@pytest.fixture
def server_shutdown() -> Iterator[AsyncMock]:
    with patch.object(stdio, "_stop_server_process", wraps=stdio._stop_server_process) as shutdown:
        yield shutdown


async def test_responses_notebook_mcp_tools_async(
    *, notebook_provider: MCPToolProvider, server_shutdown: AsyncMock
) -> None:
    async with notebook_provider.execution_scope_async():
        tools = await notebook_provider.get_tools_async()
        assert [tool.name for tool in tools] == ["get_note"]
        result = await tools[0].execute_async(arguments={"id": "welcome"})
        assert isinstance(result, dict)
        assert result["is_error"] is False
        assert result["structured_content"] == {"text": "Welcome to the example notebook."}
        error = await tools[0].execute_async(arguments={"id": "missing"})
        assert isinstance(error, dict)
        assert error["is_error"] is True

    server_shutdown.assert_awaited_once()
    assert server_shutdown.call_args.args[0].returncode is not None


async def test_responses_notebook_mcp_scope_recovers_after_error_async(
    *, notebook_provider: MCPToolProvider, server_shutdown: AsyncMock
) -> None:
    with pytest.RaisesGroup(pytest.RaisesExc(RuntimeError, match="model request failed"), flatten_subgroups=True):
        async with notebook_provider.execution_scope_async():
            await notebook_provider.get_tools_async()
            raise RuntimeError("model request failed")

    server_shutdown.assert_awaited_once()
    assert server_shutdown.call_args.args[0].returncode is not None

    async with notebook_provider.execution_scope_async():
        result = await notebook_provider.call_tool_async(name="get_note", arguments={"id": "welcome"})
        assert result["is_error"] is False
        assert result["structured_content"] == {"text": "Welcome to the example notebook."}

    assert server_shutdown.await_count == 2
    assert server_shutdown.call_args.args[0].returncode is not None
