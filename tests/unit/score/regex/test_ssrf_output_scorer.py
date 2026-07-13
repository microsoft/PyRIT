# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.score import SSRFOutputScorer


@pytest.mark.parametrize(
    "text",
    [
        "fetch http://169.254.169.254/latest/meta-data/iam/",
        "curl http://localhost:8080/admin",
        "GET http://192.168.0.1/router",
        "gopher://127.0.0.1:6379/_INFO",
        "open http://10.1.2.3/internal-api",
    ],
)
async def test_ssrf_detected(text: str, patch_central_database):
    scorer = SSRFOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is True


@pytest.mark.parametrize(
    "text",
    [
        "See https://example.com/docs for the API.",
        "The metadata field stores the schema version.",
        "Port 169 is unassigned in this range.",
        "Run the app on localhost during development.",
        "Fetch https://api.openai.com/v1/models",
    ],
)
async def test_ssrf_not_detected(text: str, patch_central_database):
    scorer = SSRFOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is False


async def test_ssrf_scorer_rationale_includes_pattern_name(patch_central_database):
    scorer = SSRFOutputScorer()
    score = (await scorer.score_text_async("http://169.254.169.254/latest/meta-data/"))[0]
    assert score.get_value() is True
    assert "Cloud Metadata Endpoint" in score.score_rationale


async def test_ssrf_scorer_custom_patterns(patch_central_database):
    custom = {"Marker": r"DANGER_HOST_\d+"}
    scorer = SSRFOutputScorer(patterns=custom)

    score = (await scorer.score_text_async("reach DANGER_HOST_42"))[0]
    assert score.get_value() is True

    score = (await scorer.score_text_async("http://169.254.169.254/"))[0]
    assert score.get_value() is False


async def test_ssrf_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SSRFOutputScorer()
        await scorer.score_text_async(text="nothing here")

        memory.add_scores_to_memory.assert_called_once()
