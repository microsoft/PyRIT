# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.score import LDAPInjectionOutputScorer


@pytest.mark.parametrize(
    "text",
    [
        "username=*)(uid=*)",
        ")(cn=*)",
        "admin)|(objectClass=*)",
        "*)(mail=*)",
        "admin)(!(password=*))",
        "admin)|(!(uid=*))",
        "admin)&(!(objectClass=*))",
        "*)((!(mail=*)))",
        "admin*)%00",
        "admin*)\\x00",
        "admin*)\\0",
        "admin*)\\00",
        "admin*)\x00",
        "*)(uid=*))%00",
        "admin)%00",
        "admin)\\x00",
        "admin)\\0",
        "admin)\x00",
        "admin)(!(password = *))",
        ")(!(objectClass=void))",
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
        "100% of the time * 0",
        "multiplication *) without null byte",
        # Non-null escapes (octal / hex) must not match the null-byte termination pattern
        r"user)\01",
        r"user)\07",
        r"user)\012",
        r"user)\077",
    ],
)
async def test_ldap_injection_not_detected(text: str, patch_central_database):
    scorer = LDAPInjectionOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is False


@pytest.mark.parametrize("suffix", ["active", "dead", "FACE", "greeting"])
@pytest.mark.parametrize("null_marker", [r"\00", r"\x00", "%00", "\x00"])
@pytest.mark.usefixtures("patch_central_database")
async def test_ldap_injection_complete_null_escape_with_suffix_async(
    *, null_marker: str, suffix: str, sqlite_instance: SQLiteMemory
) -> None:
    scorer = LDAPInjectionOutputScorer()
    score = (await scorer.score_text_async(f"user){null_marker}{suffix}"))[0]

    assert score.get_value() is True
    assert score.score_category == ["security"]
    assert score.score_rationale == "Matched: Null Byte Filter Termination"
    assert score.score_metadata == {}

    stored_scores = await sqlite_instance.get_scores_async(score_ids=[str(score.id)])
    assert len(stored_scores) == 1
    stored_score = stored_scores[0]
    assert stored_score.get_value() is True
    assert stored_score.score_category == score.score_category
    assert stored_score.score_rationale == score.score_rationale
    assert stored_score.score_metadata == score.score_metadata


async def test_ldap_injection_long_plain_text_not_detected(patch_central_database):
    scorer = LDAPInjectionOutputScorer()
    long_text = "a" * 16000
    score = (await scorer.score_text_async(long_text))[0]
    assert score.get_value() is False


async def test_ldap_injection_scorer_rationale_includes_pattern_name(patch_central_database):
    scorer = LDAPInjectionOutputScorer()
    score = (await scorer.score_text_async("filter *)(uid=*) bypass"))[0]
    assert score.get_value() is True
    assert "Filter Break Sequence" in score.score_rationale

    score_negation = (await scorer.score_text_async("admin)(!(password=*))"))[0]
    assert score_negation.get_value() is True
    assert "Boolean Operator Injection" in score_negation.score_rationale

    score_null = (await scorer.score_text_async("admin*)%00"))[0]
    assert score_null.get_value() is True
    assert "Null Byte Filter Termination" in score_null.score_rationale


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

        memory.add_scores_to_memory_async.assert_called_once()
