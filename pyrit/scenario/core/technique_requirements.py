# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared compatibility checks and input preparation for attack techniques."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from pyrit.models import AttackSeedGroup, SeedGroupRequirements
from pyrit.prompt_target import TargetRequirements

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.executor.attack import AttackStrategy
    from pyrit.models import AttackTechniqueSeedGroup
    from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class IncompatibleTechniquePolicy(str, Enum):
    """Scenario action when a technique cannot use a target or dataset group."""

    SKIP = "skip"
    RAISE = "raise"


class IncompatibleTechniqueError(ValueError):
    """A known target, dataset-input, or seed-composition mismatch."""


@dataclass(frozen=True)
class TechniqueRequirements:
    """The objective-target and dataset requirements declared by a technique."""

    objective_target: TargetRequirements = field(default_factory=TargetRequirements)
    seed_group: SeedGroupRequirements = field(default_factory=SeedGroupRequirements)

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
) -> PreparedSeedGroup:
    """
    Check dataset input, apply permitted adaptation, then validate the full merge.

    Args:
        seed_group (AttackSeedGroup): The original dataset group.
        requirements (SeedGroupRequirements): Constraints on the dataset input only.
        seed_technique (AttackTechniqueSeedGroup | None): Technique-owned seeds to compose.

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
    return PreparedSeedGroup(seed_group=execution_group, adaptation=adaptation)


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
    message = f"Technique '{technique_name}' is incompatible with the objective target:\n" + "\n".join(errors)
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
) -> list[AttackSeedGroup]:
    """
    Retain source groups that can be prepared, with one summary per combination.

    Args:
        seed_groups (Sequence[AttackSeedGroup]): Original groups from one dataset.
        requirements (SeedGroupRequirements): Dataset input constraints.
        seed_technique (AttackTechniqueSeedGroup | None): Seeds supplied by the technique.
        technique_name (str): Technique name for diagnostics.
        dataset_name (str): Dataset name for diagnostics.
        policy (IncompatibleTechniquePolicy): Skip or raise on incompatibility.

    Returns:
        list[AttackSeedGroup]: Compatible original groups, not their execution copies.

    Raises:
        IncompatibleTechniqueError: If any group is incompatible under RAISE.
    """
    retained: list[AttackSeedGroup] = []
    failures: dict[str, int] = {}
    adapted_count = 0
    for seed_group in seed_groups:
        try:
            prepared = prepare_seed_group(
                seed_group=seed_group, requirements=requirements, seed_technique=seed_technique
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
