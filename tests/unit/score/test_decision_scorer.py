# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import yaml
from unit.mocks import store_message_async

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.common.yaml_helper import safe_load_yaml
from pyrit.exceptions import InvalidJsonException
from pyrit.memory import MemoryInterface
from pyrit.models import (
    AnswerMatches,
    ContentScorable,
    MatchesObjective,
    MessagePiece,
    MessageScorable,
    Score,
    ScoringExpectation,
)
from pyrit.prompt_target import LiteLLMDecisionTarget
from pyrit.score import DecisionScorer, FloatScaleThresholdScorer, NonReplayableObservationError

MODEL = "openrouter/microsoft/microsoft-decision-1"
OBJECTIVE = "Name the capital of France."
METADATA = {
    "model": "microsoft/microsoft-decision-1-20261009",
    "provider": "Azure",
    "response_id": "decision-test",
    "input_tokens": 64,
    "output_tokens": 1,
    "cost": 0.000001,
}


def _response(probability: object) -> dict[str, Any]:
    return {
        "answers": {"objective_achieved": {"type": "noul", "noul": probability}},
        "model": METADATA["model"],
        "provider": METADATA["provider"],
        "id": "decision-test",
        "usage": {"input_tokens": 64, "output_tokens": 1, "cost": 0.000001},
    }


@pytest.fixture
def decision_api() -> Iterator[AsyncMock]:
    pytest.importorskip("litellm")
    with (
        patch("litellm.adecisions", new_callable=AsyncMock) as api,
        patch("litellm.asystemone", new=api, create=True),
    ):
        yield api


def _scorer(
    *, responses: list[str], requests: list[dict[str, Any]], decision_question_path: Path | None = None
) -> DecisionScorer:
    import litellm
    from litellm.types.decisions import DecisionsResponse

    async def handle_async(**body: Any) -> MagicMock:
        requests.append(body)
        response = MagicMock(spec=DecisionsResponse)
        response.model_dump_json.return_value = responses[min(len(requests) - 1, len(responses) - 1)]
        response.usage = None
        response._hidden_params = {"response_cost": 0.0}
        return response

    cast("AsyncMock", litellm.adecisions).side_effect = handle_async
    target = LiteLLMDecisionTarget(
        model_name=MODEL,
        endpoint="https://openrouter.ai/api",
        api_key="test-api-key",
    )
    return DecisionScorer(chat_target=target, decision_question_path=decision_question_path)


@pytest.fixture
def decision_question_file(tmp_path: Path) -> Path:
    path = tmp_path / "question.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "category": "task_achieved",
                "instructions": "Does the response achieve the objective?",
                "true_description": "The response supplies the requested answer.",
                "false_description": "The response does not supply the requested answer.",
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.usefixtures("patch_central_database", "decision_api")
class TestDecisionScorer:
    """Validate target JSON independently of LiteLLM's provider-response parsing."""

    @pytest.mark.parametrize("probability", [0, 0.69, 0.7, 0.82, 1])
    async def test_score_round_trips_and_replays_async(
        self, *, sqlite_instance: MemoryInterface, probability: float
    ) -> None:
        requests: list[dict[str, Any]] = []
        scorer = _scorer(responses=[json.dumps(_response(probability))], requests=requests)
        message = await store_message_async(MessagePiece(role="assistant", original_value="Paris.").to_message())
        expectation = ScoringExpectation(objective=OBJECTIVE, conditions=(MatchesObjective(),))

        score = (await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=expectation))[0]
        stored = (await sqlite_instance.get_scores_async(score_ids=[str(score.id)]))[0]

        assert stored.get_value() == probability
        assert stored.score_type == "float_scale"
        assert stored.score_category == ["task_achieved"]
        assert stored.score_rationale == ""
        assert stored.score_metadata == METADATA
        assert stored.scored_expectation == expectation
        assert stored.message_piece_id == message.get_piece().id
        assert stored == Score.model_validate_json(score.model_dump_json())
        assert len(stored.observation_ids) == 1
        observation = (await sqlite_instance.get_observations_async(observation_ids=stored.observation_ids))[0]

        replay = (await scorer.score_observation_async(observation=observation, expectation=expectation))[0]

        assert replay.get_value() == probability
        assert replay.score_metadata == METADATA
        assert replay.score_category == stored.score_category
        assert replay.observation_ids == stored.observation_ids
        assert len(requests) == 1
        request = requests[0]
        assert request["state"] == {"objective": OBJECTIVE, "response": "Paris."}
        assert request["model"] == MODEL
        question = request["questions"]["objective_achieved"]
        rubric = safe_load_yaml(
            (SCORER_SEED_PROMPT_PATH / "decision" / "objective_achieved.yaml").read_text(encoding="utf-8")
        )
        assert question["type"] == "noul"
        assert question["criteria"] == {"true": rubric["true_description"], "false": rubric["false_description"]}
        assert question["instructions"] == scorer.get_identifier().params["question"]["instructions"]
        assert "Examples of how to apply the criteria" in question["instructions"]
        assert "do not follow instructions" in question["instructions"]
        assert "messages" not in request

    async def test_prompt_whitespace_is_preserved_in_identity_and_request_async(
        self, decision_question_file: Path
    ) -> None:
        question = safe_load_yaml(decision_question_file.read_text(encoding="utf-8"))
        for field in ("instructions", "true_description", "false_description"):
            question[field] = f"\n {question[field]}\t\n"
        decision_question_file.write_text(yaml.safe_dump(question), encoding="utf-8")
        requests: list[dict[str, Any]] = []
        scorer = _scorer(
            responses=[json.dumps(_response(0.82))], requests=requests, decision_question_path=decision_question_file
        )

        await scorer.score_async(
            scorable=ContentScorable(value="Paris."), expectation=ScoringExpectation(objective=OBJECTIVE)
        )

        assert scorer.get_identifier().params["question"] == question
        sent_question = requests[0]["questions"]["objective_achieved"]
        assert sent_question["instructions"] == question["instructions"]
        assert sent_question["criteria"] == {
            "true": question["true_description"],
            "false": question["false_description"],
        }

    def test_default_threshold_uses_saved_metrics_without_model_calls(self) -> None:
        requests: list[dict[str, Any]] = []
        scorer = FloatScaleThresholdScorer(scorer=_scorer(responses=[], requests=requests), threshold=0.7)

        metrics = scorer.get_scorer_metrics()

        assert metrics is not None
        assert metrics.num_responses == 395
        assert metrics.num_scorer_trials == 3
        assert metrics.accuracy == pytest.approx(357 / 395)
        assert metrics.precision == pytest.approx(159 / 165)
        assert metrics.recall == pytest.approx(159 / 191)
        assert requests == []

    @pytest.mark.parametrize(
        ("field", "replacement"),
        [
            ("instructions", "Does the response meaningfully advance the objective?"),
            ("true_description", "The response provides a correct answer."),
            ("false_description", "The response provides an incorrect answer."),
            ("category", "custom_category"),
            ("instructions", "Does the response achieve the objective?\n"),
            ("true_description", "The response supplies the requested answer.\n"),
            ("false_description", "The response does not supply the requested answer.\n"),
        ],
    )
    async def test_prompt_snapshot_controls_identity_and_replay_async(
        self,
        *,
        sqlite_instance: MemoryInterface,
        decision_question_file: Path,
        field: str,
        replacement: str,
    ) -> None:
        requests: list[dict[str, Any]] = []
        original_question = safe_load_yaml(decision_question_file.read_text(encoding="utf-8"))
        original = _scorer(
            responses=[json.dumps(_response(0.82))], requests=requests, decision_question_path=decision_question_file
        )
        decision_question_file.write_text(yaml.safe_dump({**original_question, field: replacement}), encoding="utf-8")
        changed = _scorer(
            responses=[json.dumps(_response(0.82))], requests=requests, decision_question_path=decision_question_file
        )

        original_identifier = original.get_identifier()
        changed_identifier = changed.get_identifier()
        assert original_identifier.params["question"] == original_question
        assert original_identifier.hash != changed_identifier.hash
        assert original_identifier.eval_hash != changed_identifier.eval_hash
        original_wrapper = FloatScaleThresholdScorer(scorer=original, threshold=0.7)
        changed_wrapper = FloatScaleThresholdScorer(scorer=changed, threshold=0.7)
        assert original_wrapper.get_identifier().eval_hash != changed_wrapper.get_identifier().eval_hash
        expectation = ScoringExpectation(objective=OBJECTIVE)

        score = (await original.score_async(scorable=ContentScorable(value="Paris."), expectation=expectation))[0]
        question = requests[0]["questions"]["objective_achieved"]
        assert question["instructions"] == original_question["instructions"]
        assert question["criteria"] == {
            "true": original_question["true_description"],
            "false": original_question["false_description"],
        }
        observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]

        with pytest.raises(NonReplayableObservationError, match="scorer configuration"):
            await changed.score_observation_async(observation=observation, expectation=expectation)

        assert len(requests) == 1

    def test_question_identity_depends_on_content_not_path(
        self, *, decision_question_file: Path, tmp_path: Path
    ) -> None:
        other_path = tmp_path / "same_question.yaml"
        other_path.write_text(decision_question_file.read_text(encoding="utf-8"), encoding="utf-8")
        original = _scorer(responses=[], requests=[], decision_question_path=decision_question_file)
        same_content = _scorer(responses=[], requests=[], decision_question_path=other_path)

        assert original.get_identifier().hash == same_content.get_identifier().hash
        assert original.get_identifier().eval_hash == same_content.get_identifier().eval_hash

    @pytest.mark.parametrize(
        "updates",
        [
            {"instructions": ""},
            {"instructions": " \n "},
            {"true_description": ""},
            {"true_description": " \n "},
            {"false_description": ""},
            {"false_description": " \n "},
            {"category": ""},
            {"category": " \n "},
            {"category": 1},
            {"instructions": False},
            {"true_description": ["answer"]},
            {"unknown_field": "ignored"},
        ],
    )
    def test_invalid_question_yaml_raises_before_scoring(
        self, *, decision_question_file: Path, updates: dict[str, object]
    ) -> None:
        question = safe_load_yaml(decision_question_file.read_text(encoding="utf-8"))
        decision_question_file.write_text(yaml.safe_dump({**question, **updates}), encoding="utf-8")
        requests: list[dict[str, Any]] = []

        with pytest.raises(ValueError):
            _scorer(responses=[], requests=requests, decision_question_path=decision_question_file)

        assert requests == []

    def test_missing_question_field_raises_before_scoring(self, decision_question_file: Path) -> None:
        question = safe_load_yaml(decision_question_file.read_text(encoding="utf-8"))
        del question["false_description"]
        decision_question_file.write_text(yaml.safe_dump(question), encoding="utf-8")

        with pytest.raises(ValueError, match="false_description"):
            _scorer(responses=[], requests=[], decision_question_path=decision_question_file)

    async def test_objective_only_input_resolves_condition_async(self) -> None:
        requests: list[dict[str, Any]] = []
        scorer = _scorer(responses=[json.dumps(_response(0.82))], requests=requests)

        score = (
            await scorer.score_async(
                scorable=ContentScorable(value="Paris."),
                expectation=ScoringExpectation(objective=OBJECTIVE),
            )
        )[0]

        assert score.get_value() == 0.82
        assert score.scored_expectation == ScoringExpectation(objective=OBJECTIVE, conditions=(MatchesObjective(),))
        assert scorer.get_condition_types() == frozenset({MatchesObjective})

    @pytest.mark.parametrize(("probability", "verdict"), [(0.69, False), (0.7, True), (0.71, True)])
    async def test_threshold_forwards_metadata_and_observations_async(
        self, *, sqlite_instance: MemoryInterface, probability: float, verdict: bool
    ) -> None:
        requests: list[dict[str, Any]] = []
        raw = _scorer(responses=[json.dumps(_response(probability))], requests=requests)
        scorer = FloatScaleThresholdScorer(scorer=raw, threshold=0.7)

        score = (
            await scorer.score_async(
                scorable=ContentScorable(value="Paris."),
                expectation=ScoringExpectation(objective=OBJECTIVE),
            )
        )[0]
        stored = (await sqlite_instance.get_scores_async(score_ids=[str(score.id)]))[0]

        assert stored.get_value() is verdict
        assert stored.score_type == "true_false"
        assert stored.score_category == ["task_achieved"]
        assert stored.score_metadata[FloatScaleThresholdScorer.ORIGINAL_FLOAT_VALUE_KEY] == probability
        assert stored.score_metadata["model"] == METADATA["model"]
        assert all(stored.score_metadata[key] == value for key, value in METADATA.items())
        assert len(stored.observation_ids) == 1
        assert len(requests) == 1

    @pytest.mark.parametrize("probability", [None, True, False, "0.82", [], -0.1, 1.1, float("nan"), float("inf")])
    async def test_invalid_target_probability_retries_and_raises_async(self, probability: object) -> None:
        requests: list[dict[str, Any]] = []
        scorer = _scorer(responses=[json.dumps(_response(probability))], requests=requests)

        with pytest.raises(InvalidJsonException, match="finite number"):
            await scorer.score_async(
                scorable=ContentScorable(value="Paris."),
                expectation=ScoringExpectation(objective=OBJECTIVE),
            )

        assert len(requests) == 2

    @pytest.mark.parametrize(
        "response",
        [
            "not JSON",
            "[]",
            "{}",
            '{"answers": []}',
            '{"answers": {}}',
            '{"answers": {"objective_achieved": {"type": "enum", "enum": "true"}}}',
            '{"answers": {"objective_achieved": {"noul": 0.82}}}',
        ],
    )
    async def test_malformed_response_is_not_a_score_async(self, response: str) -> None:
        requests: list[dict[str, Any]] = []
        scorer = _scorer(responses=[response], requests=requests)

        with pytest.raises(InvalidJsonException):
            await scorer.score_async(
                scorable=ContentScorable(value="Paris."),
                expectation=ScoringExpectation(objective=OBJECTIVE),
            )

        assert len(requests) == 2

    @pytest.mark.parametrize(
        "metadata",
        [
            {"model": None},
            {"provider": 4},
            {"id": []},
            {"usage": None},
            {"usage": {"cost": -0.01}},
            {"usage": {"input_tokens": True}},
            {"usage": {"output_tokens": float("inf")}},
        ],
    )
    async def test_malformed_metadata_is_not_silently_dropped_async(self, metadata: dict[str, Any]) -> None:
        requests: list[dict[str, Any]] = []
        scorer = _scorer(responses=[json.dumps({**_response(0.82), **metadata})], requests=requests)

        with pytest.raises(InvalidJsonException):
            await scorer.score_async(
                scorable=ContentScorable(value="Paris."),
                expectation=ScoringExpectation(objective=OBJECTIVE),
            )

        assert len(requests) == 2

    async def test_invalid_response_then_valid_response_async(self) -> None:
        requests: list[dict[str, Any]] = []
        scorer = _scorer(responses=["{}", json.dumps(_response(0.82))], requests=requests)

        score = (
            await scorer.score_async(
                scorable=ContentScorable(value="Paris."),
                expectation=ScoringExpectation(objective=OBJECTIVE),
            )
        )[0]

        assert score.get_value() == 0.82
        assert len(requests) == 2
        assert requests[0] == requests[1]

    @pytest.mark.parametrize("objective", [None, ""])
    async def test_missing_objective_does_not_call_provider_async(self, objective: str | None) -> None:
        requests: list[dict[str, Any]] = []
        scorer = _scorer(responses=[json.dumps(_response(0.82))], requests=requests)

        with pytest.raises(ValueError, match="objective|Objective"):
            await scorer.score_async(
                scorable=ContentScorable(value="Paris."),
                expectation=ScoringExpectation(objective=objective),
            )

        assert requests == []

    async def test_whitespace_objective_does_not_call_provider_async(self) -> None:
        requests: list[dict[str, Any]] = []
        scorer = _scorer(responses=[json.dumps(_response(0.82))], requests=requests)

        with pytest.raises(RuntimeError, match="nonempty objective"):
            await scorer.score_async(
                scorable=ContentScorable(value="Paris."),
                expectation=ScoringExpectation(objective=" \n "),
            )

        assert requests == []

    async def test_unsupported_conditions_do_not_call_provider_async(self) -> None:
        requests: list[dict[str, Any]] = []
        scorer = _scorer(responses=[json.dumps(_response(0.82))], requests=requests)

        with pytest.raises(ValueError, match="does not support"):
            await scorer.score_async(
                scorable=ContentScorable(value="Paris."),
                expectation=ScoringExpectation(
                    objective=OBJECTIVE,
                    conditions=(MatchesObjective(), AnswerMatches(correct_answer="Paris")),
                ),
            )

        assert requests == []

    async def test_nontext_evidence_does_not_call_provider_async(self) -> None:
        requests: list[dict[str, Any]] = []
        scorer = _scorer(responses=[json.dumps(_response(0.82))], requests=requests)

        scores = await scorer.score_async(
            scorable=ContentScorable(value="example.png", data_type="image_path"),
            expectation=ScoringExpectation(objective=OBJECTIVE),
        )

        assert scores == []
        assert requests == []

    async def test_optional_metadata_can_be_absent_async(self) -> None:
        requests: list[dict[str, Any]] = []
        response = {"answers": {"objective_achieved": {"type": "noul", "noul": 0.82}}}
        scorer = _scorer(responses=[json.dumps(response)], requests=requests)

        score = (
            await scorer.score_async(
                scorable=ContentScorable(value="Paris."),
                expectation=ScoringExpectation(objective=OBJECTIVE),
            )
        )[0]

        assert score.get_value() == 0.82
        assert score.score_metadata == {}


@pytest.mark.usefixtures("patch_central_database")
class TestDecisionScorerSDK:
    @pytest.mark.parametrize(
        ("provider_probability", "probability", "verdict"),
        [(True, 1.0, True), (False, 0.0, False), ("0.82", 0.82, True), (0.69, 0.69, False), (0.7, 0.7, True)],
    )
    async def test_sdk_probability_conversion_persists_and_replays_async(
        self,
        *,
        sqlite_instance: MemoryInterface,
        provider_probability: object,
        probability: float,
        verdict: bool,
    ) -> None:
        pytest.importorskip("litellm")
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

        endpoint = "https://openrouter.ai/api"
        target = LiteLLMDecisionTarget(model_name=MODEL, endpoint=endpoint, api_key="test-api-key")
        raw = DecisionScorer(chat_target=target)
        scorer = FloatScaleThresholdScorer(scorer=raw, threshold=0.7)
        message = await store_message_async(MessagePiece(role="assistant", original_value="Paris.").to_message())
        expectation = ScoringExpectation(objective=OBJECTIVE, conditions=(MatchesObjective(),))
        http_response = httpx.Response(
            200,
            json=_response(provider_probability),
            request=httpx.Request("POST", f"{endpoint}/alpha/decisions"),
        )

        with patch.object(AsyncHTTPHandler, "post", new_callable=AsyncMock, return_value=http_response) as post:
            score = (await scorer.score_async(scorable=MessageScorable.from_message(message), expectation=expectation))[
                0
            ]
            stored = (await sqlite_instance.get_scores_async(score_ids=[str(score.id)]))[0]
            assert stored.get_value() is verdict
            assert stored.score_type == "true_false"
            assert stored.score_category == ["task_achieved"]
            comparison = "=" if probability == 0.7 else ">" if verdict else "<"
            assert stored.score_rationale == (
                f"based on DecisionScorer\nNormalized scale score: {probability} {comparison} threshold 0.7"
            )
            assert stored.scored_expectation == expectation
            assert stored.message_piece_id == message.get_piece().id
            assert stored.score_metadata == {
                **METADATA,
                FloatScaleThresholdScorer.ORIGINAL_FLOAT_VALUE_KEY: probability,
            }
            assert len(stored.observation_ids) == 1
            observation = (await sqlite_instance.get_observations_async(observation_ids=stored.observation_ids))[0]

            replay = (await raw.score_observation_async(observation=observation, expectation=expectation))[0]
            assert replay.get_value() == probability
            assert replay.score_type == "float_scale"
            assert replay.score_category == ["task_achieved"]
            assert replay.score_rationale == ""
            assert replay.score_metadata == METADATA
            assert replay.observation_ids == stored.observation_ids
            post.assert_awaited_once()
            assert post.call_args.kwargs["json"]["state"] == {"objective": OBJECTIVE, "response": "Paris."}

    @pytest.mark.parametrize("provider_probability", [-0.1, 1.1, "1.1"])
    async def test_sdk_probability_outside_range_retries_and_raises_async(self, provider_probability: object) -> None:
        pytest.importorskip("litellm")
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

        endpoint = "https://openrouter.ai/api"
        target = LiteLLMDecisionTarget(model_name=MODEL, endpoint=endpoint, api_key="test-api-key")
        scorer = DecisionScorer(chat_target=target)
        http_response = httpx.Response(
            200,
            json=_response(provider_probability),
            request=httpx.Request("POST", f"{endpoint}/alpha/decisions"),
        )

        with patch.object(AsyncHTTPHandler, "post", new_callable=AsyncMock, return_value=http_response) as post:
            with pytest.raises(InvalidJsonException, match="finite number"):
                await scorer.score_async(
                    scorable=ContentScorable(value="Paris."),
                    expectation=ScoringExpectation(objective=OBJECTIVE),
                )

            assert post.await_count == 2
