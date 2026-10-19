# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Sampling-parameter sweeps for adversarial chat targets (GitHub issue #365).

``AdversarialBenchmark`` compares attack success rate (ASR) across adversarial targets while the
objective target, objectives, scorer, and technique stay fixed. A sampling sweep reuses it unchanged:
each adversarial generator is built once per swept value (for example temperature 0.2, 0.7 and 1.2),
the variants are registered in ``TargetRegistry`` for the duration of the sweep, and the benchmark
runs once per independent repetition with every variant as an adversarial target.

This module adds only what the benchmark does not do on its own:

* validation that each variant differs from its siblings only in the swept parameter,
* explicit handling of generators that do not support a parameter,
* deterministic variant names, run order, and trial identifiers,
* role separation checks between adversarial, objective, and scorer targets, and
* conversion of ``ScenarioResult`` objects into ``AdversarialTrialRecord`` rows for
  ``pyrit.analytics.adversarial_settings_analysis``.

It never changes PyRIT's default adversarial settings.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
import re
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from pyrit.analytics.adversarial_settings_analysis import (
    AdversarialTrialRecord,
    read_trial_records,
    write_trial_records,
)
from pyrit.common.utils import to_sha256
from pyrit.models import AttackOutcome, ConversationType, TokenUsage
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import CapabilityName
from pyrit.registry import TargetRegistry

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator, Iterable, Mapping, Sequence
    from pathlib import Path

    from pyrit.memory import MemoryInterface
    from pyrit.models import AttackResult, AttackSeedGroup, ComponentIdentifier, ScenarioResult
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)

SWEEP_EXPERIMENT_LABEL = "sampling_sweep_experiment"
SWEEP_REPETITION_LABEL = "sampling_sweep_repetition"
_GENERATOR_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_NO_RESULT_REASON = "No attack result was recorded for this planned objective."


class SamplingParameter(str, Enum):
    """Adversarial sampling parameters that a sweep can vary."""

    TEMPERATURE = "temperature"
    TOP_P = "top_p"

    @property
    def valid_range(self) -> tuple[float, float]:
        """Inclusive range accepted by OpenAI-compatible chat targets."""
        return (0.0, 2.0) if self is SamplingParameter.TEMPERATURE else (0.0, 1.0)


class UnsupportedParameterPolicy(str, Enum):
    """What to do when a generator cannot accept the swept parameter."""

    #: Fail before any target is built. Use when every generator is expected to participate.
    RAISE = "raise"
    #: Leave the generator out of the sweep and report it in ``SamplingSweepPlan.skipped_generators``.
    SKIP = "skip"


@dataclass(frozen=True)
class AdversarialGeneratorSpec:
    """
    An adversarial model whose sampling parameters are swept.

    Args:
        name (str): Short identifier used in variant registry names, for example ``"gpt4o"``.
            Lowercase letters, digits, and underscores only.
        factory (Callable[[Mapping[str, float]], PromptTarget]): Builds a fresh adversarial target
            given the swept parameter, for example ``{"temperature": 0.7}``. Every other setting must
            be identical across calls; the sweep verifies this from the target identifiers.
        supported_parameters (frozenset[SamplingParameter]): Parameters the model accepts. Reasoning
            models that reject ``temperature`` should omit it.
    """

    name: str
    factory: Callable[[Mapping[str, float]], PromptTarget]
    supported_parameters: frozenset[SamplingParameter] = frozenset(SamplingParameter)

    def __post_init__(self) -> None:
        """
        Validate the generator name and factory.

        Raises:
            ValueError: If the name is not a lowercase identifier or the factory is not callable.
        """
        if not _GENERATOR_NAME_PATTERN.match(self.name) or "__" in self.name:
            raise ValueError(
                f"Generator name {self.name!r} must use lowercase letters, digits, and single underscores."
            )
        if not callable(self.factory):
            raise ValueError(f"Generator {self.name!r} needs a callable factory.")


@dataclass(frozen=True)
class SamplingVariant:
    """One generator at one value of the swept parameter."""

    generator_name: str
    parameter: SamplingParameter
    value: float

    @property
    def registry_name(self) -> str:
        """Deterministic ``TargetRegistry`` name, for example ``gpt4o__temperature_0p7``."""
        value_text = f"{self.value:g}".replace(".", "p").replace("-", "m")
        return f"{self.generator_name}__{self.parameter.value}_{value_text}"


@dataclass(frozen=True)
class SamplingSweepPlan:
    """
    A validated sampling-sweep design.

    Args:
        generators (Sequence[AdversarialGeneratorSpec]): Adversarial models to compare.
        parameter (SamplingParameter): The one parameter varied in this sweep. Sweep temperature and
            top_p in separate plans so their effects are not confounded.
        values (Sequence[float]): Values of ``parameter`` to evaluate.
        baseline_value (float): The value treated as the reference in comparisons. Must be in ``values``.
        repetitions (int): Independent benchmark runs per variant.
        technique (str): One ``AdversarialBenchmarkTechnique`` value, held constant for the sweep.
        run_order_seed (int): Seed for the per-repetition variant order, so time-dependent drift in
            endpoints is spread across variants reproducibly.
        unsupported_parameter_policy (UnsupportedParameterPolicy): Handling for generators that do not
            support ``parameter``.
    """

    generators: Sequence[AdversarialGeneratorSpec]
    parameter: SamplingParameter
    values: Sequence[float]
    baseline_value: float
    repetitions: int = 3
    technique: str = "crescendo_simulated"
    run_order_seed: int = 0
    unsupported_parameter_policy: UnsupportedParameterPolicy = UnsupportedParameterPolicy.RAISE
    skipped_generators: dict[str, str] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        """
        Validate the design.

        Raises:
            ValueError: If the design is empty, ambiguous, or out of range, or a generator does not support
                the parameter under the ``RAISE`` policy.
        """
        if not self.generators:
            raise ValueError("A sampling sweep needs at least one generator.")
        names = [generator.name for generator in self.generators]
        if len(names) != len(set(names)):
            raise ValueError("Generator names must be unique.")
        if len(self.values) < 2:
            raise ValueError("A sampling sweep needs at least two values.")
        if len(set(self.values)) != len(self.values):
            raise ValueError("Swept values must be unique.")
        low, high = self.parameter.valid_range
        for value in self.values:
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError(f"Swept value {value!r} must be a finite number.")
            if not low <= value <= high:
                raise ValueError(f"{self.parameter.value} value {value} is outside [{low}, {high}].")
        if self.baseline_value not in self.values:
            raise ValueError("baseline_value must be one of the swept values.")
        if not isinstance(self.repetitions, int) or isinstance(self.repetitions, bool) or self.repetitions < 1:
            raise ValueError("repetitions must be a positive integer.")
        if not self.technique:
            raise ValueError("A sampling sweep needs exactly one technique.")
        for generator in self.generators:
            if self.parameter in generator.supported_parameters:
                continue
            reason = f"{generator.name} does not support {self.parameter.value}."
            if self.unsupported_parameter_policy is UnsupportedParameterPolicy.RAISE:
                raise ValueError(reason + " Remove it from the sweep or use UnsupportedParameterPolicy.SKIP.")
            self.skipped_generators[generator.name] = reason
        if len(self.skipped_generators) == len(self.generators):
            raise ValueError(f"No generator supports {self.parameter.value}.")

    @property
    def active_generators(self) -> list[AdversarialGeneratorSpec]:
        """Generators that participate in the sweep, in declaration order."""
        return [generator for generator in self.generators if generator.name not in self.skipped_generators]

    @property
    def variants(self) -> list[SamplingVariant]:
        """Every (generator, value) variant, in declaration order."""
        return [
            SamplingVariant(generator_name=generator.name, parameter=self.parameter, value=float(value))
            for generator in self.active_generators
            for value in self.values
        ]

    def run_order(self, *, repetition: int) -> list[SamplingVariant]:
        """
        Return the variant order for one repetition.

        Args:
            repetition (int): Zero-based repetition index.

        Returns:
            list[SamplingVariant]: A seeded permutation of ``variants``; identical for identical inputs.
        """
        order = list(self.variants)
        random.Random(f"{self.run_order_seed}:{repetition}").shuffle(order)
        return order

    def planned_attack_count(self, *, objective_count: int) -> int:
        """
        Count planned attacks before retries.

        Args:
            objective_count (int): Objectives in the pinned dataset.

        Returns:
            int: ``variants x objectives x repetitions``.
        """
        return len(self.variants) * objective_count * self.repetitions


def build_variant_targets(*, plan: SamplingSweepPlan) -> dict[SamplingVariant, PromptTarget]:
    """
    Build and validate one adversarial target per variant.

    Each target must be a multi-turn ``PromptTarget`` that reports the requested value in its
    identifier, and all variants of one generator must have identical identifiers apart from the
    swept parameter (same class, endpoint, model, and other sampling settings).

    Args:
        plan (SamplingSweepPlan): The validated plan.

    Returns:
        dict[SamplingVariant, PromptTarget]: Targets keyed by variant, in plan order.

    Raises:
        ValueError: If a target is invalid, ignores the requested value, or differs from its siblings
            in anything other than the swept parameter.
    """
    targets: dict[SamplingVariant, PromptTarget] = {}
    for generator in plan.active_generators:
        reference: tuple[str, dict[str, Any]] | None = None
        for value in plan.values:
            variant = SamplingVariant(generator_name=generator.name, parameter=plan.parameter, value=float(value))
            target = generator.factory({plan.parameter.value: float(value)})
            _validate_variant_target(target=target, variant=variant)
            controlled = _controlled_identity(target=target, parameter=plan.parameter)
            if reference is None:
                reference = controlled
            elif controlled != reference:
                raise ValueError(
                    f"Variant {variant.registry_name} differs from other {generator.name} variants in settings other "
                    f"than {plan.parameter.value}; the factory must change only the swept parameter."
                )
            targets[variant] = target
    if len({id(target) for target in targets.values()}) != len(targets):
        raise ValueError("Each variant needs its own target instance; a factory returned a shared instance.")
    return targets


def check_role_separation(
    *,
    adversarial_targets: Iterable[PromptTarget],
    objective_target: PromptTarget,
    objective_scorer: TrueFalseScorer,
) -> None:
    """
    Ensure no adversarial variant is also the objective target or the scorer's chat target.

    Sharing an instance would let a sweep change the system under test or the judge.

    Args:
        adversarial_targets (Iterable[PromptTarget]): The variant targets.
        objective_target (PromptTarget): The fixed target under attack.
        objective_scorer (TrueFalseScorer): The fixed objective scorer.

    Raises:
        ValueError: If an instance is shared between roles.
    """
    adversarial_ids = {id(target) for target in adversarial_targets}
    if id(objective_target) in adversarial_ids:
        raise ValueError("The objective target must not be one of the swept adversarial targets.")
    scorer_target = objective_scorer.get_chat_target()
    if scorer_target is not None and id(scorer_target) in adversarial_ids:
        raise ValueError("The objective scorer must not use a swept adversarial target as its judge.")


@contextmanager
def registered_variants(
    *, targets: Mapping[SamplingVariant, PromptTarget], registry: TargetRegistry | None = None
) -> Generator[list[str], None, None]:
    """
    Register variant targets for the duration of a sweep, then remove exactly those entries.

    Args:
        targets (Mapping[SamplingVariant, PromptTarget]): Targets from ``build_variant_targets``.
        registry (TargetRegistry | None): Registry to use. Defaults to the singleton.

    Yields:
        list[str]: The registered names, in ``targets`` order.

    Raises:
        ValueError: If a variant name is already registered. Existing entries are never replaced.
    """
    target_registry = registry or TargetRegistry.get_registry_singleton()
    names = [variant.registry_name for variant in targets]
    for name in names:
        target_registry.instances.validate_name_available(name)
    registered: list[str] = []
    try:
        for variant, target in targets.items():
            target_registry.instances.register(target, name=variant.registry_name, tags=["sampling_sweep"])
            registered.append(variant.registry_name)
        yield names
    finally:
        for name in registered:
            target_registry.instances.unregister(name)


def find_scorer_coupled_attacks(
    *, atomic_attacks: Iterable[AtomicAttack], targets: Mapping[SamplingVariant, PromptTarget]
) -> list[str]:
    """
    Return atomic attacks whose objective scorer runs on a swept adversarial target.

    Some techniques build their own scorer from the adversarial target. For example, TAP replaces a
    true/false benchmark scorer with a ``SelfAskScaleScorer`` on ``adversarial_chat``. In a sampling sweep
    that would change the judge together with the attacker, so the scorer would no longer be fixed.

    Args:
        atomic_attacks (Iterable[AtomicAttack]): Atomic attacks built by the benchmark.
        targets (Mapping[SamplingVariant, PromptTarget]): The swept variant targets.

    Returns:
        list[str]: Names of atomic attacks with a coupled scorer, in input order.
    """
    variant_hashes = {target.get_identifier().hash for target in targets.values()}
    coupled: list[str] = []
    for atomic_attack in atomic_attacks:
        identifier = atomic_attack.attack_technique.get_identifier()
        scorer_nodes = [node for name, node in _walk_identifier(identifier) if name == "objective_scorer"]
        if any(inner.hash in variant_hashes for scorer in scorer_nodes for _, inner in _walk_identifier(scorer)):
            coupled.append(atomic_attack.atomic_attack_name)
    return coupled


def objective_ids_by_text(*, seed_groups: Iterable[AttackSeedGroup]) -> dict[str, str]:
    """
    Map objective text to a stable identifier.

    Uses the HarmBench ``BehaviorID`` when present so records can be joined with published benchmark
    data, and a SHA-256 prefix of the text otherwise. Records never store the objective text.

    Args:
        seed_groups (Iterable[AttackSeedGroup]): The pinned objectives.

    Returns:
        dict[str, str]: Identifier keyed by objective text.

    Raises:
        ValueError: If a group has no objective, or two objectives map to one identifier.
    """
    ids: dict[str, str] = {}
    for seed_group in seed_groups:
        objective = seed_group.objective
        if objective is None:
            raise ValueError("Every pinned seed group needs an objective.")
        behavior_id = (objective.metadata or {}).get("BehaviorID")
        ids[objective.value] = str(behavior_id) if behavior_id else f"sha256:{to_sha256(objective.value)[:16]}"
    if len(set(ids.values())) != len(ids):
        raise ValueError("Pinned objectives must have unique identifiers.")
    return ids


def make_trial_id(*, experiment_id: str, variant: SamplingVariant, repetition: int, objective_id: str) -> str:
    """
    Return a deterministic identifier for one planned attack.

    Args:
        experiment_id (str): The experiment identifier.
        variant (SamplingVariant): The adversarial variant.
        repetition (int): Zero-based repetition index.
        objective_id (str): Stable objective identifier.

    Returns:
        str: A 16-character hex identifier.
    """
    return _short_hash(experiment_id, variant.registry_name, str(repetition), objective_id)


def make_cell_id(*, experiment_id: str, variant: SamplingVariant, repetition: int) -> str:
    """
    Return a deterministic identifier for one (variant, repetition) cell.

    Args:
        experiment_id (str): The experiment identifier.
        variant (SamplingVariant): The adversarial variant.
        repetition (int): Zero-based repetition index.

    Returns:
        str: A 16-character hex identifier.
    """
    return _short_hash(experiment_id, variant.registry_name, str(repetition))


async def records_from_scenario_result_async(
    *,
    result: ScenarioResult,
    plan: SamplingSweepPlan,
    experiment_id: str,
    repetition: int,
    objective_ids: Mapping[str, str],
    token_reader: Callable[[AttackResult], Awaitable[dict[str, int | None]]] | None = None,
) -> list[AdversarialTrialRecord]:
    """
    Convert one benchmark repetition into trial records.

    Results are attributed to variants through ``display_group_map``, which ``AdversarialBenchmark`` sets
    to the adversarial target's registry name. For each (variant, objective) only the newest result is
    kept, as in ``build_scripts/export_adversarial_benchmark_result.py``; older retries are counted in
    ``superseded_attempts``. Every planned (variant, objective) pair without a result is recorded as
    ``error`` so missing work lowers the all-outcome ASR instead of vanishing.

    Args:
        result (ScenarioResult): The benchmark result for one repetition.
        plan (SamplingSweepPlan): The plan the repetition ran.
        experiment_id (str): The experiment identifier.
        repetition (int): Zero-based repetition index.
        objective_ids (Mapping[str, str]): Objective identifiers keyed by objective text.
        token_reader (Callable[[AttackResult], Awaitable[dict[str, int | None]]] | None): Optional reader for
            token counts. Defaults to no token data.

    Returns:
        list[AdversarialTrialRecord]: One record per planned (variant, objective) pair.
    """
    variants = {variant.registry_name: variant for variant in plan.variants}
    grouped: dict[tuple[SamplingVariant, str], list[AttackResult]] = defaultdict(list)
    for atomic_attack_name, attack_results in result.attack_results.items():
        variant = variants.get(result.display_group_map.get(atomic_attack_name, ""))
        if variant is None:
            logger.warning(f"Ignoring results for atomic attack {atomic_attack_name!r}: not a sweep variant.")
            continue
        for attack_result in attack_results:
            objective_id = objective_ids.get(attack_result.objective)
            if objective_id is None:
                logger.warning(f"Ignoring a result in {atomic_attack_name!r} for an objective outside the plan.")
                continue
            grouped[(variant, objective_id)].append(attack_result)

    records: list[AdversarialTrialRecord] = []
    for variant in plan.variants:
        for objective_id in sorted(objective_ids.values()):
            attempts = grouped.get((variant, objective_id), [])
            common: dict[str, Any] = {
                "experiment_id": experiment_id,
                "trial_id": make_trial_id(
                    experiment_id=experiment_id, variant=variant, repetition=repetition, objective_id=objective_id
                ),
                "cell_id": make_cell_id(experiment_id=experiment_id, variant=variant, repetition=repetition),
                "generator_name": variant.generator_name,
                "technique": plan.technique,
                "parameter": plan.parameter.value,
                "value": variant.value,
                "repetition": repetition,
                "objective_id": objective_id,
            }
            if not attempts:
                records.append(
                    AdversarialTrialRecord(
                        **common, outcome=AttackOutcome.ERROR.value, outcome_reason=_NO_RESULT_REASON
                    )
                )
                continue
            latest = max(attempts, key=lambda attempt: attempt.timestamp)
            tokens = await token_reader(latest) if token_reader else {}
            records.append(
                AdversarialTrialRecord(
                    **common,
                    outcome=latest.outcome.value,
                    outcome_reason=latest.outcome_reason,
                    executed_turns=latest.executed_turns,
                    backtrack_count=_optional_int(latest.metadata.get("backtrack_count")),
                    execution_time_ms=latest.execution_time_ms,
                    attack_result_id=latest.attack_result_id,
                    superseded_attempts=len(attempts) - 1,
                    **tokens,
                )
            )
    return records


def make_memory_token_reader(*, memory: MemoryInterface) -> Callable[[AttackResult], Awaitable[dict[str, int | None]]]:
    """
    Build a token reader that sums provider-reported usage from PyRIT memory.

    Objective tokens come from the attack's main conversation; adversarial tokens come from conversations
    the attack marked as ``ADVERSARIAL``. Counts stay None when a provider reports no usage.

    Args:
        memory (MemoryInterface): The memory instance holding the conversations.

    Returns:
        Callable[[AttackResult], Awaitable[dict[str, int | None]]]: The reader.
    """

    async def _sum_async(conversation_ids: Sequence[str]) -> tuple[int | None, int | None]:
        input_total: int | None = None
        output_total: int | None = None
        for conversation_id in conversation_ids:
            for piece in await memory.get_message_pieces_async(conversation_id=conversation_id):
                if piece.api_role != "assistant":
                    continue
                usage = TokenUsage.from_metadata(piece.prompt_metadata or {})
                if usage is None:
                    continue
                if usage.input_tokens is not None:
                    input_total = (input_total or 0) + usage.input_tokens
                if usage.output_tokens is not None:
                    output_total = (output_total or 0) + usage.output_tokens
        return input_total, output_total

    async def _read_async(attack_result: AttackResult) -> dict[str, int | None]:
        adversarial_ids = [
            reference.conversation_id
            for reference in attack_result.related_conversations
            if reference.conversation_type == ConversationType.ADVERSARIAL
        ]
        objective_in, objective_out = await _sum_async([attack_result.conversation_id])
        adversarial_in, adversarial_out = await _sum_async(adversarial_ids)
        return {
            "objective_input_tokens": objective_in,
            "objective_output_tokens": objective_out,
            "adversarial_input_tokens": adversarial_in,
            "adversarial_output_tokens": adversarial_out,
        }

    return _read_async


async def run_sampling_sweep_async(
    *,
    plan: SamplingSweepPlan,
    experiment_id: str,
    objective_target: PromptTarget,
    objective_scorer: TrueFalseScorer,
    dataset_config: DatasetAttackConfiguration,
    output_path: Path | None = None,
    max_concurrency: int = 1,
    scenario_factory: Callable[..., Any] | None = None,
) -> list[AdversarialTrialRecord]:
    """
    Run ``AdversarialBenchmark`` once per repetition with every sweep variant as an adversarial target.

    The objective target, scorer, dataset, and technique are the same objects for every repetition, and
    cache reuse is disabled so repetitions are independent samples. Records are appended to
    ``output_path`` after each repetition; repetitions already present there are skipped, so an
    interrupted sweep can be resumed with the same arguments.

    Args:
        plan (SamplingSweepPlan): The validated plan.
        experiment_id (str): Identifier written to every record and to scenario memory labels.
        objective_target (PromptTarget): The fixed target under attack.
        objective_scorer (TrueFalseScorer): The fixed objective scorer.
        dataset_config (DatasetAttackConfiguration): A pinned dataset, for example the
            ``harmbench-balanced-14-v1`` subset. Its sampled selection must be the same on every call.
        output_path (Path | None): Optional JSONL file for records.
        max_concurrency (int): Benchmark concurrency per repetition.
        scenario_factory (Callable[..., Any] | None): Builds the scenario. Defaults to
            ``AdversarialBenchmark``; tests pass a stub.

    Returns:
        list[AdversarialTrialRecord]: Records for every repetition, including resumed ones.

    Raises:
        ValueError: If the dataset selection changes between repetitions, roles share an instance, or the
            objective target's identity changes during the sweep.
    """
    if scenario_factory is None:
        from pyrit.scenario.scenarios.benchmark.adversarial import AdversarialBenchmark

        scenario_factory = AdversarialBenchmark
    technique = _resolve_technique(plan.technique)
    targets = build_variant_targets(plan=plan)
    check_role_separation(
        adversarial_targets=targets.values(), objective_target=objective_target, objective_scorer=objective_scorer
    )
    objective_identity = objective_target.get_identifier().hash
    existing = (
        [r for r in read_trial_records(path=output_path) if r.experiment_id == experiment_id] if output_path else []
    )
    completed_repetitions = {record.repetition for record in existing}
    records = list(existing)
    pinned_ids: dict[str, str] | None = None

    with registered_variants(targets=targets):
        for repetition in range(plan.repetitions):
            if repetition in completed_repetitions:
                logger.info(f"Sampling sweep {experiment_id}: repetition {repetition} already recorded; skipping.")
                continue
            objective_ids = objective_ids_by_text(seed_groups=await dataset_config.get_attack_seed_groups_async())
            if pinned_ids is None:
                pinned_ids = objective_ids
            elif objective_ids != pinned_ids:
                raise ValueError("The dataset selection changed between repetitions; pin the objectives.")
            scenario = scenario_factory(objective_scorer=objective_scorer, use_cached=False)
            scenario.set_params_from_args(
                args={
                    "objective_target": objective_target,
                    "adversarial_targets": [variant.registry_name for variant in plan.run_order(repetition=repetition)],
                    "scenario_techniques": [technique],
                    "dataset_config": dataset_config,
                    "max_concurrency": max_concurrency,
                    "use_cached": False,
                    "memory_labels": {SWEEP_EXPERIMENT_LABEL: experiment_id, SWEEP_REPETITION_LABEL: str(repetition)},
                }
            )
            await scenario.initialize_async()
            # Scenario exposes no public accessor for its atomic attacks; read them only for this guard.
            coupled = find_scorer_coupled_attacks(
                atomic_attacks=getattr(scenario, "_atomic_attacks", []), targets=targets
            )
            if coupled:
                raise ValueError(
                    f"Technique {plan.technique!r} scores with the swept adversarial target ({coupled}), so the judge "
                    "would change with the attacker. Choose a technique that uses the benchmark scorer."
                )
            result = await scenario.run_async()
            if objective_target.get_identifier().hash != objective_identity:
                raise ValueError("The objective target configuration changed during the sweep.")
            repetition_records = await records_from_scenario_result_async(
                result=result,
                plan=plan,
                experiment_id=experiment_id,
                repetition=repetition,
                objective_ids=objective_ids,
                token_reader=_default_token_reader(),
            )
            if output_path is not None:
                write_trial_records(records=repetition_records, path=output_path)
            records.extend(repetition_records)
    return records


def _walk_identifier(identifier: ComponentIdentifier) -> Generator[tuple[str, ComponentIdentifier], None, None]:
    pending: list[tuple[str, ComponentIdentifier]] = [("", identifier)]
    while pending:
        name, node = pending.pop()
        yield name, node
        for child_name, child in node.children.items():
            items = child if isinstance(child, list) else [child]
            pending.extend((child_name, item) for item in items)


def _validate_variant_target(*, target: object, variant: SamplingVariant) -> None:
    if not isinstance(target, PromptTarget):
        raise ValueError(f"Factory for {variant.registry_name} returned {type(target).__name__}, not a PromptTarget.")
    if not target.capabilities.includes(capability=CapabilityName.MULTI_TURN):
        raise ValueError(f"Adversarial target {variant.registry_name} must support multi_turn.")
    params = target.get_identifier().params
    if variant.parameter.value not in params:
        raise ValueError(
            f"{type(target).__name__} does not report {variant.parameter.value} in its identifier, so the sweep "
            f"cannot verify that {variant.registry_name} applied it."
        )
    reported = params[variant.parameter.value]
    is_number = isinstance(reported, (int, float)) and not isinstance(reported, bool)
    if not is_number or not math.isclose(float(reported), variant.value):
        raise ValueError(
            f"Adversarial target {variant.registry_name} reports {variant.parameter.value}={reported!r}, "
            f"expected {variant.value}."
        )


def _controlled_identity(*, target: PromptTarget, parameter: SamplingParameter) -> tuple[str, dict[str, Any]]:
    identifier = target.get_identifier()
    params = {key: value for key, value in identifier.params.items() if key != parameter.value}
    return f"{identifier.class_module}.{identifier.class_name}", params


def _resolve_technique(technique: str) -> Any:
    from pyrit.scenario.scenarios.benchmark import AdversarialBenchmarkTechnique

    try:
        return AdversarialBenchmarkTechnique(technique)
    except ValueError as error:
        available = sorted(member.value for member in AdversarialBenchmarkTechnique)
        raise ValueError(f"Unknown benchmark technique {technique!r}. Registered techniques: {available}.") from error


def _default_token_reader() -> Callable[[AttackResult], Awaitable[dict[str, int | None]]]:
    from pyrit.memory import CentralMemory

    return make_memory_token_reader(memory=CentralMemory.get_memory_instance())


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _short_hash(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:16]
