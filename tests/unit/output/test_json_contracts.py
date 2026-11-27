# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from typing import NotRequired, assert_type, get_type_hints
from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import make_scenario_result

from pyrit.models import AttackResult, ComponentIdentifier, Message, MessagePiece, Score
from pyrit.output._json_types import (
    AttackInfo,
    ConversationInfo,
    MessageInfo,
    ReasoningPieceInfo,
    RegularPieceInfo,
    ScenarioAttacks,
    ScenarioFull,
    ScenarioOverview,
    ScoreInfo,
    ScorerInfo,
    ScorerNode,
)
from pyrit.output.conversation.json import JsonConversationPrinter
from pyrit.output.conversation.source import MemoryConversationSource
from pyrit.output.scenario_result.json import JsonScenarioResultMemoryPrinter, build_scenario_full_payload
from pyrit.output.score.json import JsonScorePrinter
from pyrit.output.scorer.json import JsonScorerMemoryPrinter


def test_scorer_keys_distinguish_optional_children_from_nullable_metrics() -> None:
    assert ScorerNode.__required_keys__ == {"class_name"}
    assert ScorerNode.__optional_keys__ == {"params", "children"}
    assert ScorerInfo.__required_keys__ == {"class_name", "metrics"}
    assert ScorerInfo.__optional_keys__ == {"params", "children"}
    hints = get_type_hints(ScorerInfo, include_extras=True)
    assert hints["metrics"] == dict[str, object] | None
    assert hints["params"] == NotRequired[dict[str, object]]
    assert get_type_hints(ScorerNode)["children"] == dict[str, ScorerNode | list[ScorerNode]]


def test_score_and_piece_key_contracts() -> None:
    assert ScoreInfo.__required_keys__ == {
        "scorer",
        "score_type",
        "score_value",
        "score_category",
        "score_rationale",
        "objective",
    }
    assert not ScoreInfo.__optional_keys__
    for key in ("scorer", "score_value", "score_category", "score_rationale", "objective"):
        assert type(None) in get_type_hints(ScoreInfo)[key].__args__
    assert ReasoningPieceInfo.__required_keys__ == {"data_type", "reasoning_summary"}
    assert not ReasoningPieceInfo.__optional_keys__
    assert get_type_hints(ReasoningPieceInfo)["reasoning_summary"] == str | None
    assert RegularPieceInfo.__optional_keys__ == {"partial_content", "scores"}
    assert RegularPieceInfo.__required_keys__ == {
        "data_type",
        "original_value_data_type",
        "converted_value_data_type",
        "original_value",
        "converted_value",
        "response_error",
    }
    assert get_type_hints(MessageInfo)["pieces"] == list[ReasoningPieceInfo | RegularPieceInfo]
    assert ConversationInfo.__required_keys__ == AttackInfo.__required_keys__ | {"conversation_id", "messages"}
    assert "scorer" in ScenarioOverview.__required_keys__
    assert get_type_hints(ScenarioOverview)["scorer"] == ScorerInfo | None


async def test_nested_scorer_contract_and_serialization() -> None:
    leaf = ComponentIdentifier(class_name="Leaf", class_module="tests")
    root = ComponentIdentifier(
        class_name="Composite",
        class_module="tests",
        params={"threshold": 0.5},
        children={"sub_scorers": [leaf], "fallback": leaf},
    )
    printer = JsonScorerMemoryPrinter()
    with patch.object(printer, "_get_objective_metrics", return_value=None):
        payload = printer.build(scorer_identifier=root)
        assert_type(payload, ScorerInfo)
        expected = {
            "class_name": "Composite",
            "params": {"threshold": 0.5},
            "children": {"sub_scorers": [{"class_name": "Leaf"}], "fallback": {"class_name": "Leaf"}},
            "metrics": None,
        }
        assert payload == expected
        assert await printer.render_async(scorer_identifier=root) == json.dumps(expected, indent=2)
        bare = printer.build(scorer_identifier=leaf)
        assert bare.keys() == ScorerInfo.__required_keys__
        assert bare["metrics"] is None


def test_score_nullable_fields_are_present() -> None:
    score = Score(
        score_type="true_false", score_value="true", score_category=None, score_rationale=None, objective=None
    )
    payload = JsonScorePrinter().build(score)
    assert_type(payload, ScoreInfo)
    assert payload.keys() == ScoreInfo.__required_keys__
    assert payload["scorer"] is None
    assert payload["score_category"] is None
    assert payload["score_rationale"] is None
    assert payload["objective"] is None


@pytest.mark.parametrize("blocked", [False, True])
async def test_regular_piece_optional_fields_follow_content(blocked: bool, patch_central_database) -> None:
    piece = MessagePiece(
        role="assistant",
        original_value="hello",
        conversation_id="contract",
        sequence=0,
        response_error="blocked" if blocked else "none",
        prompt_metadata={"partial_content": "partial"},
    )
    score = Score(score_type="true_false", score_value="true", message_piece_id=str(piece.id))
    source = MemoryConversationSource()
    with patch.object(source, "get_scores_async", new=AsyncMock(return_value=[score])):
        printer = JsonConversationPrinter(source=source)
        messages = [Message(message_pieces=[piece])]
        plain = await printer.build_async(messages)
        assert_type(plain, list[MessageInfo])
        expected_keys = RegularPieceInfo.__required_keys__ | ({"partial_content"} if blocked else set())
        assert plain[0]["pieces"][0].keys() == expected_keys
        scored = await printer.build_async(messages, include_scores=True)
        assert scored[0]["pieces"][0].keys() == expected_keys | {"scores"}
        assert json.loads(await printer.render_async(messages, include_scores=True)) == scored


async def test_reasoning_null_is_not_an_omitted_regular_field(patch_central_database) -> None:
    piece = MessagePiece(
        role="assistant",
        original_value="not-json",
        original_value_data_type="reasoning",
        conversation_id="contract",
        sequence=0,
    )
    printer = JsonConversationPrinter(source=MemoryConversationSource())
    messages = [Message(message_pieces=[piece])]
    assert await printer.build_async(messages) == []
    payload = await printer.build_async(messages, include_reasoning_summaries=True)
    assert payload[0]["pieces"] == [{"data_type": "reasoning", "reasoning_summary": None}]
    assert payload[0]["pieces"][0].keys() == ReasoningPieceInfo.__required_keys__


async def test_scenario_overloads_and_composite_contracts(patch_central_database) -> None:
    attack = AttackResult(conversation_id="contract", objective="test")
    result = make_scenario_result(attack_results={"technique": [attack]})
    printer = JsonScenarioResultMemoryPrinter()
    overview = printer.build(result)
    assert_type(overview, ScenarioOverview)
    threaded = await asyncio.to_thread(lambda: printer.build(result, view="overview"))
    assert_type(threaded, ScenarioOverview)
    assert threaded == overview
    assert overview.keys() == ScenarioOverview.__required_keys__
    assert overview["scorer"] is None
    attacks = printer.build(result, view="attacks")
    assert_type(attacks, ScenarioAttacks)
    assert attacks.keys() == ScenarioAttacks.__required_keys__
    assert attacks["attacks"][0].keys() == AttackInfo.__required_keys__
    assert attacks["attacks"][0]["score"] is None
    payload = build_scenario_full_payload(result=result, overview=overview, entries=[("technique", attack, [])])
    assert_type(payload, ScenarioFull)
    assert payload.keys() == ScenarioFull.__required_keys__
    assert payload["conversations"][0].keys() == ConversationInfo.__required_keys__
    assert json.loads(await printer.render_async(result)) == overview
