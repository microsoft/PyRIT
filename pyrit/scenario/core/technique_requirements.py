# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared compatibility checks and input preparation for attack techniques."""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from pyrit.models import AttackSeedGroup, DatasetExampleSelectionError, SeedGroupRequirements
from pyrit.prompt_target import TargetRequirements
from pyrit.score import ScorerRequirements

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Mapping, Sequence

    from pyrit.executor.attack import AttackStrategy
    from pyrit.models import AttackTechniqueSeedGroup
    from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

#: While a scenario builds attacks for an exact example selection, the selection key and example id
#: of each selected seed, keyed by its dataset name (None when unnamed) and seed id.
#: Set by ``ExactExampleDatasetConfiguration._attack_building_scope``.
_exact_examples: ContextVar[Mapping[tuple[str | None, uuid.UUID], tuple[str, uuid.UUID | None]] | None] = ContextVar(
    "exact_examples", default=None
)


class IncompatibleTechniquePolicy(str, Enum):
    """Scenario action when a technique cannot use a target or dataset group."""

    SKIP = "skip"
    RAISE = "raise"


class IncompatibleTechniqueError(ValueError):
    """A known target, dataset-input, or seed-composition mismatch."""


@dataclass(frozen=True)
class TechniqueRequirements:
    """
    The objective-target, dataset, and objective-scorer requirements declared by a technique.

    ``objective_scorer`` states algorithm needs only, such as float feedback for ranking.
    Reusable techniques never declare a scenario-specific evaluation.
    """

    objective_target: TargetRequirements = field(default_factory=TargetRequirements)
    seed_group: SeedGroupRequirements = field(default_factory=SeedGroupRequirements)
    objective_scorer: ScorerRequirements = field(default_factory=ScorerRequirements)

    def __post_init__(self) -> None:
        """
        Reject a scenario-specific evaluation on a reusable technique.

        Raises:
            TypeError: If ``objective_scorer`` is not ``ScorerRequirements``.
            ValueError: If it names an evaluation.
        """
        if not isinstance(self.objective_scorer, ScorerRequirements):
            raise TypeError("TechniqueRequirements.objective_scorer must be ScorerRequirements.")
        if self.objective_scorer.evaluation is not None:
            raise ValueError(
                "Technique requirements declare algorithm needs, not an evaluation. "
                "Put the evaluation on the scenario or the dataset source."
            )

    def objective_scorer_for(self, *, attack_class: type[AttackStrategy[Any, Any]]) -> ScorerRequirements:
        """
        Combine the attack class's scorer needs with the technique's own.

        Args:
            attack_class (type[AttackStrategy]): The technique's attack class.

        Returns:
            ScorerRequirements: What any objective scorer for this technique must provide.
        """
        return ScorerRequirements.combine(attack_class.OBJECTIVE_SCORER_REQUIREMENTS, self.objective_scorer)

    @property
    def adaptation(self) -> str | None:
        """The permitted execution-input transformation, if any."""
        return "objective_only" if self.seed_group.try_adapt else None

    def check_target(self, *, target: PromptTarget, attack_class: type[AttackStrategy[Any, Any]]) -> list[str]:
        """
        Check both the attack contract and the technique's declared contract.

        Args:
            target (PromptTarget): The objective target.
            attack_class (type[AttackStrategy]): The technique's attack class.

        Returns:
            list[str]: All unmet target requirements, without flattening subclass rules.
        """
        return [
            *attack_class.TARGET_REQUIREMENTS.check(target=target),
            *self.objective_target.check(target=target),
        ]

    def validate_target(self, *, target: PromptTarget, attack_class: type[AttackStrategy[Any, Any]]) -> None:
        """
        Require both target contracts to pass.

        Args:
            target (PromptTarget): The objective target.
            attack_class (type[AttackStrategy]): The technique's attack class.

        Raises:
            IncompatibleTechniqueError: If either target contract cannot be met.
        """
        errors = self.check_target(target=target, attack_class=attack_class)
        if errors:
            raise IncompatibleTechniqueError("\n".join(errors))


@dataclass(frozen=True)
class PreparedSeedGroup:
    """An execution copy and the transformation applied to its dataset input."""

    seed_group: AttackSeedGroup
    adaptation: str | None = None


def prepare_seed_group(
    *,
    seed_group: AttackSeedGroup,
    requirements: SeedGroupRequirements,
    seed_technique: AttackTechniqueSeedGroup | None = None,
    validate_group: Callable[[AttackSeedGroup], None] | None = None,
) -> PreparedSeedGroup:
    """
    Check dataset input, apply permitted adaptation, then validate the full merge.

    Args:
        seed_group (AttackSeedGroup): The original dataset group.
        requirements (SeedGroupRequirements): Constraints on the dataset input only.
        seed_technique (AttackTechniqueSeedGroup | None): Technique-owned seeds to compose.
        validate_group (Callable | None): Prompt-binding checks on the prepared execution group.

    Returns:
        PreparedSeedGroup: A separate execution group and any applied adaptation.

    Raises:
        IncompatibleTechniqueError: If input requirements or composition cannot be met.
    """
    errors = requirements.check(seed_group=seed_group)
    adaptation = None
    if errors and requirements.try_adapt:
        objective = seed_group.objective.model_copy(deep=True)
        objective.harm_categories = sorted(seed_group.harm_categories)
        execution_group = AttackSeedGroup(seeds=[objective])
        adaptation = "objective_only"
        errors = requirements.check(seed_group=execution_group)
    else:
        execution_group = seed_group.model_copy(deep=True)
    if errors:
        raise IncompatibleTechniqueError("\n".join(errors))

    if seed_technique is not None:
        try:
            execution_group = execution_group.with_technique(technique=seed_technique)
        except ValueError as exc:
            raise IncompatibleTechniqueError(f"Technique seeds cannot be composed with dataset input: {exc}") from exc
    if validate_group is not None:
        validate_group(execution_group)
    return PreparedSeedGroup(seed_group=execution_group, adaptation=adaptation)


def require_unchanged_seed_group(
    *,
    seed_group: AttackSeedGroup,
    requirements: SeedGroupRequirements,
    seed_technique: AttackTechniqueSeedGroup | None = None,
    validate_group: Callable[[AttackSeedGroup], None] | None = None,
) -> None:
    """
    Require a group to run exactly as given, with no skip and no adaptation.

    Applies the same preparation as ``filter_seed_groups``. A group that would be
    skipped, or would run only after objective-only adaptation, fails. Callers that
    must keep a population unchanged, such as an exact example selection, use this
    instead of the skip policy.

    Args:
        seed_group (AttackSeedGroup): The original dataset group.
        requirements (SeedGroupRequirements): Dataset input constraints.
        seed_technique (AttackTechniqueSeedGroup | None): Technique-owned seeds to compose.
        validate_group (Callable | None): Additional technique input validation on the prepared group.

    Raises:
        IncompatibleTechniqueError: If the group would be skipped or adapted.
    """
    prepared = prepare_seed_group(seed_group=seed_group, requirements=requirements, seed_technique=seed_technique)
    if validate_group is not None:
        validate_group(prepared.seed_group)
    if prepared.adaptation is not None:
        raise IncompatibleTechniqueError(
            f"Technique would run this group only after {prepared.adaptation} adaptation, "
            "which drops its dataset prompts."
        )


def describe_target_incompatibility(*, technique_name: str, errors: Sequence[str]) -> str:
    """
    Format a known target mismatch consistently for assessment and execution.

    Returns:
        str: The existing technique-scoped diagnostic.
    """
    return f"Technique '{technique_name}' is incompatible with the objective target:\n" + "\n".join(errors)


def check_target_compatibility(
    *,
    target: PromptTarget,
    attack_class: type[AttackStrategy[Any, Any]],
    requirements: TechniqueRequirements,
    technique_name: str,
    policy: IncompatibleTechniquePolicy,
) -> bool:
    """
    Apply scenario policy to known target mismatches, before attack construction.

    Args:
        target (PromptTarget): The objective target.
        attack_class (type[AttackStrategy]): The attack to construct.
        requirements (TechniqueRequirements): The declared technique requirements.
        technique_name (str): Name used in diagnostics.
        policy (IncompatibleTechniquePolicy): Skip or raise on incompatibility.

    Returns:
        bool: Whether the target contracts pass.

    Raises:
        IncompatibleTechniqueError: If the target is incompatible under RAISE.
    """
    errors = requirements.check_target(target=target, attack_class=attack_class)
    if not errors:
        return True
    message = describe_target_incompatibility(technique_name=technique_name, errors=errors)
    if policy is IncompatibleTechniquePolicy.RAISE:
        raise IncompatibleTechniqueError(message)
    logger.warning("Skipping %s", message)
    return False


def filter_seed_groups(
    *,
    seed_groups: Sequence[AttackSeedGroup],
    requirements: SeedGroupRequirements,
    seed_technique: AttackTechniqueSeedGroup | None,
    technique_name: str,
    dataset_name: str,
    policy: IncompatibleTechniquePolicy,
    validate_group: Callable[[AttackSeedGroup], None] | None = None,
) -> list[AttackSeedGroup]:
    """
    Retain source groups that can be prepared, with one summary per combination.

    While a scenario builds attacks for an exact example selection, a group that would be
    skipped or adapted raises ``DatasetExampleSelectionError`` instead, whatever the policy,
    so the selected population runs unchanged or not at all.

    Args:
        seed_groups (Sequence[AttackSeedGroup]): Original groups from one dataset.
        requirements (SeedGroupRequirements): Dataset input constraints.
        seed_technique (AttackTechniqueSeedGroup | None): Seeds supplied by the technique.
        technique_name (str): Technique name for diagnostics.
        dataset_name (str): Dataset name for diagnostics.
        policy (IncompatibleTechniquePolicy): Skip or raise on incompatibility.
        validate_group (Callable | None): Additional technique input validation on the prepared group.

    Returns:
        list[AttackSeedGroup]: Compatible original groups, not their execution copies.

    Raises:
        DatasetExampleSelectionError: If a selected example of an exact selection would be skipped or adapted.
        IncompatibleTechniqueError: If any group is incompatible under RAISE.
    """
    exact_examples = _exact_examples.get()
    if exact_examples is not None:
        for seed_group in seed_groups:
            try:
                require_unchanged_seed_group(
                    seed_group=seed_group,
                    requirements=requirements,
                    seed_technique=seed_technique,
                    validate_group=validate_group,
                )
            except IncompatibleTechniqueError as exc:
                from pyrit.scenario.core.dataset_configuration import _public_group_build_reason

                selection_key, example_id = _selected_example(seed_group=seed_group, examples=exact_examples)
                raise DatasetExampleSelectionError(
                    code="unsupported",
                    selection_key=selection_key,
                    example_id=example_id,
                    detail=(
                        f"Technique '{technique_name}' cannot run this example unchanged: "
                        f"{_public_group_build_reason(exc)}"
                    ),
                ) from exc
        return list(seed_groups)
    retained: list[AttackSeedGroup] = []
    failures: dict[str, int] = {}
    adapted_count = 0
    for seed_group in seed_groups:
        try:
            prepared = prepare_seed_group(
                seed_group=seed_group,
                requirements=requirements,
                seed_technique=seed_technique,
                validate_group=validate_group,
            )
        except IncompatibleTechniqueError as exc:
            if policy is IncompatibleTechniquePolicy.RAISE:
                raise IncompatibleTechniqueError(
                    f"Technique '{technique_name}', dataset '{dataset_name}', "
                    f"seed group '{seed_group.logical_id}': {exc}"
                ) from exc
            reason = str(exc)
            failures[reason] = failures.get(reason, 0) + 1
            continue
        retained.append(seed_group)
        adapted_count += prepared.adaptation is not None
    if failures or adapted_count:
        logger.warning(
            "Technique '%s', dataset '%s': skipped %d seed group(s); "
            "%d require objective-only adaptation for execution.%s",
            technique_name,
            dataset_name,
            sum(failures.values()),
            adapted_count,
            f" Reasons: {failures}" if failures else "",
        )
    return retained


def _selected_example(
    *,
    seed_group: AttackSeedGroup,
    examples: Mapping[tuple[str | None, uuid.UUID], tuple[str, uuid.UUID | None]],
) -> tuple[str | None, uuid.UUID | None]:
    """
    Find the selection key and example id of the selected example a group was built from.

    Seed ids are matched within the seed's own dataset, because example ids are unique only within one dataset.

    Args:
        seed_group (AttackSeedGroup): A group of the exact population.
        examples (Mapping[tuple[str | None, uuid.UUID], tuple[str, uuid.UUID | None]]): The selected
            seeds' selection keys and example ids, by dataset name and seed id.

    Returns:
        tuple[str | None, uuid.UUID | None]: The selection key and example id, or Nones if no seed was selected.
    """
    return next(
        (
            examples[(seed.dataset_name or None, seed.id)]
            for seed in seed_group.seeds
            if seed.id is not None and (seed.dataset_name or None, seed.id) in examples
        ),
        (None, None),
    )
