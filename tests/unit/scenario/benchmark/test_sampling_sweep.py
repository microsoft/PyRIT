# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for adversarial sampling sweeps over AdversarialBenchmark (GitHub issue #365). No network calls."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from unit.mocks import MockPromptTarget

from pyrit.analytics.adversarial_settings_analysis import read_trial_records, write_trial_records
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    AttackSeedGroup,
    ComponentIdentifier,
    ConversationReference,
    ConversationType,
    MessagePiece,
    ScenarioIdentifier,
    ScenarioResult,
    SeedObjective,
    TokenUsage,
)
from pyrit.prompt_target import OpenAIChatTarget, PromptTarget, TargetCapabilities, TargetConfiguration
from pyrit.registry import TargetRegistry
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario import DatasetAttackConfiguration
from pyrit.scenario.scenarios.benchmark.adversarial import (
    AdversarialBenchmark,
    _build_benchmark_technique,
    _get_benchmark_adversarial_guidance,
)
from pyrit.scenario.scenarios.benchmark.sampling_sweep import (
    SWEEP_EXPERIMENT_LABEL,
    SWEEP_REPETITION_LABEL,
    AdversarialGeneratorSpec,
    SamplingParameter,
    SamplingSweepPlan,
    SamplingVariant,
    UnsupportedParameterPolicy,
    build_variant_targets,
    check_role_separation,
    find_scorer_coupled_attacks,
    make_memory_token_reader,
    make_trial_id,
    objective_ids_by_text,
    records_from_scenario_result_async,
    registered_variants,
    run_sampling_sweep_async,
)
from pyrit.score import TrueFalseScorer
from pyrit.setup.initializers.techniques import build_technique_factories

TEMPERATURES = [0.2, 0.7, 1.2]


def _openai_factory(model_name: str, **fixed: Any):
    """Build OpenAIChatTarget variants offline; construction makes no network call."""

    def factory(sampling: Mapping[str, float]) -> PromptTarget:
        return OpenAIChatTarget(
            endpoint="https://example.invalid/openai/v1",
            api_key="unit-test",
            model_name=model_name,
            **fixed,
            **sampling,
        )

    return factory


def _spec(name: str = "gen_a", model_name: str = "model-a", **kwargs: Any) -> AdversarialGeneratorSpec:
    return AdversarialGeneratorSpec(name=name, factory=_openai_factory(model_name), **kwargs)


def _plan(**overrides: Any) -> SamplingSweepPlan:
    arguments: dict[str, Any] = {
        "generators": [_spec("gen_a", "model-a"), _spec("gen_b", "model-b")],
        "parameter": SamplingParameter.TEMPERATURE,
        "values": TEMPERATURES,
        "baseline_value": 1.2,
        "repetitions": 3,
    }
    arguments.update(overrides)
    return SamplingSweepPlan(**arguments)


def _scorer(chat_target: PromptTarget | None = None) -> MagicMock:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.get_chat_target.return_value = chat_target
    scorer.get_identifier.return_value = ComponentIdentifier(class_name="FixedScorer", class_module="tests.unit")
    return scorer


def _seed_groups(count: int = 2, *, behavior_ids: bool = True) -> list[AttackSeedGroup]:
    return [
        AttackSeedGroup(
            seeds=[
                SeedObjective(
                    value=f"objective text {index}",
                    metadata={"BehaviorID": f"behavior_{index}"} if behavior_ids else {},
                )
            ]
        )
        for index in range(count)
    ]


def _attack_result(objective: str, outcome: AttackOutcome, *, minutes: int = 0, **fields: Any) -> AttackResult:
    return AttackResult(
        conversation_id=f"conv-{objective}-{minutes}",
        objective=objective,
        outcome=outcome,
        timestamp=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minutes),
        **fields,
    )


def _scenario_result(
    *, attack_results: dict[str, list[AttackResult]], display_group_map: dict[str, str]
) -> ScenarioResult:
    return ScenarioResult(
        scenario_identifier=ScenarioIdentifier(name="AdversarialBenchmark", scenario_version=7),
        objective_target_identifier=ComponentIdentifier(class_name="MockPromptTarget", class_module="tests.unit"),
        attack_results=attack_results,
        display_group_map=display_group_map,
        objective_scorer_identifier=ComponentIdentifier(class_name="FixedScorer", class_module="tests.unit"),
    )


class TestSamplingSweepPlan:
    def test_pilot_matrix_size(self):
        plan = _plan()
        assert len(plan.variants) == 6
        assert plan.planned_attack_count(objective_count=14) == 6 * 14 * 3

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"generators": []}, "at least one generator"),
            ({"generators": [_spec("gen_a"), _spec("gen_a")]}, "unique"),
            ({"values": [0.7]}, "at least two values"),
            ({"values": [0.7, 0.7]}, "unique"),
            ({"values": [0.2, 2.5], "baseline_value": 0.2}, "outside"),
            ({"values": [0.2, float("nan")], "baseline_value": 0.2}, "finite"),
            ({"baseline_value": 1.0}, "baseline_value"),
            ({"repetitions": 0}, "repetitions"),
            ({"repetitions": True}, "repetitions"),
            ({"technique": ""}, "technique"),
        ],
    )
    def test_invalid_designs_are_rejected(self, overrides: dict[str, Any], message: str):
        with pytest.raises(ValueError, match=message):
            _plan(**overrides)

    def test_top_p_uses_its_own_range(self):
        with pytest.raises(ValueError, match="outside"):
            _plan(parameter=SamplingParameter.TOP_P, values=[0.5, 1.2], baseline_value=0.5)

    @pytest.mark.parametrize("name", ["Gen", "gen-a", "gen__a", "_gen", ""])
    def test_generator_names_must_be_registry_safe(self, name: str):
        with pytest.raises(ValueError, match="Generator name"):
            _spec(name)

    def test_unsupported_parameter_raises_by_default(self):
        reasoning = _spec("reasoning", supported_parameters=frozenset({SamplingParameter.TOP_P}))
        with pytest.raises(ValueError, match="does not support temperature"):
            _plan(generators=[_spec("gen_a"), reasoning])

    def test_unsupported_parameter_can_be_skipped_and_is_reported(self):
        reasoning = _spec("reasoning", supported_parameters=frozenset({SamplingParameter.TOP_P}))
        plan = _plan(
            generators=[_spec("gen_a"), reasoning], unsupported_parameter_policy=UnsupportedParameterPolicy.SKIP
        )
        assert plan.skipped_generators == {"reasoning": "reasoning does not support temperature."}
        assert {variant.generator_name for variant in plan.variants} == {"gen_a"}

    def test_sweep_with_no_supporting_generator_is_rejected(self):
        reasoning = _spec("reasoning", supported_parameters=frozenset())
        with pytest.raises(ValueError, match="No generator supports"):
            _plan(generators=[reasoning], unsupported_parameter_policy=UnsupportedParameterPolicy.SKIP)

    def test_variant_registry_names_are_deterministic(self):
        names = [variant.registry_name for variant in _plan().variants]
        assert names[:3] == ["gen_a__temperature_0p2", "gen_a__temperature_0p7", "gen_a__temperature_1p2"]

    def test_nearby_temperatures_get_distinct_registry_names(self):
        # The old :g formatting used six significant digits and silently collided.
        plan = _plan(values=[0.12345671, 0.12345672], baseline_value=0.12345671)
        names = [variant.registry_name for variant in plan.variants]
        assert len(names) == len(set(names))
        assert names[0] != names[1]

    def test_run_order_is_a_reproducible_permutation(self):
        plan = _plan(run_order_seed=11)
        first = plan.run_order(repetition=0)
        assert sorted(first, key=lambda v: v.registry_name) == sorted(plan.variants, key=lambda v: v.registry_name)
        assert first == _plan(run_order_seed=11).run_order(repetition=0)
        orders = {tuple(plan.run_order(repetition=repetition)) for repetition in range(5)}
        assert len(orders) > 1

    def test_trial_ids_are_stable_and_distinct(self):
        variant = SamplingVariant("gen_a", SamplingParameter.TEMPERATURE, 0.2)
        first = make_trial_id(experiment_id="exp", variant=variant, repetition=0, objective_id="o1")
        assert first == make_trial_id(experiment_id="exp", variant=variant, repetition=0, objective_id="o1")
        assert first != make_trial_id(experiment_id="exp", variant=variant, repetition=1, objective_id="o1")


@pytest.mark.usefixtures("patch_central_database")
class TestBuildVariantTargets:
    def test_each_variant_applies_its_temperature(self):
        targets = build_variant_targets(plan=_plan())
        for variant, target in targets.items():
            assert target.get_identifier().params["temperature"] == variant.value

    def test_other_sampling_settings_are_held_fixed(self):
        spec = AdversarialGeneratorSpec(name="gen_a", factory=_openai_factory("model-a", top_p=0.9, seed=7))
        targets = build_variant_targets(plan=_plan(generators=[spec]))
        params = [target.get_identifier().params for target in targets.values()]
        assert {(p["top_p"], p["seed"]) for p in params} == {(0.9, 7)}

    def test_factory_that_ignores_the_value_is_rejected(self):
        spec = AdversarialGeneratorSpec(
            name="gen_a", factory=lambda _: _openai_factory("model-a")({"temperature": 1.0})
        )
        with pytest.raises(ValueError, match="expected 0.2"):
            build_variant_targets(plan=_plan(generators=[spec]))

    def test_factory_that_changes_other_settings_is_rejected(self):
        models = iter(["model-a", "model-b", "model-c"])
        spec = AdversarialGeneratorSpec(name="gen_a", factory=lambda sampling: _openai_factory(next(models))(sampling))
        with pytest.raises(ValueError, match="differs from other gen_a variants"):
            build_variant_targets(plan=_plan(generators=[spec]))

    def test_shared_instances_are_rejected(self):
        shared: dict[str, PromptTarget] = {}

        def factory(sampling: Mapping[str, float]) -> PromptTarget:
            return shared.setdefault("target", _openai_factory("model-a")(sampling))

        spec = AdversarialGeneratorSpec(name="gen_a", factory=factory)
        with pytest.raises(ValueError):
            build_variant_targets(plan=_plan(generators=[spec]))

    def test_non_target_is_rejected(self):
        spec = AdversarialGeneratorSpec(name="gen_a", factory=lambda _: object())  # type: ignore[arg-type,return-value]
        with pytest.raises(ValueError, match="not a PromptTarget"):
            build_variant_targets(plan=_plan(generators=[spec]))

    def test_target_that_cannot_report_the_parameter_is_rejected(self):
        spec = AdversarialGeneratorSpec(name="gen_a", factory=lambda _: MockPromptTarget())
        with pytest.raises(ValueError, match="does not report temperature"):
            build_variant_targets(plan=_plan(generators=[spec]))

    def test_single_turn_target_is_rejected(self):
        single_turn = TargetConfiguration(capabilities=TargetCapabilities(supports_multi_turn=False))
        spec = AdversarialGeneratorSpec(
            name="gen_a", factory=lambda _: MockPromptTarget(custom_configuration=single_turn)
        )
        with pytest.raises(ValueError, match="multi_turn"):
            build_variant_targets(plan=_plan(generators=[spec]))


@pytest.mark.usefixtures("patch_central_database")
class TestRoleSeparation:
    def test_distinct_roles_pass(self):
        targets = build_variant_targets(plan=_plan())
        check_role_separation(
            adversarial_targets=targets.values(), objective_target=MockPromptTarget(), objective_scorer=_scorer()
        )

    def test_objective_target_cannot_be_a_variant(self):
        targets = build_variant_targets(plan=_plan())
        with pytest.raises(ValueError, match="objective target"):
            check_role_separation(
                adversarial_targets=targets.values(),
                objective_target=next(iter(targets.values())),
                objective_scorer=_scorer(),
            )

    def test_scorer_cannot_judge_with_a_variant(self):
        targets = build_variant_targets(plan=_plan())
        with pytest.raises(ValueError, match="objective scorer"):
            check_role_separation(
                adversarial_targets=targets.values(),
                objective_target=MockPromptTarget(),
                objective_scorer=_scorer(next(iter(targets.values()))),
            )


@pytest.mark.usefixtures("patch_central_database")
class TestRegisteredVariants:
    @pytest.fixture(autouse=True)
    def _isolated_registry(self):
        TargetRegistry.reset_registry_singleton()
        yield
        TargetRegistry.reset_registry_singleton()

    def test_variants_are_registered_only_inside_the_scope(self):
        targets = build_variant_targets(plan=_plan())
        registry = TargetRegistry.get_registry_singleton()
        with registered_variants(targets=targets) as names:
            for name, target in zip(names, targets.values(), strict=True):
                assert registry.instances.get(name) is target
        assert not set(names) & set(registry.instances.get_names())

    def test_variants_are_removed_after_an_error(self):
        targets = build_variant_targets(plan=_plan())
        with pytest.raises(RuntimeError), registered_variants(targets=targets):
            raise RuntimeError("benchmark failed")
        assert TargetRegistry.get_registry_singleton().instances.get_names() == []

    def test_existing_entries_are_never_replaced(self):
        targets = build_variant_targets(plan=_plan())
        existing = MockPromptTarget()
        registry = TargetRegistry.get_registry_singleton()
        registry.instances.register(existing, name="gen_a__temperature_0p7")
        with pytest.raises(ValueError, match="already exists"), registered_variants(targets=targets):
            pass
        assert registry.instances.get("gen_a__temperature_0p7") is existing
        assert registry.instances.get_names() == ["gen_a__temperature_0p7"]


class TestObjectiveIds:
    def test_harmbench_behavior_ids_are_used(self):
        assert objective_ids_by_text(seed_groups=_seed_groups()) == {
            "objective text 0": "behavior_0",
            "objective text 1": "behavior_1",
        }

    def test_hash_is_used_without_behavior_id(self):
        ids = objective_ids_by_text(seed_groups=_seed_groups(1, behavior_ids=False))
        assert ids["objective text 0"].startswith("sha256:")
        assert "objective text" not in ids["objective text 0"]

    def test_duplicate_ids_are_rejected(self):
        groups = [
            AttackSeedGroup(seeds=[SeedObjective(value=f"text {index}", metadata={"BehaviorID": "same"})])
            for index in range(2)
        ]
        with pytest.raises(ValueError, match="unique"):
            objective_ids_by_text(seed_groups=groups)

    def test_duplicate_text_cannot_silently_drop_trials(self):
        groups = [
            AttackSeedGroup(seeds=[SeedObjective(value="same objective", metadata={"BehaviorID": f"id-{i}"})])
            for i in range(2)
        ]
        with pytest.raises(ValueError, match="unique text"):
            objective_ids_by_text(seed_groups=groups)

    def test_empty_objective_set_is_rejected(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            objective_ids_by_text(seed_groups=[])


class TestRecordsFromScenarioResult:
    async def test_results_are_attributed_by_display_group_and_latest_attempt(self):
        plan = _plan(generators=[_spec("gen_a")], values=[0.2, 1.2])
        objective_ids = objective_ids_by_text(seed_groups=_seed_groups(2))
        result = _scenario_result(
            attack_results={
                "crescendo_simulated__gen_a__temperature_0p2_x": [
                    _attack_result("objective text 0", AttackOutcome.FAILURE, minutes=0),
                    _attack_result(
                        "objective text 0",
                        AttackOutcome.SUCCESS,
                        minutes=5,
                        executed_turns=3,
                        metadata={"backtrack_count": 2},
                    ),
                    _attack_result("objective text 1", AttackOutcome.UNDETERMINED),
                ],
                "crescendo_simulated__gen_a__temperature_1p2_x": [
                    _attack_result("objective text 0", AttackOutcome.FAILURE),
                ],
                "crescendo_simulated__unrelated_x": [_attack_result("objective text 0", AttackOutcome.SUCCESS)],
            },
            display_group_map={
                "crescendo_simulated__gen_a__temperature_0p2_x": "gen_a__temperature_0p2",
                "crescendo_simulated__gen_a__temperature_1p2_x": "gen_a__temperature_1p2",
                "crescendo_simulated__unrelated_x": "adversarial_chat",
            },
        )
        records = await records_from_scenario_result_async(
            result=result, plan=plan, experiment_id="exp", repetition=1, objective_ids=objective_ids
        )
        by_key = {(r.value, r.objective_id): r for r in records}
        assert len(records) == 4
        latest = by_key[(0.2, "behavior_0")]
        assert (latest.outcome, latest.superseded_attempts, latest.executed_turns, latest.backtrack_count) == (
            "success",
            1,
            3,
            2,
        )
        assert by_key[(0.2, "behavior_1")].outcome == "undetermined"
        assert by_key[(1.2, "behavior_0")].outcome == "failure"
        missing = by_key[(1.2, "behavior_1")]
        assert missing.outcome == "error"
        assert "No attack result" in missing.outcome_reason
        assert {r.repetition for r in records} == {1}
        assert {r.technique for r in records} == {"crescendo_simulated"}

    async def test_results_for_objectives_outside_the_plan_are_ignored(self):
        plan = _plan(generators=[_spec("gen_a")], values=[0.2, 1.2])
        result = _scenario_result(
            attack_results={"a": [_attack_result("not pinned", AttackOutcome.SUCCESS)]},
            display_group_map={"a": "gen_a__temperature_0p2"},
        )
        records = await records_from_scenario_result_async(
            result=result,
            plan=plan,
            experiment_id="exp",
            repetition=0,
            objective_ids=objective_ids_by_text(seed_groups=_seed_groups(1)),
        )
        assert {r.outcome for r in records} == {"error"}

    async def test_token_reader_values_are_recorded(self):
        plan = _plan(generators=[_spec("gen_a")], values=[0.2, 1.2])

        async def reader(_: AttackResult) -> dict[str, int | None]:
            return {"adversarial_input_tokens": 10, "adversarial_output_tokens": 4}

        result = _scenario_result(
            attack_results={"a": [_attack_result("objective text 0", AttackOutcome.SUCCESS)]},
            display_group_map={"a": "gen_a__temperature_0p2"},
        )
        records = await records_from_scenario_result_async(
            result=result,
            plan=plan,
            experiment_id="exp",
            repetition=0,
            objective_ids=objective_ids_by_text(seed_groups=_seed_groups(1)),
            token_reader=reader,
        )
        recorded = next(r for r in records if r.value == 0.2)
        assert (recorded.adversarial_input_tokens, recorded.adversarial_output_tokens) == (10, 4)


async def test_memory_token_reader_separates_adversarial_and_objective_usage(sqlite_instance):
    async def store(conversation_id: str, role: str, usage: TokenUsage | None) -> None:
        await sqlite_instance.add_message_pieces_to_memory_async(
            message_pieces=[
                MessagePiece(
                    role=role,  # type: ignore[arg-type]
                    original_value="text",
                    conversation_id=conversation_id,
                    prompt_metadata=usage.to_metadata() if usage else {},
                )
            ]
        )

    await store("objective", "user", None)
    await store("objective", "assistant", TokenUsage(input_tokens=100, output_tokens=20))
    await store("objective", "assistant", TokenUsage(input_tokens=150, output_tokens=30))
    await store("adversarial", "assistant", TokenUsage(input_tokens=40, output_tokens=60))
    await store("adversarial", "assistant", None)
    result = AttackResult(
        conversation_id="objective",
        objective="objective text",
        related_conversations={
            ConversationReference(conversation_id="adversarial", conversation_type=ConversationType.ADVERSARIAL),
            ConversationReference(conversation_id="objective", conversation_type=ConversationType.SCORE),
        },
    )
    usage = await make_memory_token_reader(memory=sqlite_instance)(result)
    assert usage == {
        "objective_input_tokens": 250,
        "objective_output_tokens": 50,
        "adversarial_input_tokens": 40,
        "adversarial_output_tokens": 60,
    }


async def test_memory_token_reader_reports_none_without_usage(sqlite_instance):
    result = AttackResult(conversation_id="empty", objective="objective text")
    usage = await make_memory_token_reader(memory=sqlite_instance)(result)
    assert set(usage.values()) == {None}


class _StubBenchmark:
    """Records how the sweep drives the benchmark and returns a scripted ScenarioResult."""

    instances: list["_StubBenchmark"] = []
    outcomes: dict[str, AttackOutcome] = {}

    def __init__(self, *, objective_scorer: TrueFalseScorer, use_cached: bool) -> None:
        self.objective_scorer = objective_scorer
        self.use_cached = use_cached
        self.params: dict[str, Any] = {}
        self._atomic_attacks: list[Any] = []
        _StubBenchmark.instances.append(self)

    def set_params_from_args(self, *, args: dict[str, Any]) -> None:
        self.params = args

    async def initialize_async(self) -> None:
        pass

    async def run_async(self) -> ScenarioResult:
        groups = await self.params["dataset_config"].get_attack_seed_groups_async()
        names = self.params["adversarial_targets"]
        return _scenario_result(
            attack_results={
                f"crescendo_simulated__{name}_inline": [
                    _attack_result(group.objective.value, _StubBenchmark.outcomes.get(name, AttackOutcome.FAILURE))
                    for group in groups
                ]
                for name in names
            },
            display_group_map={f"crescendo_simulated__{name}_inline": name for name in names},
        )


@pytest.fixture
def stub_benchmark(monkeypatch):
    _StubBenchmark.instances = []
    _StubBenchmark.outcomes = {}
    technique = MagicMock(name="crescendo_simulated")
    monkeypatch.setattr("pyrit.scenario.scenarios.benchmark.sampling_sweep._resolve_technique", lambda name: technique)
    monkeypatch.setattr("pyrit.scenario.scenarios.benchmark.sampling_sweep._default_token_reader", lambda: None)
    TargetRegistry.reset_registry_singleton()
    yield technique
    TargetRegistry.reset_registry_singleton()


def _dataset(count: int = 2) -> DatasetAttackConfiguration:
    return DatasetAttackConfiguration(seed_groups=_seed_groups(count), max_total="all")


@pytest.mark.usefixtures("patch_central_database")
class TestRunSamplingSweep:
    async def test_each_repetition_runs_one_uncached_benchmark_with_fixed_inputs(self, stub_benchmark):
        plan = _plan(repetitions=2)
        objective_target = MockPromptTarget()
        scorer = _scorer()
        dataset = _dataset()
        _StubBenchmark.outcomes = {"gen_a__temperature_0p2": AttackOutcome.SUCCESS}

        records = await run_sampling_sweep_async(
            plan=plan,
            experiment_id="exp",
            objective_target=objective_target,
            objective_scorer=scorer,
            dataset_config=dataset,
            scenario_factory=_StubBenchmark,
        )

        assert len(_StubBenchmark.instances) == 2
        for repetition, scenario in enumerate(_StubBenchmark.instances):
            assert scenario.use_cached is False
            assert scenario.objective_scorer is scorer
            assert scenario.params["use_cached"] is False
            assert scenario.params["objective_target"] is objective_target
            assert scenario.params["dataset_config"] is dataset
            assert scenario.params["scenario_techniques"] == [stub_benchmark]
            assert scenario.params["adversarial_targets"] == [
                variant.registry_name for variant in plan.run_order(repetition=repetition)
            ]
            assert scenario.params["memory_labels"] == {
                SWEEP_EXPERIMENT_LABEL: "exp",
                SWEEP_REPETITION_LABEL: str(repetition),
            }
        assert len(records) == 6 * 2 * 2
        assert {r.outcome for r in records if r.value == 0.2 and r.generator_name == "gen_a"} == {"success"}
        assert TargetRegistry.get_registry_singleton().instances.get_names() == []

    async def test_records_are_written_and_completed_repetitions_resume(self, stub_benchmark, tmp_path):
        output = tmp_path / "sweep.jsonl"
        arguments = {
            "experiment_id": "exp",
            "objective_target": MockPromptTarget(),
            "objective_scorer": _scorer(),
            "dataset_config": _dataset(),
            "output_path": output,
            "scenario_factory": _StubBenchmark,
        }
        await run_sampling_sweep_async(plan=_plan(repetitions=1), **arguments)
        assert len(read_trial_records(path=output)) == 12

        records = await run_sampling_sweep_async(plan=_plan(repetitions=2), **arguments)
        assert len(_StubBenchmark.instances) == 2
        assert len(records) == 24
        assert len(read_trial_records(path=output)) == 24
        assert len({record.trial_id for record in records}) == 24

    async def test_incomplete_repetition_is_rerun_without_duplicate_trials(self, stub_benchmark, tmp_path):
        output = tmp_path / "partially_written.jsonl"
        arguments = {
            "plan": _plan(repetitions=1),
            "experiment_id": "exp",
            "objective_target": MockPromptTarget(),
            "objective_scorer": _scorer(),
            "dataset_config": _dataset(),
            "output_path": output,
            "scenario_factory": _StubBenchmark,
        }
        await run_sampling_sweep_async(**arguments)
        original = read_trial_records(path=output)
        assert len(original) == 12
        write_trial_records(records=original[:5], path=output, append=False)

        resumed = await run_sampling_sweep_async(**arguments)
        persisted = read_trial_records(path=output)
        assert len(_StubBenchmark.instances) == 2
        assert len(resumed) == len(persisted) == 12
        assert len({record.trial_id for record in persisted}) == 12

    async def test_changed_technique_cannot_reuse_existing_results(self, stub_benchmark, tmp_path):
        output = tmp_path / "technique_drift.jsonl"
        common = {
            "experiment_id": "exp",
            "objective_target": MockPromptTarget(),
            "objective_scorer": _scorer(),
            "dataset_config": _dataset(),
            "output_path": output,
            "scenario_factory": _StubBenchmark,
        }
        await run_sampling_sweep_async(plan=_plan(repetitions=1), **common)
        with pytest.raises(ValueError, match="different technique"):
            await run_sampling_sweep_async(plan=_plan(repetitions=1, technique="red_teaming"), **common)
        assert len(_StubBenchmark.instances) == 1

    async def test_changed_objectives_cannot_resume_old_results(self, stub_benchmark, tmp_path):
        output = tmp_path / "changed_objectives.jsonl"
        arguments = {
            "plan": _plan(repetitions=1),
            "experiment_id": "exp",
            "objective_target": MockPromptTarget(),
            "objective_scorer": _scorer(),
            "output_path": output,
            "scenario_factory": _StubBenchmark,
        }
        await run_sampling_sweep_async(dataset_config=_dataset(2), **arguments)
        with pytest.raises(ValueError, match="do not match"):
            await run_sampling_sweep_async(dataset_config=_dataset(3), **arguments)
        assert len(_StubBenchmark.instances) == 1

    async def test_objective_target_change_stops_the_sweep(self, stub_benchmark):
        objective_target = MockPromptTarget()
        identifiers = iter(
            [
                ComponentIdentifier(class_name="Target", class_module="tests.unit", params={"temperature": 0.0}),
                ComponentIdentifier(class_name="Target", class_module="tests.unit", params={"temperature": 1.0}),
            ]
        )
        objective_target.get_identifier = lambda: next(identifiers)  # type: ignore[method-assign]
        with pytest.raises(ValueError, match="objective target configuration changed"):
            await run_sampling_sweep_async(
                plan=_plan(repetitions=1),
                experiment_id="exp",
                objective_target=objective_target,
                objective_scorer=_scorer(),
                dataset_config=_dataset(),
                scenario_factory=_StubBenchmark,
            )

    async def test_unpinned_dataset_selection_stops_the_sweep(self, stub_benchmark):
        dataset = MagicMock(spec=DatasetAttackConfiguration)
        calls: list[int] = []

        async def get_groups(**_: Any) -> list[AttackSeedGroup]:
            # The sweep and the stub benchmark each read the selection once per repetition.
            calls.append(1)
            return _seed_groups(2 if len(calls) <= 2 else 3)

        dataset.get_attack_seed_groups_async = get_groups
        with pytest.raises(ValueError, match="dataset selection changed"):
            await run_sampling_sweep_async(
                plan=_plan(repetitions=2),
                experiment_id="exp",
                objective_target=MockPromptTarget(),
                objective_scorer=_scorer(),
                dataset_config=dataset,
                scenario_factory=_StubBenchmark,
            )

    async def test_scorer_coupled_technique_stops_before_running(self, stub_benchmark, monkeypatch):
        monkeypatch.setattr(
            "pyrit.scenario.scenarios.benchmark.sampling_sweep.find_scorer_coupled_attacks",
            lambda **_: ["tap__gen_a__temperature_0p2_inline"],
        )
        run_async = MagicMock()
        monkeypatch.setattr(_StubBenchmark, "run_async", run_async)
        with pytest.raises(ValueError, match="judge would change"):
            await run_sampling_sweep_async(
                plan=_plan(repetitions=1),
                experiment_id="exp",
                objective_target=MockPromptTarget(),
                objective_scorer=_scorer(),
                dataset_config=_dataset(),
                scenario_factory=_StubBenchmark,
            )
        run_async.assert_not_called()
        assert TargetRegistry.get_registry_singleton().instances.get_names() == []


@pytest.fixture
def benchmark_registry():
    """Populate the real technique catalog without environment variables, as the benchmark tests do."""
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_benchmark_technique.cache_clear()
    _get_benchmark_adversarial_guidance.cache_clear()
    placeholder = MagicMock(spec=PromptTarget)
    placeholder.capabilities.includes.return_value = True
    TargetRegistry.get_registry_singleton().instances.register(placeholder, name="adversarial_chat")
    AttackTechniqueRegistry.get_registry_singleton().register_from_factories(build_technique_factories())
    yield
    AttackTechniqueRegistry.reset_registry_singleton()
    TargetRegistry.reset_registry_singleton()
    _build_benchmark_technique.cache_clear()
    _get_benchmark_adversarial_guidance.cache_clear()


@pytest.mark.usefixtures("patch_central_database", "benchmark_registry")
class TestAdversarialBenchmarkIntegration:
    """Build real AdversarialBenchmark atomic attacks from sweep variants; nothing is sent to a model."""

    async def _initialize(self, *, technique: str, plan: SamplingSweepPlan, objective_target: PromptTarget):
        from pyrit.scenario.scenarios.benchmark import AdversarialBenchmarkTechnique

        targets = build_variant_targets(plan=plan)
        with registered_variants(targets=targets) as names:
            scenario = AdversarialBenchmark(objective_scorer=_scorer(), use_cached=False)
            scenario.set_params_from_args(
                args={
                    "objective_target": objective_target,
                    "adversarial_targets": names,
                    "scenario_techniques": [AdversarialBenchmarkTechnique(technique)],
                    "dataset_config": _dataset(1),
                    "use_cached": False,
                }
            )
            await scenario.initialize_async()
        return scenario, targets

    async def test_each_variant_becomes_its_own_display_group(self):
        plan = _plan(generators=[_spec("gen_a")])
        scenario, targets = await self._initialize(
            technique="crescendo_simulated", plan=plan, objective_target=MockPromptTarget()
        )
        groups = {attack.display_group for attack in scenario._atomic_attacks}
        assert groups == {variant.registry_name for variant in targets}
        assert len({attack.atomic_attack_name for attack in scenario._atomic_attacks}) == len(targets)

    async def test_red_teaming_uses_each_variant_temperature_and_the_fixed_objective_target(self):
        plan = _plan(generators=[_spec("gen_a")])
        objective_target = MockPromptTarget()
        scenario, targets = await self._initialize(
            technique="red_teaming", plan=plan, objective_target=objective_target
        )
        by_group = {variant.registry_name: target for variant, target in targets.items()}
        for attack in scenario._atomic_attacks:
            strategy = attack.attack_technique.attack
            assert strategy.get_objective_target() is objective_target
            assert strategy._adversarial_chat is by_group[attack.display_group]
        hashes = {attack.technique_eval_hash for attack in scenario._atomic_attacks}
        assert len(hashes) == len(targets)

    async def test_tap_scorer_coupling_is_detected(self):
        plan = _plan(generators=[_spec("gen_a")])
        scenario, targets = await self._initialize(technique="tap", plan=plan, objective_target=MockPromptTarget())
        coupled = find_scorer_coupled_attacks(atomic_attacks=scenario._atomic_attacks, targets=targets)
        assert len(coupled) == len(targets)

    async def test_red_teaming_scorer_is_not_coupled(self):
        plan = _plan(generators=[_spec("gen_a")])
        scenario, targets = await self._initialize(
            technique="red_teaming", plan=plan, objective_target=MockPromptTarget()
        )
        assert find_scorer_coupled_attacks(atomic_attacks=scenario._atomic_attacks, targets=targets) == []
