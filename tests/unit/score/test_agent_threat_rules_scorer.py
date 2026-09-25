# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory
from pyrit.models import (
    ChatMessageRole,
    ComponentIdentifier,
    ContentScorable,
    Message,
    MessagePiece,
    MessageScorable,
    PromptDataType,
    Score,
    ScoreStatus,
)
from pyrit.prompt_target.common.chat_completions_response_parser import _build_tool_pieces
from pyrit.score import ScorerPromptValidator, TrueFalseScoreAggregator
from pyrit.score.true_false.regex.agent_threat_rules_scorer import (
    SUPPORTED_DIGEST_SCHEMA,
    AgentThreatRulesScorer,
    _patterns_from_digest,
)
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseAggregatorFunc

_MODULE = "pyrit.score.true_false.regex.agent_threat_rules_scorer"


def _digest(**overrides: Any) -> dict[str, Any]:
    """A minimal digest in the shape ATR's exporter publishes."""
    base = {
        "schema": SUPPORTED_DIGEST_SCHEMA,
        "atr_version": "4.0.0",
        "atr_commit": "54d3e13e94f8980d7b36f9d79511b26174954dfc",
        "default_fields": ["agent_output", "content"],
        "rules_seen": 3,
        "rules_emitted": 3,
        "conditions_by_field": {"content": 2, "tool_response": 1},
        "conditions": {
            "ATR-2026-00030#0": {
                "rule_id": "ATR-2026-00030",
                "pattern": r"(?i)ignore\s+(?:all\s+)?previous\s+instructions",
                "field": "content",
                "category": "prompt-injection",
            },
            "ATR-2026-00031#0": {
                "rule_id": "ATR-2026-00031",
                "pattern": r"(?i)speaking\s+as\s+the\s+admin\s+agent",
                "field": "agent_output",
                "category": "agent-manipulation",
            },
            "ATR-2026-00032#0": {
                "rule_id": "ATR-2026-00032",
                "pattern": r"(?i)exfiltrate\s+the\s+system\s+prompt",
                "field": "tool_response",
                "category": "context-exfiltration",
            },
        },
        "excluded": {},
    }
    base.update(overrides)
    return base


@pytest.fixture
def offline_digest() -> Iterator[MagicMock]:
    """Serve the fixture digest without touching the network or the cache."""
    payload = json.dumps(_digest()).encode("utf-8")
    response = MagicMock()
    response.read.return_value = payload
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    with patch(f"{_MODULE}.urllib.request.urlopen", new_callable=MagicMock, return_value=response) as urlopen:
        yield urlopen


class TestFieldSelection:
    """Field selection limits rules without changing the evidence's surface."""

    def test_defaults_to_the_digests_default_fields(self) -> None:
        patterns = _patterns_from_digest(_digest())
        assert set(patterns) == {"ATR-2026-00030#0", "ATR-2026-00031#0"}

    def test_explicit_fields_override_the_default(self) -> None:
        patterns = _patterns_from_digest(_digest(), fields=["tool_response"])
        assert set(patterns) == {"ATR-2026-00032#0"}

    def test_condition_keys_keep_a_match_traceable_to_its_rule(self) -> None:
        patterns = _patterns_from_digest(_digest(), fields=["content"])
        assert all(name.startswith("ATR-") and "#" in name for name in patterns)

    def test_a_digest_without_default_fields_and_no_request_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="no fields requested|default_fields"):
            _patterns_from_digest(_digest(default_fields=[]))


class TestDigestValidation:
    """An ATR-side regression must fail loudly here, not score silently less."""

    def test_unsupported_schema_is_rejected(self, offline_digest: MagicMock) -> None:
        payload = json.dumps(_digest(schema=SUPPORTED_DIGEST_SCHEMA + 1)).encode("utf-8")
        offline_digest.return_value.read.return_value = payload
        with pytest.raises(ValueError, match="schema"):
            AgentThreatRulesScorer(cache=False)

    def test_a_pattern_that_does_not_compile_is_rejected_at_construction(self, offline_digest: MagicMock) -> None:
        broken = _digest()
        broken["conditions"]["ATR-2026-00030#0"]["pattern"] = r"(?i)unclosed[group"
        offline_digest.return_value.read.return_value = json.dumps(broken).encode("utf-8")
        with pytest.raises(ValueError, match="does not compile"):
            AgentThreatRulesScorer(cache=False)

    def test_an_empty_digest_is_rejected(self, offline_digest: MagicMock) -> None:
        offline_digest.return_value.read.return_value = json.dumps(_digest(conditions={})).encode("utf-8")
        with pytest.raises(ValueError, match="no conditions"):
            AgentThreatRulesScorer(cache=False)

    def test_requesting_a_field_the_digest_has_none_of_names_what_is_available(self, offline_digest: MagicMock) -> None:
        with pytest.raises(ValueError, match="tool_name"):
            AgentThreatRulesScorer(fields=["tool_name"], cache=False)


class TestConstruction:
    def test_loads_only_default_field_patterns(self, offline_digest: MagicMock) -> None:
        scorer = AgentThreatRulesScorer(cache=False)
        assert len(scorer._patterns) == 2

    def test_pins_to_a_commit_by_default(self, offline_digest: MagicMock) -> None:
        AgentThreatRulesScorer(cache=False)
        url = offline_digest.call_args[0][0]
        assert "54d3e13e94f8980d7b36f9d79511b26174954dfc" in url
        assert url.startswith("https://raw.githubusercontent.com/Agent-Threat-Rule/agent-threat-rules/")

    def test_an_explicit_ref_is_honoured(self, offline_digest: MagicMock) -> None:
        AgentThreatRulesScorer(ref="main", cache=False)
        assert "/main/data/pyrit-digest.json" in offline_digest.call_args[0][0]

    def test_categories_default_to_agent_threat(self, offline_digest: MagicMock) -> None:
        scorer = AgentThreatRulesScorer(cache=False)
        assert scorer._score_categories == ["agent_threat"]

    def test_categories_can_be_overridden(self, offline_digest: MagicMock) -> None:
        scorer = AgentThreatRulesScorer(categories=["custom"], cache=False)
        assert scorer._score_categories == ["custom"]

    def test_a_fetch_failure_names_the_url(self) -> None:
        with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=OSError("no route to host")):
            with pytest.raises(ValueError, match="Could not fetch the ATR digest"):
                AgentThreatRulesScorer(cache=False)


class TestCaching:
    def test_a_cached_digest_is_used_without_refetching(self, offline_digest: MagicMock, tmp_path: Path) -> None:
        cache_file = tmp_path / "pyrit-digest-cached.json"
        cache_file.write_text(json.dumps(_digest()), encoding="utf-8")
        with patch(f"{_MODULE}._cache_path", return_value=cache_file):
            AgentThreatRulesScorer(cache=True)
        offline_digest.assert_not_called()

    def test_a_corrupt_cache_entry_falls_back_to_fetching(self, offline_digest: MagicMock, tmp_path: Path) -> None:
        cache_file = tmp_path / "pyrit-digest-corrupt.json"
        cache_file.write_text("{ not json", encoding="utf-8")
        with patch(f"{_MODULE}._cache_path", return_value=cache_file):
            scorer = AgentThreatRulesScorer(cache=True)
        offline_digest.assert_called_once()
        assert len(scorer._patterns) == 2

    def test_the_fetched_digest_is_written_to_cache(self, offline_digest: MagicMock, tmp_path: Path) -> None:
        cache_file = tmp_path / "nested" / "pyrit-digest.json"
        with patch(f"{_MODULE}._cache_path", return_value=cache_file):
            AgentThreatRulesScorer(cache=True)
        assert json.loads(cache_file.read_text(encoding="utf-8"))["atr_version"] == "4.0.0"


class TestCachePath:
    def test_a_branch_name_cannot_escape_the_cache_directory(self) -> None:
        from pyrit.score.true_false.regex.agent_threat_rules_scorer import _cache_path

        path = _cache_path("../../etc/passwd")
        assert ".." not in path.parts
        assert path.name.startswith("pyrit-digest-")


@pytest.fixture
def routed_digest(offline_digest: MagicMock) -> MagicMock:
    fields = ("content", "agent_output", "user_input", "tool_name", "tool_args", "tool_response")
    conditions = {
        f"ATR-{field}#0": {"rule_id": f"ATR-{field}", "pattern": "MARKER", "field": field} for field in fields
    }
    offline_digest.return_value.read.return_value = json.dumps(_digest(conditions=conditions)).encode("utf-8")
    return offline_digest


def _piece(*, value: str, role: ChatMessageRole = "assistant", data_type: PromptDataType = "text") -> MessagePiece:
    return MessagePiece(role=role, original_value=value, original_value_data_type=data_type)


def _message_scorable(*pieces: MessagePiece) -> MessageScorable:
    conversation_id = str(uuid.uuid4())
    for piece in pieces:
        piece.conversation_id = conversation_id
    message = Message(message_pieces=list(pieces))
    CentralMemory.get_memory_instance().add_message_to_memory(request=message)
    return MessageScorable.from_message(message)


@pytest.mark.usefixtures("patch_central_database", "routed_digest")
class TestScoring:
    @pytest.mark.parametrize("text", ["MARKER", "ordinary text"])
    async def test_default_scoring_async(self, text: str) -> None:
        scorer = AgentThreatRulesScorer(cache=False)
        scores = await scorer.score_text_async(text=text)
        assert len(scores) == 1
        assert scores[0].get_value() is ("MARKER" in text)
        assert scores[0].score_category == ["agent_threat"]
        assert scores[0].scorable is not None

    async def test_loose_content_uses_configured_aggregator_async(self) -> None:
        aggregator = MagicMock(wraps=TrueFalseScoreAggregator.OR)
        aggregator.__name__ = "custom_aggregator"
        scorer = AgentThreatRulesScorer(cache=False, score_aggregator=aggregator)
        await scorer.score_text_async(text="MARKER")
        aggregator.assert_called_once()

    @pytest.mark.parametrize(
        ("role", "data_type", "value", "expected_fields"),
        [
            ("assistant", "text", "MARKER", {"content", "agent_output"}),
            ("user", "text", "MARKER", {"content", "user_input"}),
            ("tool", "text", "MARKER", {"content", "tool_response"}),
            ("system", "text", "MARKER", {"content"}),
            ("developer", "text", "MARKER", {"content"}),
            ("assistant", "function_call", '{"type":"function_call","name":"MARKER","arguments":"{}"}', {"tool_name"}),
            ("assistant", "function_call", '{"name":"safe","arguments":"{\\"query\\":\\"MARKER\\"}"}', {"tool_args"}),
            ("assistant", "function_call", '{"name":"safe","arguments":"not json MARKER"}', {"tool_args"}),
            (
                "assistant",
                "function_call",
                '{"type":"function","function":{"name":"MARKER","arguments":"{\\"query\\":\\"MARKER\\"}"}}',
                {"tool_name", "tool_args"},
            ),
            ("tool", "function_call_output", '{"call_id":"MARKER","output":"safe"}', set()),
            ("tool", "function_call_output", '{"output":{"result":"MARKER"}}', {"content", "tool_response"}),
        ],
    )
    async def test_routes_only_matching_fields_async(
        self, *, role: ChatMessageRole, data_type: PromptDataType, value: str, expected_fields: set[str]
    ) -> None:
        scorer = AgentThreatRulesScorer(fields=sorted(AgentThreatRulesScorer._SUPPORTED_FIELDS), cache=False)
        scorable = _message_scorable(_piece(value=value, role=role, data_type=data_type))
        score = (await scorer.score_async(scorable=scorable))[0]
        assert score.get_value() is bool(expected_fields)
        for field in AgentThreatRulesScorer._SUPPORTED_FIELDS:
            assert (f"ATR-{field}#0" in score.score_rationale) is (field in expected_fields)
        assert score.scorable == scorable

    @pytest.mark.parametrize(
        ("data_type", "value"),
        [
            ("function_call", "{invalid"),
            ("function_call", "[]"),
            ("function_call", '{"name":"safe"}'),
            ("function_call", '{"arguments":{}}'),
            ("function_call", '{"type":"function","function":null}'),
        ],
    )
    async def test_unreadable_call_is_undetermined_async(self, *, data_type: PromptDataType, value: str) -> None:
        scorer = AgentThreatRulesScorer(fields=["tool_name", "tool_args"], cache=False)
        score = (await scorer.score_async(scorable=_message_scorable(_piece(value=value, data_type=data_type))))[0]
        assert score.status is ScoreStatus.UNDETERMINED
        assert score.score_rationale

    @pytest.mark.parametrize("value", ["{invalid", "{}", '{"output":""}'])
    async def test_tool_output_missing_differs_from_empty_async(self, value: str) -> None:
        scorer = AgentThreatRulesScorer(fields=["tool_response"], cache=False)
        scorable = _message_scorable(_piece(value=value, role="tool", data_type="function_call_output"))
        score = (await scorer.score_async(scorable=scorable))[0]
        if value == '{"output":""}':
            assert score.get_value() is False
        else:
            assert score.status is ScoreStatus.UNDETERMINED

    async def test_known_match_settles_missing_arguments_async(self) -> None:
        scorer = AgentThreatRulesScorer(fields=["tool_name", "tool_args"], cache=False)
        scorable = _message_scorable(_piece(value='{"name":"MARKER"}', data_type="function_call"))
        assert (await scorer.score_async(scorable=scorable))[0].get_value() is True

    async def test_missing_unselected_field_does_not_prevent_negative_async(self) -> None:
        scorer = AgentThreatRulesScorer(fields=["tool_name"], cache=False)
        scorable = _message_scorable(_piece(value='{"name":"safe"}', data_type="function_call"))
        assert (await scorer.score_async(scorable=scorable))[0].get_value() is False

    @pytest.mark.parametrize("text", ["safe", "MARKER"])
    async def test_mixed_readable_and_malformed_pieces_async(self, text: str) -> None:
        scorer = AgentThreatRulesScorer(fields=["agent_output", "tool_args"], cache=False)
        scorable = _message_scorable(
            _piece(value=text),
            _piece(value="{invalid", data_type="function_call"),
        )
        score = (await scorer.score_async(scorable=scorable))[0]
        if text == "MARKER":
            assert score.get_value() is True
        else:
            assert score.status is ScoreStatus.UNDETERMINED

    async def test_converted_arguments_are_the_scored_evidence_async(self) -> None:
        scorer = AgentThreatRulesScorer(fields=["tool_args"], cache=False)
        piece = _piece(value='{"name":"safe","arguments":{}}', data_type="function_call")
        piece.converted_value = '{"name":"safe","arguments":{"query":"MARKER"}}'
        assert (await scorer.score_async(scorable=_message_scorable(piece)))[0].get_value() is True

    @pytest.mark.parametrize("chat_format", [False, True])
    @pytest.mark.parametrize(
        "arguments",
        [
            {"url": "http://169.254.169.254/latest/meta-data/"},
            '{"url": "http://169.254.169.254/latest/meta-data/"}',
            r'{"url":"http:\/\/169.254.169.254\/latest\/meta-data\/"}',
            r'{"url":"http://\u0031\u0036\u0039.254.169.254/latest/meta-data/"}',
            {"request": {"urls": ["http://169.254.169.254/latest/meta-data/"]}},
            r'{"request":{"urls":["http:\/\/169.254.169.254\/latest\/meta-data\/"]}}',
        ],
    )
    async def test_equivalent_argument_encodings_match_async(
        self, *, routed_digest: MagicMock, arguments: dict[str, Any] | str, chat_format: bool
    ) -> None:
        digest = _digest(
            conditions={
                "ATR-2026-01605#0": {
                    "field": "tool_args",
                    "pattern": r"(?i)169\.254\.169\.254(?::\d+)?/",
                }
            }
        )
        routed_digest.return_value.read.return_value = json.dumps(digest).encode()
        scorer = AgentThreatRulesScorer(fields=["tool_args"], cache=False)
        call = {"name": "fetch", "arguments": arguments}
        payload = {"type": "function", "function": call} if chat_format else call
        scorable = _message_scorable(_piece(value=json.dumps(payload), data_type="function_call"))
        score = (await scorer.score_async(scorable=scorable))[0]
        assert score.get_value() is True
        assert score.score_rationale == "Matched: ATR-2026-01605#0"

    @pytest.mark.parametrize(
        ("arguments", "pattern", "expected"),
        [
            ('{"query": "MARKER"}', r'"query": "MARKER"', True),
            ('{"query":"MARKER"}', "MARKER", True),
            (r'{"query":"\u004dARKER"}', "MARKER", True),
            (r'{"query":"\\u004dARKER"}', "MARKER", False),
            ('{ "query": "MARKER", "a": 1 }', r'^\{"a":1,"query":"MARKER"\}$', True),
            ("{invalid MARKER", "MARKER", True),
            ("not json", "MARKER", False),
            ("", "MARKER", False),
        ],
    )
    async def test_argument_raw_matches_are_preserved_async(
        self, *, routed_digest: MagicMock, arguments: str, pattern: str, expected: bool
    ) -> None:
        digest = _digest(conditions={"args#0": {"field": "tool_args", "pattern": pattern}})
        routed_digest.return_value.read.return_value = json.dumps(digest).encode()
        scorer = AgentThreatRulesScorer(fields=["tool_args"], cache=False)
        payload = json.dumps({"name": "safe", "arguments": arguments})
        score = (
            await scorer.score_async(scorable=_message_scorable(_piece(value=payload, data_type="function_call")))
        )[0]
        assert score.get_value() is expected
        assert score.score_rationale == ("Matched: args#0" if expected else "")

    async def test_decoded_arguments_do_not_match_other_fields_async(self) -> None:
        scorer = AgentThreatRulesScorer(fields=["tool_name", "content"], cache=False)
        payload = json.dumps({"name": "safe", "arguments": r'{"query":"\u004dARKER"}'})
        score = (
            await scorer.score_async(scorable=_message_scorable(_piece(value=payload, data_type="function_call")))
        )[0]
        assert score.get_value() is False

    @pytest.mark.parametrize("case", ["positive", "negative", "malformed", "mixed", "blocked", "error", "loose"])
    async def test_provenance_is_saved_async(self, case: str) -> None:
        scorer = AgentThreatRulesScorer(fields=["content", "tool_args"], ref="main", cache=False)
        if case == "loose":
            scorable = ContentScorable(value="MARKER")
        else:
            pieces = [_piece(value="MARKER" if case == "positive" else "safe")]
            if case in ("malformed", "mixed"):
                pieces = [_piece(value="{invalid", data_type="function_call")]
                if case == "mixed":
                    pieces.append(_piece(value="MARKER"))
            elif case in ("blocked", "error"):
                pieces = [
                    MessagePiece(
                        role="assistant",
                        original_value="",
                        original_value_data_type="error",
                        response_error="blocked" if case == "blocked" else "processing",
                    )
                ]
            scorable = _message_scorable(*pieces)
        score = (await scorer.score_async(scorable=scorable))[0]
        expected = {
            "atr_ref": "main",
            "atr_digest_url": (
                "https://raw.githubusercontent.com/Agent-Threat-Rule/agent-threat-rules/main/data/pyrit-digest.json"
            ),
            "atr_digest_sha256": scorer.get_identifier().params["digest_sha256"],
            "atr_commit": _digest()["atr_commit"],
            "atr_version": _digest()["atr_version"],
        }
        assert score.score_metadata == expected
        stored = CentralMemory.get_memory_instance().get_scores(score_ids=[str(score.id)])[0]
        assert stored.score_metadata == expected
        assert Score.model_validate_json(stored.model_dump_json()).score_metadata == expected

    async def test_field_selection_does_not_relabel_text_async(self) -> None:
        scorer = AgentThreatRulesScorer(fields=["tool_response"], cache=False)
        assert await scorer.score_async(scorable=_message_scorable(_piece(value="MARKER"))) == []
        assert await scorer.score_text_async(text="MARKER") == []

    @pytest.mark.parametrize(
        ("role", "data_type", "value"),
        [
            ("simulated_assistant", "text", "MARKER"),
            ("assistant", "reasoning", "MARKER"),
            ("user", "function_call", '{"name":"MARKER","arguments":{}}'),
            ("assistant", "tool_call", '{"type":"web_search_call","query":"MARKER"}'),
        ],
    )
    async def test_unsupported_pieces_do_not_score_async(
        self, *, role: ChatMessageRole, data_type: PromptDataType, value: str
    ) -> None:
        scorer = AgentThreatRulesScorer(fields=sorted(AgentThreatRulesScorer._SUPPORTED_FIELDS), cache=False)
        scorable = _message_scorable(_piece(value=value, role=role, data_type=data_type))
        assert await scorer.score_async(scorable=scorable) == []

    @pytest.mark.parametrize("aggregator", [TrueFalseScoreAggregator.OR, TrueFalseScoreAggregator.AND])
    async def test_mixed_piece_aggregation_async(self, aggregator: TrueFalseAggregatorFunc) -> None:
        scorer = AgentThreatRulesScorer(fields=["agent_output", "tool_args"], cache=False, score_aggregator=aggregator)
        first = _piece(value="safe")
        second = _piece(value='{"name":"safe","arguments":{"query":"MARKER"}}', data_type="function_call")
        scorable = _message_scorable(first, second)
        score = (await scorer.score_async(scorable=scorable))[0]
        assert score.get_value() is (aggregator is TrueFalseScoreAggregator.OR)
        assert "ATR-tool_args#0" in score.score_rationale

    async def test_loose_content_scores_as_user_text_async(self) -> None:
        scorer = AgentThreatRulesScorer(fields=["user_input", "agent_output", "content"], cache=False)
        score = (await scorer.score_async(scorable=ContentScorable(value="MARKER")))[0]
        assert "ATR-content#0" in score.score_rationale
        assert "ATR-user_input#0" in score.score_rationale
        assert "ATR-agent_output#0" not in score.score_rationale
        assert score.scorable is not None

    async def test_chat_completions_tool_call_is_parsed_async(self) -> None:
        tool_call = MagicMock()
        tool_call.id = "call_1"
        tool_call.function.name = "MARKER"
        tool_call.function.arguments = '{"query":"MARKER"}'
        request = _piece(value="hi", role="user")
        pieces = _build_tool_pieces(message=MagicMock(tool_calls=[tool_call]), request=request)
        scorer = AgentThreatRulesScorer(fields=["tool_name", "tool_args"], cache=False)
        score = (await scorer.score_async(scorable=_message_scorable(*pieces)))[0]
        assert "ATR-tool_name#0" in score.score_rationale
        assert "ATR-tool_args#0" in score.score_rationale

    async def test_custom_validator_restricts_evidence_async(self) -> None:
        scorer = AgentThreatRulesScorer(
            cache=False, validator=ScorerPromptValidator(supported_data_types=["text"], supported_roles=["assistant"])
        )
        scorable = _message_scorable(_piece(value="MARKER", role="user"))
        assert await scorer.score_async(scorable=scorable) == []


@pytest.mark.usefixtures("routed_digest")
class TestIdentifiers:
    def test_identifier_records_configuration(self) -> None:
        scorer = AgentThreatRulesScorer(fields=["tool_args", "tool_name"], cache=False)
        identifier = scorer.get_identifier()
        assert list(identifier.params["fields"]) == ["tool_args", "tool_name"]
        assert len(identifier.params["digest_sha256"]) == 64
        assert ComponentIdentifier.model_validate_json(identifier.model_dump_json()) == identifier

    def test_equal_count_rule_changes_change_identity(self, routed_digest: MagicMock) -> None:
        first = AgentThreatRulesScorer(cache=False).get_identifier()
        payload = json.loads(routed_digest.return_value.read.return_value)
        payload["conditions"]["ATR-content#0"]["pattern"] = "different"
        routed_digest.return_value.read.return_value = json.dumps(payload).encode()
        second = AgentThreatRulesScorer(cache=False).get_identifier()
        assert first.eval_hash != second.eval_hash

    def test_fields_categories_and_aggregator_change_identity(self) -> None:
        scorers = [
            AgentThreatRulesScorer(fields=["tool_args"], cache=False),
            AgentThreatRulesScorer(fields=["tool_name"], cache=False),
            AgentThreatRulesScorer(fields=["tool_args"], categories=["custom"], cache=False),
            AgentThreatRulesScorer(fields=["tool_args"], score_aggregator=TrueFalseScoreAggregator.AND, cache=False),
        ]
        assert len({scorer.get_identifier().eval_hash for scorer in scorers}) == len(scorers)

    def test_field_order_does_not_change_identity(self) -> None:
        first = AgentThreatRulesScorer(fields=["tool_args", "tool_name"], cache=False)
        second = AgentThreatRulesScorer(fields=["tool_name", "tool_args"], cache=False)
        assert first.get_identifier().eval_hash == second.get_identifier().eval_hash

    def test_source_ref_does_not_change_identity_for_the_same_digest(self) -> None:
        first = AgentThreatRulesScorer(ref="main", cache=False)
        second = AgentThreatRulesScorer(cache=False)
        assert first.get_identifier().eval_hash == second.get_identifier().eval_hash

    def test_revision_changes_identity(self, routed_digest: MagicMock) -> None:
        first = AgentThreatRulesScorer(cache=False).get_identifier()
        payload = json.loads(routed_digest.return_value.read.return_value)
        payload["atr_commit"] = "another-commit"
        routed_digest.return_value.read.return_value = json.dumps(payload).encode()
        second = AgentThreatRulesScorer(cache=False).get_identifier()
        assert first.eval_hash != second.eval_hash


@pytest.mark.usefixtures("offline_digest")
@pytest.mark.parametrize(
    "fields", [["tool_description"], ["trace.require_violation"], ["content", "tool_name"], "content"]
)
def test_invalid_field_selection_is_rejected(fields: Any) -> None:
    with pytest.raises(ValueError, match="fields|field names"):
        AgentThreatRulesScorer(fields=fields, cache=False)


def test_cache_disabled_fetches_mutable_ref_each_time(offline_digest: MagicMock, tmp_path: Path) -> None:
    cache_file = tmp_path / "cached.json"
    cache_file.write_text(json.dumps(_digest()), encoding="utf-8")
    with patch(f"{_MODULE}._cache_path", return_value=cache_file):
        AgentThreatRulesScorer(ref="main", cache=False)
        AgentThreatRulesScorer(ref="main", cache=False)
    assert offline_digest.call_count == 2
    assert json.loads(cache_file.read_text(encoding="utf-8")) == _digest()


@pytest.mark.parametrize(
    "condition",
    [{"pattern": 12, "field": "content"}, {"pattern": "MARKER"}, {"pattern": "MARKER", "field": None}],
)
def test_invalid_condition_shape_is_rejected(offline_digest: MagicMock, condition: dict[str, Any]) -> None:
    offline_digest.return_value.read.return_value = json.dumps(_digest(conditions={"bad": condition})).encode()
    with pytest.raises(ValueError, match="pattern|field"):
        AgentThreatRulesScorer(cache=False)


@pytest.mark.parametrize("default_fields", [None, "content", [None]])
def test_invalid_default_fields_are_rejected(offline_digest: MagicMock, default_fields: Any) -> None:
    offline_digest.return_value.read.return_value = json.dumps(_digest(default_fields=default_fields)).encode()
    with pytest.raises(ValueError, match="field names"):
        AgentThreatRulesScorer(cache=False)
