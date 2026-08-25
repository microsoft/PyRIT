# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.score import OpenRedirectOutputScorer


@pytest.mark.parametrize(
    "text",
    [
        "redirect=//evil.com/steal",
        "next=%2f%2fattacker.com",
        "returnto=%2f%2fattacker.com",
        "Location: //attacker.example",
        "https://trusted.com@evil.com/login",
    ],
)
async def test_open_redirect_detected(text: str, patch_central_database):
    scorer = OpenRedirectOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is True


@pytest.mark.parametrize(
    "text",
    [
        "Visit https://example.com for more info.",
        "The redirect happens automatically after login.",
        "Email me at user@example.com anytime.",
        "Set the destination city in your settings.",
    ],
)
async def test_open_redirect_not_detected(text: str, patch_central_database):
    scorer = OpenRedirectOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is False


async def test_open_redirect_scorer_rationale_includes_pattern_name(patch_central_database):
    scorer = OpenRedirectOutputScorer()
    score = (await scorer.score_text_async("redirect=//evil.com/steal"))[0]
    assert score.get_value() is True
    assert "Protocol-Relative Redirect Param" in score.score_rationale


async def test_open_redirect_scorer_custom_patterns(patch_central_database):
    custom = {"Marker": r"DANGER_REDIR_\d+"}
    scorer = OpenRedirectOutputScorer(patterns=custom)

    score = (await scorer.score_text_async("payload DANGER_REDIR_42"))[0]
    assert score.get_value() is True

    score = (await scorer.score_text_async("redirect=//evil.com/steal"))[0]
    assert score.get_value() is False


async def test_open_redirect_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = OpenRedirectOutputScorer()
        await scorer.score_text_async(text="nothing here")

        memory.add_scores_to_memory.assert_called_once()
