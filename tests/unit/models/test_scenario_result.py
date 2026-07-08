# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import datetime, timezone

from pyrit.models import (
    ComponentIdentifier,
    ConversationReference,
    ConversationType,
    ScenarioResult,
)
from pyrit.models.results.attack_result import AttackOutcome, AttackResult
from pyrit.models.retry_event import RetryEvent
from tests.unit.mocks import make_scenario_result


def _make_component_identifier_dict(class_name="TestTarget"):
    return ComponentIdentifier.model_validate({"__type__": class_name, "__module__": "test.module", "params": {}})


def _make_attack_result(*, objective="test objective", outcome=AttackOutcome.SUCCESS):
    return AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective=objective,
        outcome=outcome,
    )


class TestScenarioResult:
    def test_init_basic(self):
        target_id = _make_component_identifier_dict()
        scorer_id = _make_component_identifier_dict("TestScorer")
        result = make_scenario_result(
            scenario_name="TestScenario",
            objective_target_identifier=target_id,
            attack_results={"strat1": []},
            objective_scorer_identifier=scorer_id,
        )
        assert result.scenario_name == "TestScenario"
        assert result.scenario_version == 1
        assert result.scenario_run_state == "CREATED"
        assert result.labels == {}
        assert result.number_tries == 0
        assert isinstance(result.id, uuid.UUID)

    def test_init_with_explicit_id(self):
        explicit_id = uuid.uuid4()
        result = make_scenario_result(
            scenario_name="TestScenario",
            objective_target_identifier=ComponentIdentifier.model_validate({}),
            attack_results={},
            objective_scorer_identifier=ComponentIdentifier.model_validate({}),
            id=explicit_id,
        )
        assert result.id == explicit_id

    def test_get_strategies_used(self):
        result = make_scenario_result(
            scenario_name="TestScenario",
            objective_target_identifier=ComponentIdentifier.model_validate({}),
            attack_results={"crescendo": [], "flip": []},
            objective_scorer_identifier=ComponentIdentifier.model_validate({}),
        )
        strategies = result.get_strategies_used()
        assert sorted(strategies) == ["crescendo", "flip"]

    def test_get_objectives_all(self):
        ar1 = _make_attack_result(objective="obj1")
        ar2 = _make_attack_result(objective="obj2")
        ar3 = _make_attack_result(objective="obj1")
        result = make_scenario_result(
            scenario_name="TestScenario",
            objective_target_identifier=ComponentIdentifier.model_validate({}),
            attack_results={"s1": [ar1, ar3], "s2": [ar2]},
            objective_scorer_identifier=ComponentIdentifier.model_validate({}),
        )
        objectives = result.get_objectives()
        assert sorted(objectives) == ["obj1", "obj2"]

    def test_get_objectives_by_attack_name(self):
        ar1 = _make_attack_result(objective="obj1")
        ar2 = _make_attack_result(objective="obj2")
        result = make_scenario_result(
            scenario_name="TestScenario",
            objective_target_identifier=ComponentIdentifier.model_validate({}),
            attack_results={"s1": [ar1], "s2": [ar2]},
            objective_scorer_identifier=ComponentIdentifier.model_validate({}),
        )
        assert result.get_objectives(atomic_attack_name="s1") == ["obj1"]
        assert result.get_objectives(atomic_attack_name="nonexistent") == []

    def test_objective_achieved_rate_all(self):
        results = [
            _make_attack_result(outcome=AttackOutcome.SUCCESS),
            _make_attack_result(outcome=AttackOutcome.FAILURE),
            _make_attack_result(outcome=AttackOutcome.SUCCESS),
            _make_attack_result(outcome=AttackOutcome.UNDETERMINED),
        ]
        sr = make_scenario_result(
            scenario_name="TestScenario",
            objective_target_identifier=ComponentIdentifier.model_validate({}),
            attack_results={"s1": results},
            objective_scorer_identifier=ComponentIdentifier.model_validate({}),
        )
        assert sr.objective_achieved_rate() == 50

    def test_objective_achieved_rate_empty(self):
        sr = make_scenario_result(
            scenario_name="TestScenario",
            objective_target_identifier=ComponentIdentifier.model_validate({}),
            attack_results={"s1": []},
            objective_scorer_identifier=ComponentIdentifier.model_validate({}),
        )
        assert sr.objective_achieved_rate() == 0

    def test_objective_achieved_rate_by_name(self):
        sr = make_scenario_result(
            scenario_name="TestScenario",
            objective_target_identifier=ComponentIdentifier.model_validate({}),
            attack_results={
                "s1": [_make_attack_result(outcome=AttackOutcome.SUCCESS)],
                "s2": [_make_attack_result(outcome=AttackOutcome.FAILURE)],
            },
            objective_scorer_identifier=ComponentIdentifier.model_validate({}),
        )
        assert sr.objective_achieved_rate(atomic_attack_name="s1") == 100
        assert sr.objective_achieved_rate(atomic_attack_name="s2") == 0
        assert sr.objective_achieved_rate(atomic_attack_name="missing") == 0

    def test_normalize_scenario_name_snake_case(self):
        assert ScenarioResult.normalize_scenario_name("content_harms") == "ContentHarms"
        assert ScenarioResult.normalize_scenario_name("foundry") == "foundry"

    def test_normalize_scenario_name_already_pascal(self):
        assert ScenarioResult.normalize_scenario_name("ContentHarms") == "ContentHarms"

    def test_normalize_scenario_name_mixed_case_with_underscore(self):
        assert ScenarioResult.normalize_scenario_name("Content_harms") == "Content_harms"

    def test_error_attack_result_ids_defaults_to_empty(self):
        """error_attack_result_ids defaults to empty list."""
        sr = make_scenario_result(
            scenario_name="TestScenario",
            objective_target_identifier=ComponentIdentifier.model_validate({}),
            attack_results={},
            objective_scorer_identifier=ComponentIdentifier.model_validate({}),
        )
        assert sr.error_attack_result_ids == []

    def test_error_attack_result_ids_stored(self):
        """error_attack_result_ids are stored correctly."""
        sr = make_scenario_result(
            scenario_name="TestScenario",
            objective_target_identifier=ComponentIdentifier.model_validate({}),
            attack_results={},
            objective_scorer_identifier=ComponentIdentifier.model_validate({}),
            error_attack_result_ids=["id-1", "id-2"],
        )
        assert sr.error_attack_result_ids == ["id-1", "id-2"]


def test_scenario_result_to_dict_from_dict_roundtrip():
    target_id = ComponentIdentifier(
        class_name="OpenAIChatTarget",
        class_module="pyrit.prompt_target",
        params={"endpoint": "https://api.example.com"},
    )
    scorer_id = ComponentIdentifier(
        class_name="SelfAskTrueFalseScorer",
        class_module="pyrit.score",
    )
    attack_result = AttackResult(
        conversation_id="conv-1",
        objective="test objective",
        outcome=AttackOutcome.SUCCESS,
        outcome_reason="Objective achieved",
        executed_turns=3,
        execution_time_ms=1500,
        timestamp=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        related_conversations={
            ConversationReference(
                conversation_id="conv-2",
                conversation_type=ConversationType.PRUNED,
                description="pruned branch",
            ),
        },
        metadata={"model": "gpt-4"},
        labels={"category": "violence"},
        retry_events=[
            RetryEvent(
                attempt_number=1,
                function_name="send_prompt",
                exception_type="TimeoutError",
                exception_message="timed out",
                component_role="target",
                timestamp=datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            ),
        ],
        total_retries=1,
    )
    original = make_scenario_result(
        id=uuid.UUID("12345678-1234-1234-1234-123456789abc"),
        scenario_name="ContentHarms",
        scenario_version=2,
        pyrit_version="0.14.0",
        objective_target_identifier=target_id,
        objective_scorer_identifier=scorer_id,
        scenario_run_state="COMPLETED",
        attack_results={"crescendo": [attack_result]},
        display_group_map={"crescendo": "Crescendo Attack"},
        labels={"env": "test"},
        creation_time=datetime(2026, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
        completion_time=datetime(2026, 1, 15, 12, 30, 0, tzinfo=timezone.utc),
        number_tries=1,
        error_attack_result_ids=["err-1"],
        error_message="partial failure",
        error_type="RuntimeError",
    )
    dumped = original.model_dump(mode="json", by_alias=True)
    roundtripped = ScenarioResult.model_validate(dumped)
    assert dumped == roundtripped.model_dump(mode="json", by_alias=True)
    # Identity facts round-trip as denormalized flat scalars on the result.
    assert dumped["scenario_name"] == "ContentHarms"
    assert dumped["scenario_version"] == 2
    assert dumped["pyrit_version"] == "0.14.0"


def test_scenario_result_from_dict_preserves_missing_completion_time():
    """An in-progress scenario serialized without completion_time should round-trip with completion_time=None."""
    target_id = ComponentIdentifier(class_name="OpenAIChatTarget", class_module="pyrit.prompt_target")

    original = make_scenario_result(
        scenario_name="Test",
        pyrit_version="0.14.0",
        objective_target_identifier=target_id,
        objective_scorer_identifier=None,
        attack_results={},
        scenario_run_state="IN_PROGRESS",
    )
    original.completion_time = None  # type: ignore[ty:invalid-assignment]

    roundtripped = ScenarioResult.model_validate(original.model_dump(mode="json", by_alias=True))
    assert roundtripped.completion_time is None
    assert roundtripped.scenario_run_state == "IN_PROGRESS"


def test_scenario_result_display_group_map_is_public_field():
    result = make_scenario_result(
        scenario_name="Test",
        pyrit_version="0.14.0",
        objective_target_identifier=ComponentIdentifier.model_validate({}),
        objective_scorer_identifier=None,
        attack_results={"crescendo": []},
        display_group_map={"crescendo": "Crescendo Attack"},
    )
    assert "display_group_map" in ScenarioResult.model_fields
    assert result.display_group_map == {"crescendo": "Crescendo Attack"}
    # Mutable and writable (used by benchmark merge logic).
    result.display_group_map["foundry"] = "Foundry"
    assert result.display_group_map["foundry"] == "Foundry"
