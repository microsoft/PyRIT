# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
import asyncio
import json
from contextlib import chdir
from pathlib import Path
from unittest.mock import AsyncMock, patch

import mcp.client.stdio as stdio_module
import pytest
from anyio.abc import Process

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
        assert provider.server_config.args[1:] == ["--transport", "stdio"]


@pytest.mark.parametrize("fail_after_call", [False, True])
async def test_notebook_mcp_server_is_closed_after_execution_async(fail_after_call: bool) -> None:
    provider = await asyncio.to_thread(_notebook_provider)
    processes: list[Process] = []
    create_process = stdio_module._create_platform_compatible_process

    async def capture_process_async(**kwargs: object) -> Process:
        process = await create_process(**kwargs)
        assert isinstance(process, Process)
        processes.append(process)
        return process

    failure = RuntimeError("notebook consumer failed")

    async def execute_async() -> None:
        async with provider.execution_scope_async():
            tools = await provider.get_tools_async()
            note = next(tool for tool in tools if tool.name == "get_note")
            result = await note.execute_async(arguments={"id": "welcome"})
            assert isinstance(result, dict)
            assert result["structured_content"] == {"text": "Welcome to the example notebook."}
            assert processes and all(process.returncode is None for process in processes)
            if fail_after_call:
                raise failure

    with patch.object(
        stdio_module, "_create_platform_compatible_process", AsyncMock(side_effect=capture_process_async)
    ):
        if fail_after_call:
            with pytest.raises(ExceptionGroup) as error:
                await asyncio.wait_for(execute_async(), timeout=30)
            assert error.value.subgroup(lambda item: item is failure) is not None
        else:
            await asyncio.wait_for(execute_async(), timeout=30)
    assert len(processes) == 1
    assert all(process.returncode is not None for process in processes)
