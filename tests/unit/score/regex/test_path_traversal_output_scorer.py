# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.score import PathTraversalOutputScorer


@pytest.mark.parametrize(
    "text",
    [
        "Read this file: ../../etc/passwd",
        "Try the path ../../../etc/shadow",
        "Windows: ../../windows\\system32\\config",
        "Process: ../../proc/self/environ",
    ],
)
async def test_path_traversal_detected(text: str, patch_central_database):
    scorer = PathTraversalOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is True


@pytest.mark.parametrize(
    "text",
    [
        # Single ../ — not a multi-segment walk.
        "Open ../README.md to see the docs.",
        # Multi-segment ../ but to a non-sensitive target — keeps FP rate low.
        "Look at ../../docs/getting_started.md",
        # Sensitive path words without the ../ traversal.
        "The /etc/passwd file lists local users.",
        # Plain prose mentioning system32.
        "Windows system32 holds the OS DLLs.",
        # A legitimate explanation about /proc/self.
        "The /proc/self directory is a symlink to the current process.",
    ],
)
async def test_path_traversal_not_detected(text: str, patch_central_database):
    scorer = PathTraversalOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is False


async def test_path_traversal_scorer_rationale_includes_pattern_name(patch_central_database):
    scorer = PathTraversalOutputScorer()
    score = (await scorer.score_text_async("Try ../../etc/passwd"))[0]
    assert score.get_value() is True
    assert "Path Traversal to Sensitive File" in score.score_rationale


async def test_path_traversal_scorer_custom_patterns(patch_central_database):
    custom = {"Marker": r"TRAVERSAL_MARKER_\d+"}
    scorer = PathTraversalOutputScorer(patterns=custom)

    score = (await scorer.score_text_async("TRAVERSAL_MARKER_3"))[0]
    assert score.get_value() is True

    score = (await scorer.score_text_async("../../etc/passwd"))[0]
    assert score.get_value() is False


async def test_path_traversal_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = PathTraversalOutputScorer()
        await scorer.score_text_async(text="nothing here")

        memory.add_scores_to_memory.assert_called_once()
