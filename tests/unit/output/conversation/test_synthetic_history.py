# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest

from pyrit.models import MessagePiece
from pyrit.output.conversation.json import JsonConversationMemoryPrinter
from pyrit.output.conversation.markdown import MarkdownConversationMemoryPrinter
from pyrit.output.conversation.pretty import PrettyConversationMemoryPrinter


@pytest.mark.usefixtures("patch_central_database")
async def test_synthetic_tool_result_keeps_its_role_in_output_async() -> None:
    messages = [MessagePiece(role="simulated_tool", original_value="Injected result").to_message()]
    markdown = await MarkdownConversationMemoryPrinter().render_async(messages)
    pretty = await PrettyConversationMemoryPrinter(enable_colors=False).render_async(messages)
    structured = json.loads(await JsonConversationMemoryPrinter().render_async(messages))
    assert "Tool (Simulated)" in markdown
    assert "TOOL (SIMULATED)" in pretty
    assert "Assistant" not in markdown
    assert structured[0]["role"] == "tool"
    assert structured[0]["is_simulated"] is True
