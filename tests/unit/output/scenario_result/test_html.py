# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import uuid
from typing import Any

from unit.mocks import make_scenario_result

from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    Message,
    MessagePiece,
    Score,
)
from pyrit.output.conversation.json import JsonConversationPrinter
from pyrit.output.scenario_result.html import HtmlScenarioReportPrinter
from pyrit.output.scenario_result.json import (
    JsonScenarioResultPrinter,
    build_scenario_full_payload,
)


def _payload(*, piece_value: str = "hello world") -> dict[str, Any]:
    return {
        "view": "full",
        "scenario_result_id": "sid-1",
        "overview": {
            "view": "overview",
            "scenario": {
                "name": "MyScenario",
                "id": "sid-1",
                "version": 1,
                "pyrit_version": "1.2.0",
                "description": "a description",
            },
            "target": {"type": "FakeTarget", "model": "gpt-x", "endpoint": "https://e"},
            "scorer": None,
            "stats": {
                "total_techniques": 2,
                "total_results": 3,
                "overall_success_rate": 67,
                "unique_objectives": 2,
            },
            "groups": [{"name": "g1", "num_results": 3, "success_rate": 67}],
        },
        "conversations": [
            {
                "id": "a1",
                "technique": "tech_a",
                "objective": "obj-1",
                "outcome": "success",
                "executed_turns": 2,
                "score": "true",
                "conversation_id": "c1",
                "messages": [
                    {
                        "role": "user",
                        "is_simulated": False,
                        "pieces": [
                            {
                                "data_type": "text",
                                "original_value": piece_value,
                                "converted_value": piece_value,
                                "response_error": None,
                            }
                        ],
                    }
                ],
            }
        ],
    }


async def test_html_report_contains_summary_and_transcript():
    html = await HtmlScenarioReportPrinter().render_async(_payload())

    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "MyScenario" in html
    assert "67%" in html
    assert "[SUCCESS] tech_a" in html
    assert "hello world" in html


async def test_html_report_escapes_untrusted_content():
    html = await HtmlScenarioReportPrinter().render_async(_payload(piece_value="<script>alert(1)</script>"))

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


async def test_html_report_renders_media_piece_as_placeholder():
    payload = _payload()
    payload["conversations"][0]["messages"][0]["pieces"][0] = {
        "data_type": "image_path",
        "original_value": "media/img.png",
        "converted_value": "media/img.png",
        "response_error": None,
    }

    html = await HtmlScenarioReportPrinter().render_async(payload)

    assert "[image_path] media/img.png" in html


async def test_html_report_renders_every_template_field():
    # Kitchen-sink payload: exercises every branch the template reads — reasoning
    # pieces, inline scores, response errors, groups — so a renamed/removed field
    # surfaces as a failing assertion instead of a silently blank section.
    payload = _payload()
    payload["conversations"][0]["messages"][0]["pieces"] = [
        {
            "data_type": "text",
            "original_value": "the-answer",
            "converted_value": "the-answer",
            "response_error": "boom",
            "scores": [
                {"scorer": "MyScorer", "score_value": "true", "score_rationale": "why-it-scored"},
            ],
        },
        {"data_type": "reasoning", "reasoning_summary": "the-reasoning"},
    ]

    html = await HtmlScenarioReportPrinter().render_async(payload)

    assert "gpt-x" in html  # target.model
    assert "https://e" in html  # target.endpoint
    assert "FakeTarget" in html  # target.type
    assert "g1" in html  # group name
    assert "obj-1" in html  # objective
    assert "the-answer" in html  # text piece
    assert "the-reasoning" in html  # p.reasoning_summary
    assert "error: boom" in html  # p.response_error
    assert "MyScorer" in html  # score.scorer
    assert "why-it-scored" in html  # score.score_rationale


async def test_html_report_omits_error_line_for_none_response_error():
    # "none" is the default (non-error) response_error, so it must not render an error line.
    payload = _payload()
    payload["conversations"][0]["messages"][0]["pieces"][0]["response_error"] = "none"

    html = await HtmlScenarioReportPrinter().render_async(payload)

    assert "error:" not in html


class _StubConversationSource:
    """Minimal ``ConversationSource`` returning canned scores per piece id."""

    def __init__(self, scores_by_piece: dict[str, list[Score]]) -> None:
        self._scores = scores_by_piece

    async def get_messages_async(self, *, conversation_id: str) -> list[Message]:
        return []

    async def get_scores_async(self, *, prompt_ids: list[str]) -> list[Score]:
        return [score for prompt_id in prompt_ids for score in self._scores.get(prompt_id, [])]


async def test_html_report_renders_payload_from_real_builders(patch_central_database):
    # Contract test: build the payload with the same builders production uses
    # rather than a hand-written fixture. If a builder renames a key the template
    # reads, the corresponding value stops rendering and an assertion below fails.
    target_id = ComponentIdentifier(
        class_name="MyFakeTarget",
        class_module="tests",
        params={"model_name": "gpt-contract", "endpoint": "https://contract.example"},
    )
    attack = AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective="contract-objective",
        outcome=AttackOutcome.SUCCESS,
    )
    result = make_scenario_result(
        objective_target_identifier=target_id,
        attack_results={"contract_tech": [attack]},
    )
    overview = JsonScenarioResultPrinter().build(result, view="overview")

    text_piece = MessagePiece(
        role="assistant",
        original_value="assistant-answer",
        original_value_data_type="text",
        conversation_id=attack.conversation_id,
        sequence=0,
    )
    reasoning_piece = MessagePiece(
        role="assistant",
        original_value=json.dumps({"summary": [{"type": "summary_text", "text": "chain-of-thought"}]}),
        original_value_data_type="reasoning",
        conversation_id=attack.conversation_id,
        sequence=1,
    )
    score = Score(
        score_type="true_false",
        score_value="true",
        score_category=["hate"],
        score_rationale="rationale-text",
        objective="contract-objective",
        message_piece_id=str(text_piece.id),
        scorer_class_identifier=ComponentIdentifier(class_name="ContractScorer", class_module="tests"),
    )
    source = _StubConversationSource({str(text_piece.id): [score]})
    messages = await JsonConversationPrinter(source=source).build_async(
        [Message(message_pieces=[text_piece]), Message(message_pieces=[reasoning_piece])],
        include_scores=True,
        include_reasoning_summaries=True,
    )

    payload = build_scenario_full_payload(
        result=result, overview=overview, entries=[("contract_tech", attack, messages)]
    )
    html = await HtmlScenarioReportPrinter().render_async(payload)

    assert "gpt-contract" in html  # target.model
    assert "https://contract.example" in html  # target.endpoint
    assert "MyFakeTarget" in html  # target.type
    assert "contract_tech" in html  # technique
    assert "contract-objective" in html  # objective
    assert "assistant-answer" in html  # text piece
    assert "chain-of-thought" in html  # p.reasoning_summary
    assert "ContractScorer" in html  # score.scorer
    assert "rationale-text" in html  # score.score_rationale
