# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for ``Scenario.MODALITY_POLICY`` — plan-time enforcement of modality compatibility.

Validation runs inside ``initialize_async`` before any attack is queued or any prompt is sent.
Fresh runs check built attacks; resumed runs check only the replayed seed groups. The policy
decides what happens to an attack whose payload provably cannot reach its target or scorer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar
from unittest.mock import MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget, get_mock_target
from unit.modality_profiles import IMAGE_EDIT_INPUT_MODALITIES, TEXT_ONLY_MODALITIES, VISION_INPUT_MODALITIES

from pyrit.converter import QRCodeConverter
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackConverterConfig,
    AttackScoringConfig,
    PromptSendingAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.models import SCENARIO_RUN_PLAN_METADATA_KEY, AttackSeedGroup, ComponentIdentifier, SeedObjective, SeedPrompt
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target.common.target_requirements import TargetRequirements
from pyrit.scenario.core import AtomicAttack, BaselineAttackPolicy, Scenario, ScenarioTechnique
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration, DatasetConfiguration
from pyrit.scenario.core.modality_validation import (
    ModalityPolicy,
    ModalityValidationError,
    ModalityVerdict,
    validate_atomic_attack,
)
from pyrit.score import Scorer, SubStringScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator

if TYPE_CHECKING:
    from pyrit.scenario.core.scenario_context import ScenarioContext

_TEST_SCORER_ID = ComponentIdentifier(class_name="MockScorer", class_module="tests.unit.scenario")


class _PolicyScenario(Scenario):
    """Minimal scenario returning a fixed list of atomic attacks."""

    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

    def __init__(self, *, atomic_attacks_to_return=None, **kwargs):
        class TestTechnique(ScenarioTechnique):
            TEST = ("test", {"concrete"}, "Test technique description.")
            ALL = ("all", {"all"})

            @classmethod
            def get_aggregate_tags(cls) -> set[str]:
                return {"all"}

        kwargs.setdefault("technique_class", TestTechnique)
        kwargs.setdefault("default_dataset_config", DatasetConfiguration())
        kwargs.setdefault("version", 1)
        if "objective_scorer" not in kwargs:
            scorer = MagicMock(spec=Scorer)
            scorer.get_identifier.return_value = _TEST_SCORER_ID
            scorer.get_scorer_metrics.return_value = None
            kwargs["objective_scorer"] = scorer
        super().__init__(**kwargs)
        self._atomic_attacks_to_return = atomic_attacks_to_return or []

    async def _resolve_seed_groups_by_dataset_async(self, *, apply_sampling: bool = True):
        return {}

    async def _build_atomic_attacks_async(self, *, context):
        return self._atomic_attacks_to_return


class _SampledPolicyScenario(_PolicyScenario):
    """Build one attack from the dataset's sampled or replayed seed groups."""

    async def _resolve_seed_groups_by_dataset_async(
        self, *, apply_sampling: bool = True
    ) -> dict[str, list[AttackSeedGroup]]:
        return await Scenario._resolve_seed_groups_by_dataset_async(self, apply_sampling=apply_sampling)

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        return [
            AtomicAttack(
                atomic_attack_name="sampled",
                attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=context.objective_target)),
                seed_groups=list(context.seed_groups),
                memory_labels=context.memory_labels,
            )
        ]


def _sampled_config() -> DatasetAttackConfiguration:
    return DatasetAttackConfiguration(
        seed_groups=[
            AttackSeedGroup(seeds=[SeedObjective(value="saved text")]),
            AttackSeedGroup(
                seeds=[SeedObjective(value="unsampled image"), SeedPrompt(value="seed.png", data_type="image_path")]
            ),
        ],
        max_dataset_size=1,
    )


async def _start_sampled_scenario(
    *, target, scenario_class: type[_SampledPolicyScenario] = _SampledPolicyScenario
) -> _SampledPolicyScenario:
    with patch(
        "pyrit.scenario.core.dataset_configuration.random.sample",
        side_effect=lambda population, k: list(population)[:k],
    ):
        scenario = scenario_class(default_dataset_config=_sampled_config())
        await _initialize(scenario, target=target)
    assert len(scenario._atomic_attacks[0].seed_groups) == 1
    assert scenario._atomic_attacks[0].seed_groups[0].objective.value == "saved text"
    return scenario


async def _resume_sampled_scenario(
    *, scenario_result_id: str, target, scenario_class: type[_SampledPolicyScenario] = _SampledPolicyScenario
) -> _SampledPolicyScenario:
    resumed = scenario_class(default_dataset_config=_sampled_config(), scenario_result_id=scenario_result_id)
    with patch("pyrit.scenario.core.dataset_configuration.random.sample", side_effect=AssertionError("resampled")):
        await _initialize(resumed, target=target)
    return resumed


def _atomic(*, target, converters=None, scorer=None, name="atomic") -> AtomicAttack:
    """A real AtomicAttack sending one objective through an optional converter chain."""
    kwargs = {"objective_target": target}
    if converters is not None:
        kwargs["attack_converter_config"] = AttackConverterConfig(
            request_converters=ConverterConfiguration.from_converters(converters=list(converters))
        )
    if scorer is not None:
        kwargs["attack_scoring_config"] = AttackScoringConfig(objective_scorer=scorer)
    return AtomicAttack(
        atomic_attack_name=name,
        attack_technique=AttackTechnique(attack=PromptSendingAttack(**kwargs)),
        seed_groups=[AttackSeedGroup(seeds=[SeedObjective(value=f"objective for {name}")])],
    )


def _incompatible(*, name="incompatible") -> AtomicAttack:
    """An attack whose converter emits an image into a text-only target."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    return _atomic(target=target, converters=[QRCodeConverter()], name=name)


def _compatible(*, name="compatible") -> AtomicAttack:
    """An attack whose converter emits an image into a target that accepts images."""
    target = get_mock_target(input_modalities=VISION_INPUT_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    return _atomic(target=target, converters=[QRCodeConverter()], name=name)


def _tap_atomic(*, width: int, target: MockPromptTarget | MagicMock, seed_group: AttackSeedGroup) -> AtomicAttack:
    attack = TreeOfAttacksWithPruningAttack(
        objective_target=target,
        attack_adversarial_config=AttackAdversarialConfig(target=MockPromptTarget()),
        tree_width=width,
    )
    return AtomicAttack(
        atomic_attack_name="tap_media",
        attack_technique=AttackTechnique(attack=attack),
        seed_groups=[seed_group],
    )


@pytest.mark.parametrize(
    ("width", "modalities", "seed", "expected", "projected"),
    [
        (2, TEXT_ONLY_MODALITIES, "image", ModalityVerdict.UNKNOWN, {"image_path", "text"}),
        (1, TEXT_ONLY_MODALITIES, "image", ModalityVerdict.INCOMPATIBLE, {"image_path"}),
        (2, IMAGE_EDIT_INPUT_MODALITIES, "image", ModalityVerdict.INCOMPATIBLE, {"image_path"}),
        (2, IMAGE_EDIT_INPUT_MODALITIES, "text_image", ModalityVerdict.COMPATIBLE, {"text", "image_path"}),
        (2, VISION_INPUT_MODALITIES, "image", ModalityVerdict.COMPATIBLE, {"text", "image_path"}),
    ],
)
async def test_tap_first_turn_roots_modality_and_skip(
    patch_central_database, width, modalities, seed, expected, projected
):
    """Only text-capable TAP siblings can rescue an incompatible seeded root."""
    seeds = [SeedObjective(value="objective"), SeedPrompt(value="seed.png", data_type="image_path")]
    if seed == "text_image":
        seeds.append(SeedPrompt(value="edit this", data_type="text"))
    target = get_mock_target(input_modalities=modalities, output_modalities=TEXT_ONLY_MODALITIES)
    atomic = _tap_atomic(width=width, target=target, seed_group=AttackSeedGroup(seeds=seeds))
    attack = atomic.attack_technique.attack
    assert isinstance(attack, TreeOfAttacksWithPruningAttack)
    assert attack.has_unseeded_first_turn_roots is (width > 1 and frozenset({"text"}) in modalities)
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.verdict is expected
    assert report.projected_request_types == projected
    if expected is ModalityVerdict.UNKNOWN:
        assert any("seeded root" in reason and "text" in reason for reason in report.reasons)
    scenario = _PolicyScenario(atomic_attacks_to_return=[atomic, _compatible(name="control")])
    await _initialize(scenario, target=target)
    assert [attack.atomic_attack_name for attack in scenario._atomic_attacks] == (
        ["control"] if expected is ModalityVerdict.INCOMPATIBLE else ["tap_media", "control"]
    )


def test_non_tap_media_seed_does_not_gain_generated_text_root(patch_central_database):
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    atomic = AtomicAttack(
        atomic_attack_name="single",
        attack_technique=AttackTechnique(attack=PromptSendingAttack(objective_target=target)),
        seed_groups=[
            AttackSeedGroup(
                seeds=[SeedObjective(value="objective"), SeedPrompt(value="seed.png", data_type="image_path")]
            )
        ],
    )
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.verdict is ModalityVerdict.INCOMPATIBLE
    assert report.projected_request_types == frozenset({"image_path"})


def test_tap_generated_roots_are_projected_through_request_converters(patch_central_database):
    """Text siblings cannot rescue a media root if the converter also makes them incompatible."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    seed = AttackSeedGroup(
        seeds=[SeedObjective(value="objective"), SeedPrompt(value="seed.png", data_type="image_path")]
    )
    attack = TreeOfAttacksWithPruningAttack(
        objective_target=target,
        attack_adversarial_config=AttackAdversarialConfig(target=MockPromptTarget()),
        attack_converter_config=AttackConverterConfig(
            request_converters=ConverterConfiguration.from_converters(converters=[QRCodeConverter()])
        ),
        tree_width=2,
    )
    atomic = AtomicAttack(
        atomic_attack_name="converted_tap",
        attack_technique=AttackTechnique(attack=attack),
        seed_groups=[seed],
    )
    report = validate_atomic_attack(atomic_attack=atomic)
    assert report.verdict is ModalityVerdict.INCOMPATIBLE
    assert report.projected_request_types == frozenset({"image_path"})
    assert any("QRCodeConverter" in reason for reason in report.reasons)


async def _initialize(scenario: Scenario, *, target=None) -> None:
    """Fill the parameter bag and initialize."""
    scenario.set_params_from_args(args={"objective_target": target or get_mock_target()})
    await scenario.initialize_async()


# ---------------------------------------------------------------------------
# Policy declaration
# ---------------------------------------------------------------------------
def test_modality_policy_defaults_to_skip():
    """Dropping unrunnable attacks is the default, matching BASELINE_ATTACK_POLICY's shape."""
    assert Scenario.MODALITY_POLICY is ModalityPolicy.SKIP


def test_modality_policy_is_overridable_per_scenario_class():
    """A scenario subclass can choose a stricter policy."""

    class _Strict(_PolicyScenario):
        MODALITY_POLICY: ClassVar[ModalityPolicy] = ModalityPolicy.RAISE

    assert _Strict.MODALITY_POLICY is ModalityPolicy.RAISE
    assert _PolicyScenario.MODALITY_POLICY is ModalityPolicy.SKIP


# ---------------------------------------------------------------------------
# SKIP
# ---------------------------------------------------------------------------
async def test_skip_drops_incompatible_and_keeps_compatible(patch_central_database):
    """The incompatible attack is removed; the compatible one survives."""
    scenario = _PolicyScenario(atomic_attacks_to_return=[_incompatible(), _compatible()])
    await _initialize(scenario)
    assert [attack.atomic_attack_name for attack in scenario._atomic_attacks] == ["compatible"]


async def test_skip_keeps_attacks_whose_compatibility_is_unknown(patch_central_database):
    """An attack against a target with unreadable capabilities is kept, never dropped."""
    scenario = _PolicyScenario(atomic_attacks_to_return=[_atomic(target=get_mock_target(), name="unknown")])
    await _initialize(scenario)
    assert [attack.atomic_attack_name for attack in scenario._atomic_attacks] == ["unknown"]


async def test_skip_excludes_dropped_attack_from_display_group_map(patch_central_database):
    """Filtering happens before the display-group map is built from the surviving attacks."""
    scenario = _PolicyScenario(atomic_attacks_to_return=[_incompatible(), _compatible()])
    await _initialize(scenario)
    assert "incompatible" not in scenario._display_group_map
    assert "compatible" in scenario._display_group_map


async def test_skip_excludes_dropped_attack_from_persisted_run_plan(patch_central_database):
    """The persisted plan records only the attacks that will actually run."""
    scenario = _PolicyScenario(atomic_attacks_to_return=[_incompatible(), _compatible()])
    await _initialize(scenario)
    [stored] = scenario._memory.get_scenario_results(scenario_result_ids=[scenario._scenario_result_id])
    planned = {group["atomic_attack_name"] for group in stored.metadata["run_plan"]["atomic_groups"]}
    assert planned == {"compatible"}


@pytest.mark.parametrize("policy", list(ModalityPolicy))
@pytest.mark.parametrize(
    ("outputs", "strict", "raise_on_empty", "incompatible"),
    [
        ([{"text"}], False, False, False),
        ([{"text", "audio_path"}], False, False, False),
        ([{"text", "audio_path"}], False, True, False),
        ([{"text", "audio_path"}], True, False, True),
        ([{"audio_path"}], False, False, True),
        ([{"audio_path"}], False, True, True),
        ([{"text"}, {"audio_path"}], False, False, False),
    ],
)
async def test_modality_policy_selective_text_scorer_output_matrix(
    patch_central_database, policy, outputs, strict, raise_on_empty, incompatible
):
    """A working mixed response survives all policies; unscorable responses obey policy."""

    class _ConfiguredPolicyScenario(_PolicyScenario):
        MODALITY_POLICY: ClassVar[ModalityPolicy] = policy

    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=outputs)
    validator = ScorerPromptValidator(
        supported_data_types=["text"],
        enforce_all_pieces_valid=strict,
        raise_on_no_valid_pieces=raise_on_empty,
    )
    scorer = SubStringScorer(substring="match", validator=validator)
    scenario = _ConfiguredPolicyScenario(atomic_attacks_to_return=[_atomic(target=target, scorer=scorer)])

    if incompatible and policy is not ModalityPolicy.WARN:
        with pytest.raises(ModalityValidationError):
            await _initialize(scenario, target=target)
    else:
        await _initialize(scenario, target=target)
        assert len(scenario._atomic_attacks) == 1


@pytest.mark.parametrize("legacy_plan", [False, True])
async def test_resume_validates_only_persisted_seed_groups(patch_central_database, legacy_plan):
    """An unsampled image group cannot invalidate the saved text-only attack."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    original = await _start_sampled_scenario(target=target)
    scenario_result_id = original._scenario_result_id
    [stored] = original._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
    original_metadata = dict(stored.metadata)
    if legacy_plan:
        original_metadata.pop(SCENARIO_RUN_PLAN_METADATA_KEY)
        original._memory.update_scenario_metadata(scenario_result_id=scenario_result_id, metadata=original_metadata)

    resumed = await _resume_sampled_scenario(scenario_result_id=scenario_result_id, target=target)

    assert resumed._scenario_result_id == scenario_result_id
    assert len(resumed._atomic_attacks) == 1
    assert [group.logical_id for group in resumed._atomic_attacks[0].seed_groups] == [
        original._atomic_attacks[0].seed_groups[0].logical_id
    ]
    [after] = resumed._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
    assert SCENARIO_RUN_PLAN_METADATA_KEY in after.metadata
    assert after.metadata["objective_hashes"] == original_metadata["objective_hashes"]
    if not legacy_plan:
        assert after.metadata[SCENARIO_RUN_PLAN_METADATA_KEY] == original_metadata[SCENARIO_RUN_PLAN_METADATA_KEY]


@pytest.mark.parametrize("legacy_plan", [False, True])
async def test_resume_rejects_incompatible_persisted_seed_group(patch_central_database, legacy_plan):
    """SKIP must not silently alter a stored plan when a saved group becomes incompatible."""
    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    original = await _start_sampled_scenario(target=target)
    scenario_result_id = original._scenario_result_id
    [stored] = original._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
    metadata = dict(stored.metadata)
    if legacy_plan:
        metadata.pop(SCENARIO_RUN_PLAN_METADATA_KEY)
        original._memory.update_scenario_metadata(scenario_result_id=scenario_result_id, metadata=metadata)

    changed_target = get_mock_target(input_modalities=[{"image_path"}], output_modalities=TEXT_ONLY_MODALITIES)
    with pytest.raises(ModalityValidationError, match="cannot resume.*saved.*incompatible"):
        await _resume_sampled_scenario(scenario_result_id=scenario_result_id, target=changed_target)

    [after] = original._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
    assert after.metadata == metadata


async def test_resume_warn_retains_incompatible_persisted_seed_group(patch_central_database, caplog):
    """WARN keeps the saved group rather than changing the replayed plan."""

    class _WarnSampledPolicyScenario(_SampledPolicyScenario):
        MODALITY_POLICY: ClassVar[ModalityPolicy] = ModalityPolicy.WARN

    target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES, output_modalities=TEXT_ONLY_MODALITIES)
    original = await _start_sampled_scenario(target=target, scenario_class=_WarnSampledPolicyScenario)
    changed_target = get_mock_target(input_modalities=[{"image_path"}], output_modalities=TEXT_ONLY_MODALITIES)

    with caplog.at_level(logging.WARNING, logger="pyrit.scenario.core.scenario"):
        resumed = await _resume_sampled_scenario(
            scenario_result_id=original._scenario_result_id,
            target=changed_target,
            scenario_class=_WarnSampledPolicyScenario,
        )

    assert [group.logical_id for group in resumed._atomic_attacks[0].seed_groups] == [
        original._atomic_attacks[0].seed_groups[0].logical_id
    ]
    assert any("modality incompatibility" in record.getMessage().lower() for record in caplog.records)


async def test_skip_logs_a_warning_naming_the_attack_and_reason(patch_central_database, caplog):
    """Dropping a whole atomic attack is loud even though the policy allows it."""
    scenario = _PolicyScenario(atomic_attacks_to_return=[_incompatible(), _compatible()])
    with caplog.at_level(logging.WARNING, logger="pyrit.scenario.core.scenario"):
        await _initialize(scenario)
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert any("incompatible" in record.getMessage() for record in warnings)
    assert any("image_path" in record.getMessage() for record in warnings)


async def test_skip_raises_when_every_attack_is_dropped(patch_central_database):
    """A run that would proceed with zero attacks is a failure, not a success."""
    scenario = _PolicyScenario(atomic_attacks_to_return=[_incompatible(name="a"), _incompatible(name="b")])
    with pytest.raises(ModalityValidationError, match="all"):
        await _initialize(scenario)


# ---------------------------------------------------------------------------
# WARN
# ---------------------------------------------------------------------------
async def test_warn_keeps_the_attack_and_logs(patch_central_database, caplog):
    """WARN surfaces the problem but lets the run proceed."""

    class _Warn(_PolicyScenario):
        MODALITY_POLICY: ClassVar[ModalityPolicy] = ModalityPolicy.WARN

    scenario = _Warn(atomic_attacks_to_return=[_incompatible()])
    with caplog.at_level(logging.WARNING, logger="pyrit.scenario.core.scenario"):
        await _initialize(scenario)
    assert [attack.atomic_attack_name for attack in scenario._atomic_attacks] == ["incompatible"]
    assert any("incompatible" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# RAISE
# ---------------------------------------------------------------------------
async def test_raise_aborts_initialization(patch_central_database):
    """RAISE stops the run before anything is persisted or queued."""

    class _Raise(_PolicyScenario):
        MODALITY_POLICY: ClassVar[ModalityPolicy] = ModalityPolicy.RAISE

    scenario = _Raise(atomic_attacks_to_return=[_incompatible(), _compatible()])
    with pytest.raises(ModalityValidationError) as excinfo:
        await _initialize(scenario)
    assert "incompatible" in str(excinfo.value)
    assert "image_path" in str(excinfo.value)


async def test_raise_error_is_catchable_as_value_error(patch_central_database):
    """Existing ``except ValueError`` handlers around initialize_async keep working."""

    class _Raise(_PolicyScenario):
        MODALITY_POLICY: ClassVar[ModalityPolicy] = ModalityPolicy.RAISE

    scenario = _Raise(atomic_attacks_to_return=[_incompatible()])
    with pytest.raises(ValueError):
        await _initialize(scenario)


async def test_raise_happens_before_any_prompt_is_sent(patch_central_database):
    """Fail fast: the target is never contacted when validation rejects the plan."""

    class _Raise(_PolicyScenario):
        MODALITY_POLICY: ClassVar[ModalityPolicy] = ModalityPolicy.RAISE

    attack = _incompatible()
    target = attack.attack_technique.attack.get_objective_target()
    scenario = _Raise(atomic_attacks_to_return=[attack])
    with pytest.raises(ModalityValidationError):
        await _initialize(scenario)
    assert target.send_prompt_async.call_count == 0


# ---------------------------------------------------------------------------
# Ordering against the existing static check
# ---------------------------------------------------------------------------
async def test_target_requirements_failure_precedes_modality_validation(patch_central_database):
    """
    ``TARGET_REQUIREMENTS`` is resolved before atomic attacks are built.

    A scenario whose target fails the static capability check reports that, not a derived
    modality verdict — the static misconfiguration is the more fundamental problem.
    """

    class _NeedsVideo(_PolicyScenario):
        TARGET_REQUIREMENTS: ClassVar[TargetRequirements] = TargetRequirements(
            required_input_modalities=frozenset({frozenset({"video_path"})})
        )
        MODALITY_POLICY: ClassVar[ModalityPolicy] = ModalityPolicy.RAISE

    scenario = _NeedsVideo(atomic_attacks_to_return=[_incompatible()])
    text_target = get_mock_target(input_modalities=TEXT_ONLY_MODALITIES)
    with pytest.raises(ValueError, match="video_path") as excinfo:
        await _initialize(scenario, target=text_target)
    assert not isinstance(excinfo.value, ModalityValidationError)
