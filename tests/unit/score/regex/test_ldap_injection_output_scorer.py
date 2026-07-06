# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.score import LDAPInjectionOutputScorer


@pytest.mark.parametrize(
    "text",
    [
        "username=*)(uid=*)",
        ")(cn=*)",
        "admin)|(objectClass=*)",
        "*)(mail=*)",
    ],
)
async def test_ldap_injection_detected(text: str, patch_central_database):
    scorer = LDAPInjectionOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is True


@pytest.mark.parametrize(
    "text",
    [
        "Use parentheses (like this) in math.",
        "The function f(x) = y * 2 returns a value.",
        "Call foo() and bar() in sequence.",
        "LDAP is a directory access protocol.",
        # Regex-group syntax has the same *)( shape as a filter break but no attr= clause.
        r"Python regex r'(\w*)(\s+)' captures a word then whitespace.",
        "match (a*)(b) then read the two groups",
    ],
)
async def test_ldap_injection_not_detected(text: str, patch_central_database):
    scorer = LDAPInjectionOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is False


async def test_ldap_injection_scorer_rationale_includes_pattern_name(patch_central_database):
    scorer = LDAPInjectionOutputScorer()
    score = (await scorer.score_text_async("filter *)(uid=*) bypass"))[0]
    assert score.get_value() is True
    assert "Filter Break Sequence" in score.score_rationale


async def test_ldap_injection_scorer_custom_patterns(patch_central_database):
    custom = {"Marker": r"DANGER_LDAP_\d+"}
    scorer = LDAPInjectionOutputScorer(patterns=custom)

    score = (await scorer.score_text_async("payload DANGER_LDAP_42"))[0]
    assert score.get_value() is True

    score = (await scorer.score_text_async("*)(uid=*)"))[0]
    assert score.get_value() is False


async def test_ldap_injection_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = LDAPInjectionOutputScorer()
        await scorer.score_text_async(text="nothing here")

        memory.add_scores_to_memory.assert_called_once()
