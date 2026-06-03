# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import datetime, timezone

import pytest

import pyrit
from pyrit.models import ComponentIdentifier
from pyrit.models.conversation_reference import ConversationReference, ConversationType
from pyrit.models.results.attack_result import AttackOutcome, AttackResult
from pyrit.models.retry_event import RetryEvent
from pyrit.models.scenario_result import ScenarioIdentifier, ScenarioResult


def _make_scenario_identifier(**kwargs):
    defaults = {"name": "TestScenario", "description": "A test", "scenario_version": 1}
    defaults.update(kwargs)
    return ScenarioIdentifier(**defaults)


def _make_component_identifier_dict(class_name="TestTarget"):
    return ComponentIdentifier.from_dict({"__type__": class_name, "__module__": "test.module", "params": {}})


def _make_attack_result(*, objective="test objective", outcome=AttackOutcome.SUCCESS):
    return AttackResult(
        conversation_id=str(uuid.uuid4()),
        objective=objective,
        outcome=outcome,
    )


class TestScenarioIdentifier:
    def test_init_basic(self):
        si = ScenarioIdentifier(name="MySc")
        assert si.name == "MySc"
        assert si.description == ""
        assert si.version == 1
        assert si.init_data is None

    def test_init_with_all_params(self):
        si = ScenarioIdentifier(
            name="MySc",
            description="desc",
            scenario_version=2,
            init_data={"key": "val"},
            pyrit_version="1.0.0",
        )
        assert si.version == 2
        assert si.init_data == {"key": "val"}
        assert si.pyrit_version == "1.0.0"

    def test_init_default_pyrit_version(self):
        si = ScenarioIdentifier(name="X")
        assert si.pyrit_version == pyrit.__version__


class TestScenarioResult:
    def test_init_basic(self):
        si = _make_scenario_identifier()
        target_id = _make_component_identifier_dict()
        scorer_id = _make_component_identifier_dict("TestScorer")
        result = ScenarioResult(
            scenario_identifier=si,
            objective_target_identifier=target_id,
            attack_results={"strat1": []},
            objective_scorer_identifier=scorer_id,
        )
        assert result.scenario_identifier is si
        assert result.scenario_run_state == "CREATED"
        assert result.labels == {}
        assert result.number_tries == 0
        assert isinstance(result.id, uuid.UUID)

    def test_init_with_explicit_id(self):
        si = _make_scenario_identifier()
        explicit_id = uuid.uuid4()
        result = ScenarioResult(
            scenario_identifier=si,
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
            id=explicit_id,
        )
        assert result.id == explicit_id

    def test_get_strategies_used(self):
        si = _make_scenario_identifier()
        result = ScenarioResult(
            scenario_identifier=si,
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={"crescendo": [], "flip": []},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
        )
        strategies = result.get_strategies_used()
        assert sorted(strategies) == ["crescendo", "flip"]

    def test_get_objectives_all(self):
        ar1 = _make_attack_result(objective="obj1")
        ar2 = _make_attack_result(objective="obj2")
        ar3 = _make_attack_result(objective="obj1")
        result = ScenarioResult(
            scenario_identifier=_make_scenario_identifier(),
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={"s1": [ar1, ar3], "s2": [ar2]},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
        )
        objectives = result.get_objectives()
        assert sorted(objectives) == ["obj1", "obj2"]

    def test_get_objectives_by_attack_name(self):
        ar1 = _make_attack_result(objective="obj1")
        ar2 = _make_attack_result(objective="obj2")
        result = ScenarioResult(
            scenario_identifier=_make_scenario_identifier(),
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={"s1": [ar1], "s2": [ar2]},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
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
        sr = ScenarioResult(
            scenario_identifier=_make_scenario_identifier(),
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={"s1": results},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
        )
        assert sr.objective_achieved_rate() == 50

    def test_objective_achieved_rate_empty(self):
        sr = ScenarioResult(
            scenario_identifier=_make_scenario_identifier(),
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={"s1": []},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
        )
        assert sr.objective_achieved_rate() == 0

    def test_objective_achieved_rate_by_name(self):
        sr = ScenarioResult(
            scenario_identifier=_make_scenario_identifier(),
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={
                "s1": [_make_attack_result(outcome=AttackOutcome.SUCCESS)],
                "s2": [_make_attack_result(outcome=AttackOutcome.FAILURE)],
            },
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
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
        sr = ScenarioResult(
            scenario_identifier=_make_scenario_identifier(),
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
        )
        assert sr.error_attack_result_ids == []

    def test_error_attack_result_ids_stored(self):
        """error_attack_result_ids are stored correctly."""
        sr = ScenarioResult(
            scenario_identifier=_make_scenario_identifier(),
            objective_target_identifier=ComponentIdentifier.from_dict({}),
            attack_results={},
            objective_scorer_identifier=ComponentIdentifier.from_dict({}),
            error_attack_result_ids=["id-1", "id-2"],
        )
        assert sr.error_attack_result_ids == ["id-1", "id-2"]


def test_scenario_identifier_to_dict_from_dict_roundtrip():
    original = ScenarioIdentifier(
        name="ContentHarms",
        description="Tests content harm scenarios",
        scenario_version=3,
        init_data={"max_turns": 5, "strategy": "crescendo"},
        pyrit_version="0.14.0",
    )
    roundtripped = ScenarioIdentifier.from_dict(original.to_dict())
    assert original.to_dict() == roundtripped.to_dict()


def test_scenario_result_to_dict_from_dict_roundtrip():
    scenario_id = ScenarioIdentifier(
        name="ContentHarms",
        description="Tests content harm scenarios",
        scenario_version=2,
        pyrit_version="0.14.0",
    )
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
    original = ScenarioResult(
        id=uuid.UUID("12345678-1234-1234-1234-123456789abc"),
        scenario_identifier=scenario_id,
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
    roundtripped = ScenarioResult.from_dict(original.to_dict())
    assert original.to_dict() == roundtripped.to_dict()
    # The nested identifier must preserve the legacy ``scenario_version`` wire key.
    assert "scenario_version" in original.to_dict()["scenario_identifier"]
    assert "version" not in original.to_dict()["scenario_identifier"]


def test_scenario_identifier_from_dict_missing_pyrit_version_uses_current():
    """A payload missing pyrit_version now resolves to the current version via the Pydantic default."""
    data = {
        "name": "Legacy",
        "description": "loaded from older payload",
        "scenario_version": 1,
        "init_data": None,
        # pyrit_version intentionally absent
    }
    identifier = ScenarioIdentifier.from_dict(data)
    assert identifier.pyrit_version == pyrit.__version__


def test_scenario_result_from_dict_preserves_missing_completion_time():
    """An in-progress scenario serialized without completion_time should round-trip with completion_time=None."""
    scenario_id = ScenarioIdentifier(name="Test", scenario_version=1, pyrit_version="0.14.0")
    target_id = ComponentIdentifier(class_name="OpenAIChatTarget", class_module="pyrit.prompt_target")

    original = ScenarioResult(
        scenario_identifier=scenario_id,
        objective_target_identifier=target_id,
        objective_scorer_identifier=None,
        attack_results={},
        scenario_run_state="IN_PROGRESS",
    )
    original.completion_time = None  # type: ignore[ty:invalid-assignment]

    roundtripped = ScenarioResult.from_dict(original.to_dict())
    assert roundtripped.completion_time is None
    assert roundtripped.scenario_run_state == "IN_PROGRESS"


def test_scenario_identifier_to_dict_from_dict_emit_deprecation_warnings():
    identifier = ScenarioIdentifier(name="Test", scenario_version=1, pyrit_version="0.14.0")
    with pytest.warns(DeprecationWarning):
        payload = identifier.to_dict()
    with pytest.warns(DeprecationWarning):
        ScenarioIdentifier.from_dict(payload)


def test_scenario_result_to_dict_from_dict_emit_deprecation_warnings():
    scenario_id = ScenarioIdentifier(name="Test", scenario_version=1, pyrit_version="0.14.0")
    result = ScenarioResult(
        scenario_identifier=scenario_id,
        objective_target_identifier=ComponentIdentifier.from_dict({}),
        objective_scorer_identifier=None,
        attack_results={},
    )
    with pytest.warns(DeprecationWarning):
        payload = result.to_dict()
    with pytest.warns(DeprecationWarning):
        ScenarioResult.from_dict(payload)


def test_scenario_result_display_group_map_is_public_field():
    scenario_id = ScenarioIdentifier(name="Test", scenario_version=1, pyrit_version="0.14.0")
    result = ScenarioResult(
        scenario_identifier=scenario_id,
        objective_target_identifier=ComponentIdentifier.from_dict({}),
        objective_scorer_identifier=None,
        attack_results={"crescendo": []},
        display_group_map={"crescendo": "Crescendo Attack"},
    )
    assert "display_group_map" in ScenarioResult.model_fields
    assert result.display_group_map == {"crescendo": "Crescendo Attack"}
    # Mutable and writable (used by benchmark merge logic).
    result.display_group_map["foundry"] = "Foundry"
    assert result.display_group_map["foundry"] == "Foundry"
