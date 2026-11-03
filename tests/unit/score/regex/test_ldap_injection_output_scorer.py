# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import ScorerEvaluationIdentifier, config_hash
from pyrit.score import LDAPInjectionOutputScorer, TrueFalseScoreAggregator
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc


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
        # URL-encoded wildcards (%2a / %2A)
        "%2a)(uid=*)",
        "%2A)(uid=*)",
        ")(uid=%2a)",
        ")(uid=%2A)",
        "%2a)(uid=%2a)",
        "%2A)(uid=%2A)",
        # Percent-encoded filter breaks and clauses
        "%2a%29%28uid=%2a%29",
        "%29%28cn=%2a%29",
        "%29%28objectClass=%2a%29",
        "%29%7c%28objectClass=%2a%29",
        "%29%26%28uid=%2a%29",
        "%29%28%21%28password=%2a%29%29",
        "admin%29%7c%28%21%28uid=%2a%29%29",
        # Null-byte filter terminations with encoded parens/wildcards
        "admin*%29%00",
        "admin%2a%29%00",
        r"admin\2a)%00",
        # LDAP URLs with search filter queries (RFC 4516) containing injection sequences
        "ldap://localhost:389/???%2a%29%28uid=%2a%29",
        "ldaps://ldap.corp.internal/???%29%7c%28objectClass=%2a%29",
        "ldap://127.0.0.1/dc=example,dc=com???admin%29%28uid=%2a%29",
        "ldap://127.0.0.1/dc=example,dc=com???admin)(uid=*)",
        "ldap://directory.example.com/ou=users,dc=org???*)(uid=*)",
        "ldap://10.0.0.1:1389/???admin%29%28%21%28userPassword=%2a%29%29",
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
        # Benign URLs and text
        "ldap://example.com/cn=John%20Doe,dc=example,dc=com",
        "http://example.com/search?q=(test)",
        "price is 20% (discount applied)",
        # Benign LDAP URLs with plain and percent-encoded filters or extension-only text
        "ldap://example.com/???(uid=alice)",
        "ldap://example.com/???%28uid%3Dalice%29",
        "ldap://example.com/????x-note=%28uid=alice%29",
        "ldap://example.com/????x-note=%28uid=%2a%29",
        "ldap://example.com/???(objectClass=person)",
        "ldap://example.com/???%28objectClass%3Dperson%29",
        "ldap://example.com/???(&(uid=alice)(status=active))",
        "ldap://example.com/???%28%26%28uid%3Dalice%29%28status%3Dactive%29%29",
        "ldap://example.com/???(|(uid=alice)(uid=bob))",
        # Literal-star equality assertions in benign compound filters (RFC 4515 \2a is literal assertion data)
        r"(&(uid=alice)(cn=\2a))",
        r"(&(uid=alice)(cn=\2A))",
        r"(&(cn=\2a)(uid=alice))",
        r"(&(cn=\2A)(uid=alice))",
        r"(&(objectClass=\2a)(sn=Smith))",
        r"(&(sn=Smith)(objectClass=\2a))",
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

    score_always_true = (await scorer.score_text_async("user%29%28cn=%2a%29bypass"))[0]
    assert score_always_true.get_value() is True
    assert "Always-True Clause" in score_always_true.score_rationale

    score_url = (await scorer.score_text_async("ldap://127.0.0.1/dc=example,dc=com???admin%29%28uid=%2a%29"))[0]
    assert score_url.get_value() is True
    assert "LDAP URL Filter Injection" in score_url.score_rationale


async def test_ldap_injection_scorer_malformed_url_does_not_stall(patch_central_database):
    scorer = LDAPInjectionOutputScorer()
    malformed_text = "ldap://example.com/???" + "(" * 640 + "a" * 640
    score = (await scorer.score_text_async(malformed_text))[0]
    assert score.get_value() is False


async def test_ldap_injection_scorer_custom_patterns(patch_central_database):
    custom = {"Marker": r"DANGER_LDAP_\d+"}
    scorer = LDAPInjectionOutputScorer(patterns=custom)

    score = (await scorer.score_text_async("payload DANGER_LDAP_42"))[0]
    assert score.get_value() is True

    score = (await scorer.score_text_async("*)(uid=*)"))[0]
    assert score.get_value() is False


@pytest.mark.parametrize(
    "text",
    [
        "ldap://example.com/???%2a%29%28%75id%3d%2a%29",
        "ldap://example.com/???%2a%29%28u%69d%3d%2a%29",
        "ldap://example.com/???%2a%29%28ui%64%3d%2a%29",
        "ldap://example.com/???%2a%29%28%75%69%64%3d%2a%29",
        "LDAPS://example.com/dc=example?cn,sn?sub?%2A%29%28%6Dail%3D%2A%29",
        "ldap:///???%2a%29%28%75%69%64%3d%2a%29",
        "ldap://example.com/???*)(%75id=*)",
        "ldap://example.com/???%29%28%75id%3d%2a%29",
        "ldap://example.com/???%29%7c%28%75id%3dalice%29",
        "ldap://example.com/???%29%26%28%75id%3dalice%29",
        "ldap://example.com/???%29%28%21%28%75id%3dalice%29%29",
        "ldap://example.com/???%29%5cx00",
        "ldap://example.com/???(uid=alice) ldap://example.com/???%29%28%75id%3d%2a%29",
    ],
)
@pytest.mark.usefixtures("patch_central_database")
async def test_ldap_url_encoded_filter_detected_async(*, text: str, sqlite_instance: SQLiteMemory) -> None:
    scorer = LDAPInjectionOutputScorer()
    score = (await scorer.score_text_async(text))[0]

    assert score.get_value() is True
    assert score.score_category == ["security"]
    assert score.score_rationale == "Matched: LDAP URL Filter Injection"
    assert score.score_metadata == {}
    assert score.scorer_class_identifier == scorer.get_identifier()

    stored_scores = await sqlite_instance.get_scores_async(score_ids=[str(score.id)])
    assert len(stored_scores) == 1
    assert stored_scores[0].get_value() is True
    assert stored_scores[0].score_category == score.score_category
    assert stored_scores[0].score_rationale == score.score_rationale
    assert stored_scores[0].score_metadata == score.score_metadata
    assert stored_scores[0].scorer_class_identifier == score.scorer_class_identifier


@pytest.mark.parametrize(
    "text",
    [
        "ldap://example.com/???%28%75id%3dalice%29",
        "ldap://example.com/???%28%75id%3d%2a%29",
        "ldap://example.com/???%28%26%28%75id%3dalice%29%28cn%3d%5c2a%29%29",
        "ldap://example.com/???%28%26%28cn%3d%5c2A%29%28%75id%3dalice%29%29",
        "ldap://example.com/???%252a%2529%2528%75id%253d%252a%2529",
        "ldap://example.com/???%29%2500",
        "ldap://example.com/????x-note=%2a%29%28%75id%3d%2a%29",
        "ldap://example.com/???(uid=alice)?x-note=%2a%29%28%75id%3d%2a%29",
        "ldap://example.com/???(uid=alice)#%2a%29%28%75id%3d%2a%29",
        "ldap://example.com/??%2a%29%28%75id%3d%2a%29",
        "ldap://example.com/%2a%29%28%75id%3d%2a%29",
        "https://example.com/???%2a%29%28%75id%3d%2a%29",
        "%2a%29%28%75id%3d%2a%29",
        "ldap://example.com/???)%255c01",
    ],
)
@pytest.mark.usefixtures("patch_central_database")
async def test_ldap_url_decoding_is_scoped_and_single_pass_async(*, text: str, sqlite_instance: SQLiteMemory) -> None:
    score = (await LDAPInjectionOutputScorer().score_text_async(text))[0]

    assert score.get_value() is False
    assert score.score_rationale == ""
    stored_scores = await sqlite_instance.get_scores_async(score_ids=[str(score.id)])
    assert len(stored_scores) == 1
    assert stored_scores[0].get_value() is False
    assert stored_scores[0].score_rationale == ""


@pytest.mark.parametrize("copy_defaults", [False, True])
@pytest.mark.usefixtures("patch_central_database")
async def test_ldap_url_custom_patterns_do_not_decode_async(*, copy_defaults: bool) -> None:
    default_patterns = LDAPInjectionOutputScorer._DEFAULT_PATTERNS
    assert default_patterns is not None
    patterns = dict(default_patterns) if copy_defaults else {"Marker": "LOCAL_MARKER"}
    scorer = LDAPInjectionOutputScorer(patterns=patterns)
    text = "ldap://example.com/???%2a%29%28%75id%3d%2a%29"

    assert (await scorer.score_text_async(text))[0].get_value() is False
    if not copy_defaults:
        assert (await scorer.score_text_async("LOCAL_MARKER"))[0].get_value() is True


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("aggregator", [TrueFalseScoreAggregator.OR, TrueFalseScoreAggregator.AND])
async def test_ldap_url_decoding_policy_has_distinct_persisted_identity_async(
    *, aggregator: TrueFalseAggregatorFunc, sqlite_instance: SQLiteMemory
) -> None:
    default_patterns = LDAPInjectionOutputScorer._DEFAULT_PATTERNS
    assert default_patterns is not None
    default = LDAPInjectionOutputScorer(score_aggregator=aggregator)
    copied = LDAPInjectionOutputScorer(patterns=dict(default_patterns), score_aggregator=aggregator)
    text = "ldap://example.com/???%2a%29%28%75id%3d%2a%29"

    default_score = (await default.score_text_async(text))[0]
    copied_score = (await copied.score_text_async(text))[0]
    assert default_score.get_value() is True
    assert copied_score.get_value() is False
    default_identifier = default.get_identifier()
    copied_identifier = copied.get_identifier()
    assert default_identifier.hash != copied_identifier.hash
    assert default_identifier.eval_hash != copied_identifier.eval_hash
    assert default_identifier.params["decode_url_filters"] is True
    assert copied_identifier.params["decode_url_filters"] is False

    for scorer, repeated, score in (
        (default, LDAPInjectionOutputScorer(score_aggregator=aggregator), default_score),
        (
            copied,
            LDAPInjectionOutputScorer(patterns=dict(default_patterns), score_aggregator=aggregator),
            copied_score,
        ),
    ):
        identifier = scorer.get_identifier()
        assert identifier.hash == repeated.get_identifier().hash
        assert identifier.eval_hash == repeated.get_identifier().eval_hash
        assert identifier.eval_hash == ScorerEvaluationIdentifier(identifier).eval_hash
        assert identifier.params["score_aggregator"] == aggregator.__name__
        assert identifier.params["patterns_digest"] == config_hash(default_patterns)
        stored = await sqlite_instance.get_scores_async(score_ids=[str(score.id)])
        assert len(stored) == 1
        assert stored[0].get_value() == score.get_value()
        assert stored[0].scorer_class_identifier == identifier
        assert stored[0].scorer_class_identifier.hash == identifier.hash
        assert stored[0].scorer_class_identifier.eval_hash == identifier.eval_hash


@pytest.mark.usefixtures("patch_central_database")
async def test_ldap_url_decoded_near_miss_async() -> None:
    text = "ldap://example.com/???" + "%28" * 640 + "%61" * 640
    score = (await LDAPInjectionOutputScorer().score_text_async(text))[0]

    assert score.get_value() is False


async def test_ldap_injection_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = LDAPInjectionOutputScorer()
        await scorer.score_text_async(text="nothing here")

        memory.add_scores_to_memory_async.assert_called_once()
