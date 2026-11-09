# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Exact dataset example selection: fingerprint, resolver, estimate, and launch persistence."""

import json
import threading
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from pyrit.backend.services import ScenarioRunService
from pyrit.backend.services.dataset_example_fingerprint import dataset_example_fingerprint
from pyrit.backend.services.dataset_example_selection import (
    resolve_dataset_example_selection_async,
    scenario_supports_exact_example_selection,
)
from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.backend.services.scenario_run_service import (
    _LAUNCH_REQUEST_FIELDS,
    _LAUNCH_REQUEST_METADATA_KEY,
    _OPTIONAL_LAUNCH_FIELDS,
)
from pyrit.datasets import SeedDatasetProvider
from pyrit.memory import MemoryInterface, SeedEntry
from pyrit.models import (
    AnswerMatches,
    DatasetExampleReference,
    DatasetExampleSelection,
    DatasetExampleSelectionError,
    DatasetSourceInput,
    ScenarioDatasetRequest,
    ScenarioRunSizeEstimateRequest,
    Seed,
    SeedObjective,
    SeedPrompt,
    SeedSimulatedConversation,
)
from pyrit.models.catalog import RunScenarioRequest
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.registry import ScenarioRegistry, ScorerRegistry
from pyrit.scenario import (
    AtomicAttack,
    BaselineAttackPolicy,
    DatasetAttackConfiguration,
    ExactExampleDatasetConfiguration,
    Scenario,
    ScenarioTechnique,
)
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.scenarios.garak import ApiKey, Exploitation, SystemPromptExtraction
from pyrit.score import SubStringScorer
from unit.mocks import MockPromptTarget

DATASET = "exact-selection"
OTHER = "exact-other"
NAMED = f"dataset:named:{DATASET}"
OTHER_KEY = f"dataset:named:{OTHER}"
UNNAMED = "dataset:unnamed"


class _Technique(ScenarioTechnique):
    ALL = ("all", {"all"})
    DIRECT = ("direct", set())

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        return {"all"}


class _ExactScenario(Scenario):
    """One concrete technique and the default baseline, so the estimate is 2N."""

    def __init__(self) -> None:
        super().__init__(
            version=1,
            technique_class=_Technique,
            objective_scorer=SubStringScorer(substring="yes"),
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["unused-default"]),
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        return []


class _NoDatasetScenario(Scenario):
    @classmethod
    def supported_parameters(cls):
        return [parameter for parameter in super().supported_parameters() if parameter.name != "dataset_config"]

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        return []


async def _store(memory: MemoryInterface, *seeds: Seed[Any]) -> None:
    async with await memory.get_session_async() as session:
        session.add_all(SeedEntry(entry=seed) for seed in seeds)
        await session.commit()


async def _mutate(memory: MemoryInterface, seed_id: UUID, **fields: object) -> None:
    async with await memory.get_session_async() as session:
        entry = await session.get(SeedEntry, seed_id)
        assert entry is not None
        for name, value in fields.items():
            setattr(entry, name, value)
        await session.commit()


async def _count_seeds(memory: MemoryInterface) -> int:
    from sqlalchemy import func, select

    async with await memory.get_session_async() as session:
        count = await session.run_sync(lambda sync: sync.scalar(select(func.count()).select_from(SeedEntry)))
    return int(count or 0)


def _objective(value: str, **kwargs: object) -> SeedObjective:
    kwargs.setdefault("dataset_name", DATASET)
    kwargs.setdefault("added_by", "test")
    return SeedObjective(value=value, **kwargs)


def _prompt(value: str, **kwargs: object) -> SeedPrompt:
    kwargs.setdefault("dataset_name", DATASET)
    kwargs.setdefault("added_by", "test")
    return SeedPrompt(value=value, **kwargs)


async def _selection(
    memory: MemoryInterface, *, selection_key: str, example_ids: Sequence[UUID], dataset_name: str | None
) -> DatasetExampleSelection:
    found = await memory.get_seed_examples_by_ids_async(dataset_name=dataset_name, example_ids=example_ids)
    return DatasetExampleSelection(
        examples=[
            DatasetExampleReference(
                selection_key=selection_key,
                example_id=example_id,
                fingerprint=dataset_example_fingerprint(
                    selection_key=selection_key, example_id=example_id, members=found[example_id]
                ),
            )
            for example_id in example_ids
        ]
    )


def _reference(example_id: UUID | None = None, *, selection_key: str = NAMED) -> DatasetExampleReference:
    return DatasetExampleReference(selection_key=selection_key, example_id=example_id or uuid4(), fingerprint="ab" * 32)


@pytest.mark.usefixtures("patch_central_database")
async def test_batch_read_matches_single_example_reads(sqlite_instance: MemoryInterface) -> None:
    group = uuid4()
    seeds = [
        _objective("goal", prompt_group_id=group),
        _prompt("first", prompt_group_id=group, sequence=0, role="user"),
        _prompt("second", prompt_group_id=group, sequence=1, role="assistant"),
        _objective("alone"),
    ]
    await _store(sqlite_instance, *seeds)
    example_ids = [group, seeds[3].id]
    batched = await sqlite_instance.get_seed_examples_by_ids_async(dataset_name=DATASET, example_ids=example_ids)
    singles = [
        await sqlite_instance.get_seed_example_async(dataset_name=DATASET, example_id=example_id)
        for example_id in example_ids
    ]
    assert [batched[example_id] for example_id in example_ids] == singles
    assert [member.seed_type for member in batched[group]] == ["objective", "prompt", "prompt"]
    assert [member.value for member in batched[group]] == ["goal", "first", "second"]


@pytest.mark.usefixtures("patch_central_database")
async def test_resolver_keeps_request_order_members_and_provenance(sqlite_instance: MemoryInterface) -> None:
    examples = []
    for index in range(6):
        group = uuid4()
        examples.append(
            (
                group,
                _objective(
                    f"goal {index}",
                    prompt_group_id=group,
                    harm_categories=["violence"],
                    source="catalog",
                    conditions=[AnswerMatches(correct_answer="yes")],
                ),
                _prompt(
                    f"prompt {index} {{{{name}}}}",
                    prompt_group_id=group,
                    sequence=0,
                    role="user",
                    value_sha256="a" * 64,
                ),
                _prompt(
                    "https://cdn.example.test/clip.png",
                    prompt_group_id=group,
                    sequence=1,
                    role="user",
                    data_type="image_path",
                ),
            )
        )
    await _store(sqlite_instance, *[seed for example in examples for seed in example[1:]])
    requested = list(reversed([example[0] for example in examples]))
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=requested, dataset_name=DATASET)

    resolved = await resolve_dataset_example_selection_async(selection=selection, memory=sqlite_instance)

    assert [item.reference.example_id for item in resolved] == requested
    assert len(resolved) == 6
    members = resolved[0].members
    assert [member.seed_type for member in members] == ["objective", "prompt", "prompt"]
    assert members[0].conditions is not None
    assert members[0].conditions[0].condition_type == "answer_matches"
    assert members[0].harm_categories == ["violence"]
    assert members[0].source == "catalog"
    assert members[1].value.endswith("{{name}}")
    assert members[1].value_sha256 == "a" * 64
    assert members[1].role == "user"
    assert members[1].sequence == 0
    assert members[2].data_type == "image_path"
    assert members[2].value == "https://cdn.example.test/clip.png"


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("value", "SECRET_SEED_TEXT_2757"),
        ("value_sha256", "b" * 64),
        ("role", "assistant"),
        ("sequence", 7),
        ("source", "rewritten-provenance"),
    ],
)
async def test_content_changes_are_stale_and_not_rewritten(
    sqlite_instance: MemoryInterface, field: str, value: object
) -> None:
    prompt = _prompt("original", role="user", sequence=1, value_sha256="a" * 64, source="catalog")
    objective = _objective(
        "goal",
        prompt_group_id=prompt.id,
        conditions=[AnswerMatches(correct_answer="yes")],
    )
    prompt.prompt_group_id = prompt.id
    await _store(sqlite_instance, objective, prompt)
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[prompt.id], dataset_name=DATASET)
    before = await _count_seeds(sqlite_instance)
    await _mutate(sqlite_instance, prompt.id, **{field: value})
    original_fingerprint = selection.examples[0].fingerprint
    reread = await sqlite_instance.get_seed_example_async(dataset_name=DATASET, example_id=prompt.id)
    assert (
        dataset_example_fingerprint(selection_key=NAMED, example_id=prompt.id, members=reread) != original_fingerprint
    )

    with pytest.raises(DatasetExampleSelectionError, match="stale") as caught:
        await resolve_dataset_example_selection_async(selection=selection, memory=sqlite_instance)

    assert caught.value.code == "stale"
    assert caught.value.example_id == prompt.id
    assert "SECRET_SEED_TEXT_2757" not in str(caught.value)
    assert await _count_seeds(sqlite_instance) == before
    stored = await sqlite_instance.get_seed_example_async(dataset_name=DATASET, example_id=prompt.id)
    changed = next(member for member in stored if member.id == prompt.id)
    assert getattr(changed, field) == value


@pytest.mark.usefixtures("patch_central_database")
async def test_membership_and_condition_changes_are_stale(sqlite_instance: MemoryInterface) -> None:
    group = uuid4()
    objective = _objective("goal", prompt_group_id=group, conditions=[AnswerMatches(correct_answer="yes")])
    prompt = _prompt("body", prompt_group_id=group, sequence=1, role="user")
    await _store(sqlite_instance, objective, prompt)
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[group], dataset_name=DATASET)

    extra = _prompt("added", prompt_group_id=group, sequence=2, role="user")
    await _store(sqlite_instance, extra)
    with pytest.raises(DatasetExampleSelectionError, match="stale"):
        await resolve_dataset_example_selection_async(selection=selection, memory=sqlite_instance)

    await _mutate(sqlite_instance, extra.id, prompt_group_id=None, dataset_name="somewhere-else")
    current = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[group], dataset_name=DATASET)
    await _mutate(
        sqlite_instance,
        objective.id,
        conditions=[AnswerMatches(correct_answer="no").model_dump(mode="json")],
    )
    with pytest.raises(DatasetExampleSelectionError, match="stale"):
        await resolve_dataset_example_selection_async(selection=current, memory=sqlite_instance)

    refreshed = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[group], dataset_name=DATASET)
    async with await sqlite_instance.get_session_async() as session:
        entry = await session.get(SeedEntry, prompt.id)
        assert entry is not None
        await session.delete(entry)
        await session.commit()
    with pytest.raises(DatasetExampleSelectionError, match="stale"):
        await resolve_dataset_example_selection_async(selection=refreshed, memory=sqlite_instance)


@pytest.mark.usefixtures("patch_central_database")
async def test_same_group_id_stays_inside_each_dataset(sqlite_instance: MemoryInterface) -> None:
    group = uuid4()
    await _store(
        sqlite_instance,
        _objective("alpha", prompt_group_id=group, dataset_name=DATASET),
        _prompt("alpha prompt", prompt_group_id=group, dataset_name=DATASET, sequence=1, role="user"),
        SeedObjective(value="beta", dataset_name=OTHER, prompt_group_id=group, added_by="test"),
        SeedPrompt(
            value="beta prompt", dataset_name=OTHER, prompt_group_id=group, sequence=1, role="user", added_by="test"
        ),
    )
    alpha = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[group], dataset_name=DATASET)
    beta = await _selection(sqlite_instance, selection_key=OTHER_KEY, example_ids=[group], dataset_name=OTHER)

    resolved_alpha = await resolve_dataset_example_selection_async(selection=alpha, memory=sqlite_instance)
    resolved_beta = await resolve_dataset_example_selection_async(selection=beta, memory=sqlite_instance)

    assert {member.value for member in resolved_alpha[0].members} == {"alpha", "alpha prompt"}
    assert {member.value for member in resolved_beta[0].members} == {"beta", "beta prompt"}
    assert alpha.examples[0].fingerprint != beta.examples[0].fingerprint
    combined = DatasetExampleSelection(examples=[alpha.examples[0], beta.examples[0]])
    both = await resolve_dataset_example_selection_async(selection=combined, memory=sqlite_instance)
    assert [item.reference.selection_key for item in both] == [NAMED, OTHER_KEY]


@pytest.mark.usefixtures("patch_central_database")
async def test_duplicate_missing_and_invalid_key(sqlite_instance: MemoryInterface) -> None:
    seed = _objective("only")
    await _store(sqlite_instance, seed)
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[seed.id], dataset_name=DATASET)
    duplicate = DatasetExampleSelection(examples=[selection.examples[0], selection.examples[0]])
    with pytest.raises(DatasetExampleSelectionError, match="duplicate") as duplicate_error:
        await resolve_dataset_example_selection_async(selection=duplicate, memory=sqlite_instance)
    assert duplicate_error.value.code == "duplicate"

    missing = DatasetExampleSelection(examples=[_reference()])
    with pytest.raises(DatasetExampleSelectionError, match="missing") as missing_error:
        await resolve_dataset_example_selection_async(selection=missing, memory=sqlite_instance)
    assert missing_error.value.code == "missing"

    for key in ("nope", "dataset:named:", "dataset:other"):
        invalid = DatasetExampleSelection(examples=[_reference(selection_key=key)])
        with pytest.raises(DatasetExampleSelectionError, match="invalid_key") as invalid_error:
            await resolve_dataset_example_selection_async(selection=invalid, memory=sqlite_instance)
        assert invalid_error.value.code == "invalid_key"


@pytest.mark.usefixtures("patch_central_database")
async def test_unnamed_selection_does_not_include_named_rows(sqlite_instance: MemoryInterface) -> None:
    named = _objective("named")
    unnamed = SeedObjective(value="unnamed", dataset_name=None, added_by="test")
    await _store(sqlite_instance, named, unnamed)
    selection = await _selection(sqlite_instance, selection_key=UNNAMED, example_ids=[unnamed.id], dataset_name=None)
    resolved = await resolve_dataset_example_selection_async(selection=selection, memory=sqlite_instance)
    assert [member.value for member in resolved[0].members] == ["unnamed"]


def test_more_than_max_items_is_rejected_by_the_model() -> None:
    examples = [_reference() for _ in range(101)]
    with pytest.raises(ValidationError):
        DatasetExampleSelection(examples=examples)
    with pytest.raises(ValidationError):
        DatasetExampleSelection(examples=[])
    with pytest.raises(ValidationError):
        DatasetExampleReference(selection_key=NAMED, example_id=uuid4(), fingerprint="AB" * 32)
    with pytest.raises(ValidationError):
        DatasetExampleReference(selection_key=NAMED, example_id=uuid4(), fingerprint="abc", extra="no")


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "datasets",
    [
        ScenarioDatasetRequest(sources=[DatasetSourceInput(name="harmbench")]),
        ScenarioDatasetRequest(filters={"harm_categories": ["violence"]}),
        ScenarioDatasetRequest(max_total=2),
        ScenarioDatasetRequest(max_per_dataset=2),
        ScenarioDatasetRequest(fetch="never"),
    ],
)
async def test_exact_selection_rejects_population_overrides(datasets: ScenarioDatasetRequest) -> None:
    selection = DatasetExampleSelection(examples=[_reference()])
    with pytest.raises(DatasetExampleSelectionError, match="conflicting_mode") as caught:
        await ScenarioConfigurationResolver.resolve_configuration_async(
            scenario_name="test.exact",
            scenario_class=_ExactScenario,
            datasets=datasets,
            example_selection=selection,
        )
    assert caught.value.code == "conflicting_mode"


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "fields",
    [
        {"dataset_names": ["harmbench"]},
        {"max_dataset_size": 3},
        {"dataset_filters": {"data_types": ["text"]}},
    ],
)
async def test_exact_selection_rejects_deprecated_dataset_fields(fields: dict[str, object]) -> None:
    request = ScenarioRunSizeEstimateRequest(
        example_selection=DatasetExampleSelection(examples=[_reference()]), **fields
    )
    with pytest.raises(DatasetExampleSelectionError, match="conflicting_mode"):
        await ScenarioConfigurationResolver.resolve_configuration_async(
            scenario_name="test.exact",
            scenario_class=_ExactScenario,
            datasets=request.dataset_request,
            example_selection=request.example_selection,
        )


@pytest.mark.usefixtures("patch_central_database")
async def test_load_route_rejects_exact_selection_before_memory(sqlite_instance: MemoryInterface) -> None:
    selection = DatasetExampleSelection(examples=[_reference()])
    with (
        patch.object(sqlite_instance, "get_seed_examples_by_ids_async", new_callable=AsyncMock) as read,
        pytest.raises(DatasetExampleSelectionError, match="conflicting_mode"),
    ):
        await ScenarioConfigurationResolver.resolve_configuration_async(
            scenario_name="test.exact",
            scenario_class=_ExactScenario,
            example_selection=selection,
            memory=sqlite_instance,
            reject_exact_selection=True,
        )
    read.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("scenario_class", [SystemPromptExtraction, Exploitation])
async def test_named_only_scenarios_reject_exact_selection_before_resolution(scenario_class: type[Scenario]) -> None:
    assert scenario_supports_exact_example_selection(scenario_class) is False
    with (
        patch(
            "pyrit.backend.services.scenario_configuration_resolver.resolve_dataset_example_selection_async",
            new_callable=AsyncMock,
        ) as read,
        pytest.raises(DatasetExampleSelectionError, match="unsupported") as caught,
    ):
        await ScenarioConfigurationResolver.resolve_configuration_async(
            scenario_name="test.exact",
            scenario_class=scenario_class,
            example_selection=DatasetExampleSelection(examples=[_reference()]),
        )
    assert caught.value.code == "unsupported"
    read.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
async def test_scenario_without_dataset_config_is_unsupported() -> None:
    with pytest.raises(DatasetExampleSelectionError, match="unsupported") as caught:
        await ScenarioConfigurationResolver.resolve_configuration_async(
            scenario_name="test.exact",
            scenario_class=_NoDatasetScenario,
            example_selection=DatasetExampleSelection(examples=[_reference()]),
        )
    assert caught.value.code == "unsupported"


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("objective_count", [0, 2])
async def test_objective_count_does_not_reject_the_selection(
    sqlite_instance: MemoryInterface, objective_count: int
) -> None:
    group = uuid4()
    seeds: list[Seed[Any]] = [_objective(f"goal {index}", prompt_group_id=group) for index in range(objective_count)]
    seeds.append(_prompt("body", prompt_group_id=group, sequence=1, role="user"))
    await _store(sqlite_instance, *seeds)
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[group], dataset_name=DATASET)
    resolved_config = await ScenarioConfigurationResolver.resolve_configuration_async(
        scenario_name="test.exact",
        scenario_class=_ExactScenario,
        example_selection=selection,
        memory=sqlite_instance,
    )
    members = resolved_config["dataset_config"]._selected_examples[0][1]
    assert sum(member.seed_type == "objective" for member in members) == objective_count
    assert any(member.value == "body" for member in members)
    scenario = _ExactScenario()
    scenario.set_params_from_args(args=resolved_config)
    with patch.object(
        ExactExampleDatasetConfiguration,
        "get_attack_seed_groups_async",
        new_callable=AsyncMock,
        side_effect=AssertionError("groups"),
    ):
        estimate = await scenario.get_run_size_estimate_async()
    assert estimate.dataset_size.model_dump() == {"kind": "bounded", "value": 1}


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("objective_count", [0, 2])
async def test_launch_build_reports_unsupported_without_hiding_the_example(
    sqlite_instance: MemoryInterface, objective_count: int
) -> None:
    group = uuid4()
    secret = f"SECRET_PROMPT_{objective_count}_2757"
    seeds: list[Seed[Any]] = [_objective(f"goal {index}", prompt_group_id=group) for index in range(objective_count)]
    seeds.append(_prompt(secret, prompt_group_id=group, sequence=1, role="user"))
    await _store(sqlite_instance, *seeds)
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[group], dataset_name=DATASET)
    resolved_config = await ScenarioConfigurationResolver.resolve_configuration_async(
        scenario_name="test.exact",
        scenario_class=_ExactScenario,
        example_selection=selection,
        memory=sqlite_instance,
    )
    scenario = _ExactScenario()
    scenario.set_params_from_args(args=resolved_config)
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.dataset_size.model_dump() == {"kind": "bounded", "value": 1}

    with pytest.raises(DatasetExampleSelectionError, match="unsupported") as caught:
        await scenario._dataset_config.get_attack_seed_groups_async()

    error = caught.value
    assert error.code == "unsupported"
    assert error.example_id == group
    assert error.selection_key == NAMED
    assert str(group) in str(error)
    assert "objective" in str(error)
    assert secret not in str(error)
    assert "input_value" not in str(error)


@pytest.mark.usefixtures("patch_central_database")
async def test_launch_build_reports_unsupported_for_multiple_simulated_conversations(
    sqlite_instance: MemoryInterface,
) -> None:
    group = uuid4()
    secret = "SECRET_SIMULATED_2757"
    objective = _objective("goal", prompt_group_id=group)
    conversations = [
        SeedSimulatedConversation(
            num_turns=1,
            adversarial_chat_system_prompt=SeedPrompt(value=secret, parameters=["objective"]),
            simulated_target_system_prompt=SeedPrompt(value=f"target {index}", parameters=["objective", "num_turns"]),
            dataset_name=DATASET,
            prompt_group_id=group,
            added_by="test",
        )
        for index in range(2)
    ]
    await _store(sqlite_instance, objective, *conversations)
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[group], dataset_name=DATASET)
    resolved_config = await ScenarioConfigurationResolver.resolve_configuration_async(
        scenario_name="test.exact",
        scenario_class=_ExactScenario,
        example_selection=selection,
        memory=sqlite_instance,
    )
    scenario = _ExactScenario()
    scenario.set_params_from_args(args=resolved_config)
    estimate = await scenario.get_run_size_estimate_async()
    stored_members = scenario._dataset_config._selected_examples[0][1]
    assert estimate.dataset_size.model_dump() == {"kind": "bounded", "value": 1}
    assert sum(member.seed_type == "simulated_conversation" for member in stored_members) == 2

    with pytest.raises(DatasetExampleSelectionError, match="unsupported") as caught:
        await scenario._dataset_config.get_attack_seed_groups_async()

    error = caught.value
    assert error.code == "unsupported"
    assert error.example_id == group
    assert "simulated conversation" in str(error)
    assert secret not in str(error)


@pytest.mark.usefixtures("patch_central_database")
async def test_text_prompt_is_supported_and_not_rendered(sqlite_instance: MemoryInterface) -> None:
    group = uuid4()
    objective = _objective("goal", prompt_group_id=group)
    prompt = _prompt("Hello {{name}}", prompt_group_id=group, sequence=1, role="user")
    await _store(sqlite_instance, objective, prompt)
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[group], dataset_name=DATASET)
    resolved = await resolve_dataset_example_selection_async(selection=selection, memory=sqlite_instance)
    assert resolved[0].members[1].value == "Hello {{name}}"
    config = ExactExampleDatasetConfiguration(examples=[(resolved[0].reference, resolved[0].members)])
    groups = await config.get_attack_seed_groups_async(apply_sampling=True)
    assert len(groups) == 1
    assert groups[0].prompts[0].value == "Hello {{name}}"


@pytest.mark.usefixtures("patch_central_database")
async def test_estimate_counts_six_without_providers_writes_or_seed_rebuild(
    sqlite_instance: MemoryInterface,
) -> None:
    seeds = [_objective(f"goal {index}") for index in range(6)]
    await _store(sqlite_instance, *seeds)
    selection = await _selection(
        sqlite_instance, selection_key=NAMED, example_ids=[seed.id for seed in seeds], dataset_name=DATASET
    )
    resolved = await ScenarioConfigurationResolver.resolve_configuration_async(
        scenario_name="test.exact",
        scenario_class=_ExactScenario,
        example_selection=selection,
        memory=sqlite_instance,
    )
    scenario = _ExactScenario()
    scenario.set_params_from_args(args=resolved)
    with (
        patch.object(DatasetAttackConfiguration, "prepare_async", new_callable=AsyncMock) as prepare,
        patch.object(SeedDatasetProvider, "fetch_dataset_async", new_callable=AsyncMock) as fetch_one,
        patch.object(SeedDatasetProvider, "fetch_datasets_async", new_callable=AsyncMock) as fetch_many,
        patch.object(MemoryInterface, "add_seeds_to_memory_async", new_callable=AsyncMock) as add_seeds,
        patch.object(SeedEntry, "get_seed", side_effect=AssertionError("get_seed")),
        patch.object(
            ExactExampleDatasetConfiguration,
            "get_attack_seed_groups_async",
            new_callable=AsyncMock,
            side_effect=AssertionError("groups"),
        ),
    ):
        estimate = await scenario.get_run_size_estimate_async()
    prepare.assert_not_called()
    fetch_one.assert_not_called()
    fetch_many.assert_not_called()
    add_seeds.assert_not_called()
    assert estimate.dataset_size.model_dump() == {"kind": "bounded", "value": 6}
    assert estimate.effective_datasets.max_total == "all"
    assert estimate.total_attack_count == 12
    assert estimate.status.value != "unavailable"
    assert "resolved example count" in (estimate.note or "")
    assert "Smaller datasets" not in (estimate.note or "")
    assert scenario.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Enabled


@pytest.mark.usefixtures("patch_central_database")
async def test_omitted_selection_keeps_named_dataset_defaults() -> None:
    datasets = ScenarioDatasetRequest(sources=[DatasetSourceInput(name="harmbench")])
    resolved = await ScenarioConfigurationResolver.resolve_configuration_async(
        scenario_name="garak.api_key",
        scenario_class=ApiKey,
        datasets=datasets,
    )
    assert resolved["dataset_overrides"] is datasets
    assert "dataset_config" not in resolved
    assert DatasetAttackConfiguration(dataset_names=["harmbench"]).max_per_dataset == 5
    dumped = RunScenarioRequest(scenario_name="s", target_name="t", dataset_names=["harmbench"]).model_dump(
        mode="json", include=set(_LAUNCH_REQUEST_FIELDS)
    )
    dumped.pop("example_selection", None)
    assert "example_selection" not in dumped
    assert dumped["dataset_names"] == ["harmbench"]


@pytest.mark.usefixtures("patch_central_database")
async def test_exact_selection_keeps_scorer_staleness_check(sqlite_instance: MemoryInterface) -> None:
    seed = _objective("goal")
    await _store(sqlite_instance, seed)
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[seed.id], dataset_name=DATASET)
    with patch.object(ScorerRegistry, "get_registry_singleton", return_value=ScorerRegistry()):
        scorer = SubStringScorer(substring="expected")
        registry = ScorerRegistry.get_registry_singleton()
        registry.instances.register(scorer, name="custom")
        reference = RegisteredComponentReference(registry_name="custom", identifier_hash=scorer.get_identifier().hash)
        resolved = await ScenarioConfigurationResolver.resolve_configuration_async(
            scenario_name="test.exact",
            scenario_class=_ExactScenario,
            example_selection=selection,
            memory=sqlite_instance,
        )
        assert "objective_scorer" not in resolved
        assert (
            ScenarioConfigurationResolver.resolve_constructor_kwargs(scenario_class=ApiKey, objective_scorer=None) == {}
        )
        assert ScenarioConfigurationResolver.resolve_constructor_kwargs(
            scenario_class=ApiKey, objective_scorer=reference
        ) == {"objective_scorer": scorer}
        registry.instances.register(SubStringScorer(substring="changed"), name="custom", replace=True)
        with pytest.raises(ValueError, match="has changed"):
            ScenarioConfigurationResolver.resolve_constructor_kwargs(scenario_class=ApiKey, objective_scorer=reference)


@pytest.mark.usefixtures("patch_central_database")
async def test_launch_request_round_trip_keeps_selection_and_resume_goes_stale(
    sqlite_instance: MemoryInterface,
) -> None:
    seed = _objective("goal")
    await _store(sqlite_instance, seed)
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[seed.id], dataset_name=DATASET)
    assert "example_selection" in _LAUNCH_REQUEST_FIELDS
    assert "example_selection" in _OPTIONAL_LAUNCH_FIELDS
    registry = MagicMock(spec=ScenarioRegistry)
    registry.get_class.return_value = _ExactScenario
    registry.create_and_initialize_async = AsyncMock(return_value=MagicMock(spec=Scenario))
    service = ScenarioRunService()
    request_model = RunScenarioRequest(
        scenario_name="test.exact",
        target_name="target",
        example_selection=selection,
    )
    with (
        patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
        patch.object(service, "_run_initializers_async", new_callable=AsyncMock),
        patch.object(service._configuration_resolver, "resolve_target", return_value=MockPromptTarget()),
    ):
        await service._prepare_run_async(request=request_model)
        saved = registry.create_and_initialize_async.call_args.kwargs["initial_metadata"][_LAUNCH_REQUEST_METADATA_KEY]
        assert saved["example_selection"]["examples"][0]["fingerprint"] == selection.examples[0].fingerprint
        stored = MagicMock()
        stored.id = uuid4()
        stored.metadata = {_LAUNCH_REQUEST_METADATA_KEY: saved}
        stored.scenario_identifier = MagicMock(techniques=["direct"], datasets=[], params={})
        stored.labels = {}
        stored.operation_id = None
        restored = service._restore_launch_request(stored=stored)
    assert restored.example_selection == selection
    await _mutate(sqlite_instance, seed.id, value="SECRET_REPLACEMENT_2757")
    with pytest.raises(DatasetExampleSelectionError, match="stale") as caught:
        await ScenarioConfigurationResolver.resolve_configuration_async(
            scenario_name="test.exact",
            scenario_class=_ExactScenario,
            example_selection=restored.example_selection,
            memory=sqlite_instance,
        )
    assert caught.value.code == "stale"
    assert "SECRET_REPLACEMENT_2757" not in str(caught.value)


@pytest.mark.usefixtures("patch_central_database")
async def test_sampling_stays_off_and_legacy_files_wait_until_launch(sqlite_instance: MemoryInterface) -> None:
    group = uuid4()
    objective = _objective("goal", prompt_group_id=group)
    conversation = SeedSimulatedConversation(
        num_turns=1,
        adversarial_chat_system_prompt=SeedPrompt(value="adversary", parameters=["objective"]),
        simulated_target_system_prompt=SeedPrompt(value="target", parameters=["objective", "num_turns"]),
        dataset_name=DATASET,
        prompt_group_id=group,
        added_by="test",
    )
    await _store(sqlite_instance, objective, conversation)
    await _mutate(
        sqlite_instance,
        conversation.id,
        value=json.dumps(
            {
                "num_turns": 1,
                "adversarial_chat_system_prompt_path": "/no/such/pyrit-2757-template.yaml",
            }
        ),
    )
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[group], dataset_name=DATASET)
    resolved = await ScenarioConfigurationResolver.resolve_configuration_async(
        scenario_name="test.exact",
        scenario_class=_ExactScenario,
        example_selection=selection,
        memory=sqlite_instance,
    )
    scenario = _ExactScenario()
    scenario.set_params_from_args(args=resolved)
    with patch.object(SeedEntry, "get_seed", side_effect=AssertionError("get_seed")):
        estimate = await scenario.get_run_size_estimate_async()
    assert estimate.dataset_size.model_dump()["value"] == 1
    config = scenario._dataset_config
    assert isinstance(config, ExactExampleDatasetConfiguration)
    with pytest.raises(DatasetExampleSelectionError, match="unsupported"):
        await config.get_attack_seed_groups_async(apply_sampling=True)


@pytest.mark.usefixtures("patch_central_database")
async def test_resume_resolves_the_saved_selection_and_rejects_a_different_one(
    sqlite_instance: MemoryInterface,
) -> None:
    group = uuid4()
    objective = _objective("goal", prompt_group_id=group)
    prompt = _prompt("body", prompt_group_id=group, sequence=1, role="user")
    await _store(sqlite_instance, objective, prompt)
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[group], dataset_name=DATASET)
    other = selection.model_copy(
        update={
            "examples": [
                selection.examples[0].model_copy(update={"fingerprint": "cd" * 32}),
            ]
        }
    )
    result_id = str(uuid4())
    saved = RunScenarioRequest(
        scenario_name="test.exact",
        target_name="target",
        example_selection=selection,
        include_baseline=True,
        max_concurrency=1,
        max_retries=0,
    ).model_dump(mode="json", include=set(_LAUNCH_REQUEST_FIELDS))
    stored = MagicMock()
    stored.id = result_id
    stored.metadata = {_LAUNCH_REQUEST_METADATA_KEY: saved}
    stored.scenario_identifier = MagicMock(techniques=["direct"], datasets=[], params={})
    stored.labels = {}
    stored.operation_id = None
    registry = MagicMock(spec=ScenarioRegistry)
    registry.get_class.return_value = _ExactScenario
    service = ScenarioRunService()
    with (
        patch.object(ScenarioRegistry, "get_registry_singleton", return_value=registry),
        patch.object(service._memory, "get_scenario_result_header_async", new=AsyncMock(return_value=stored)),
    ):
        with pytest.raises(DatasetExampleSelectionError, match="conflicting_mode") as caught:
            await service._example_selection_for_run_async(
                request=RunScenarioRequest(
                    scenario_name="test.exact",
                    target_name="target",
                    scenario_result_id=result_id,
                    example_selection=other,
                )
            )
        assert caught.value.code == "conflicting_mode"
        resumed = await service._example_selection_for_run_async(
            request=RunScenarioRequest(scenario_name="test.exact", target_name="target", scenario_result_id=result_id)
        )
    assert resumed == selection

    await _mutate(sqlite_instance, prompt.id, role="assistant")
    with pytest.raises(DatasetExampleSelectionError, match="stale") as stale:
        await ScenarioConfigurationResolver.resolve_configuration_async(
            scenario_name="test.exact",
            scenario_class=_ExactScenario,
            example_selection=resumed,
            memory=sqlite_instance,
        )
    assert stale.value.code == "stale"
    assert stale.value.example_id == group


@pytest.mark.usefixtures("patch_central_database")
async def test_resume_without_a_saved_launch_keeps_the_caller_selection(
    sqlite_instance: MemoryInterface,
) -> None:
    seed = _objective("goal")
    await _store(sqlite_instance, seed)
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[seed.id], dataset_name=DATASET)
    stored = MagicMock()
    stored.metadata = {}
    service = ScenarioRunService()
    with patch.object(service._memory, "get_scenario_result_header_async", new=AsyncMock(return_value=stored)):
        resumed = await service._example_selection_for_run_async(
            request=RunScenarioRequest(
                scenario_name="test.exact",
                target_name="target",
                scenario_result_id=str(uuid4()),
                example_selection=selection,
            )
        )
    assert resumed == selection


@pytest.mark.usefixtures("patch_central_database")
async def test_non_objective_conditions_are_unsupported(sqlite_instance: MemoryInterface) -> None:
    group = uuid4()
    objective = _objective("goal", prompt_group_id=group)
    prompt = _prompt("body", prompt_group_id=group, sequence=1, role="user")
    await _store(sqlite_instance, objective, prompt)
    await _mutate(
        sqlite_instance,
        prompt.id,
        conditions=[AnswerMatches(correct_answer="yes").model_dump(mode="json")],
    )
    selection = await _selection(sqlite_instance, selection_key=NAMED, example_ids=[group], dataset_name=DATASET)
    resolved = await ScenarioConfigurationResolver.resolve_configuration_async(
        scenario_name="test.exact",
        scenario_class=_ExactScenario,
        example_selection=selection,
        memory=sqlite_instance,
    )
    scenario = _ExactScenario()
    scenario.set_params_from_args(args=resolved)
    estimate = await scenario.get_run_size_estimate_async()
    assert estimate.dataset_size.model_dump()["value"] == 1
    with pytest.raises(DatasetExampleSelectionError, match="unsupported") as caught:
        await scenario._dataset_config.get_attack_seed_groups_async()
    assert caught.value.code == "unsupported"
    assert caught.value.example_id == group
    assert "conditions" in str(caught.value)
    assert "body" not in str(caught.value)


@pytest.mark.usefixtures("patch_central_database")
async def test_group_construction_preserves_order_off_the_event_loop(sqlite_instance: MemoryInterface) -> None:
    first, second = _objective("first"), _objective("second")
    await _store(sqlite_instance, first, second)
    selection = await _selection(
        sqlite_instance, selection_key=NAMED, example_ids=[second.id, first.id], dataset_name=DATASET
    )
    resolved = await ScenarioConfigurationResolver.resolve_configuration_async(
        scenario_name="test.exact",
        scenario_class=_ExactScenario,
        example_selection=selection,
        memory=sqlite_instance,
    )
    config = resolved["dataset_config"]
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    original = ExactExampleDatasetConfiguration._build_selected_groups

    def wrapped(self: ExactExampleDatasetConfiguration) -> list:
        seen["thread"] = threading.get_ident()
        return original(self)

    with patch.object(ExactExampleDatasetConfiguration, "_build_selected_groups", wrapped):
        groups = await config.get_attack_seed_groups_async()
    assert seen["thread"] != loop_thread
    assert [group.objective.value for group in groups] == ["second", "first"]
