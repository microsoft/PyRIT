# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.score import MarkdownInjectionScorer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "![sdf](www.google.com)",
        "![Drag Racing](Dragster.jpg)",
        "![][image_ref_a32ff4ads]",
    ],
)
async def test_md_injection_detected(text: str, patch_central_database):
    scorer = MarkdownInjectionScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is True


@pytest.mark.asyncio
async def test_md_injection_not_detected(patch_central_database):
    classifier = MarkdownInjectionScorer()
    text = "this is a test"
    score = await classifier.score_text_async(text)
    assert score[0].get_value() is False


@pytest.mark.asyncio
async def test_md_injection_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = MarkdownInjectionScorer()
        await scorer.score_text_async(text="string")

        memory.add_scores_to_memory.assert_called_once()
