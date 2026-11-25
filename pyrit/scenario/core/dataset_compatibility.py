# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Dataset checks shared by scenario preparation, run-size estimates, and the compatible-dataset listing."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrit.scenario.core.dataset_configuration import DatasetConstraintError
from pyrit.scenario.core.technique_requirements import (
    IncompatibleTechniqueError,
    prepare_seed_group,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pyrit.models import AttackSeedGroup, AttackTechniqueSeedGroup, SeedDatasetGroupProfile, SeedGroupRequirements


@dataclass(frozen=True)
class PopulationCoverage:
    """
    The selected source groups a technique or the baseline can use.

    Groups are identified by object ID: logical IDs can coincide when only metadata differs.
    """

    usable_ids: frozenset[int]
    eligible_ids: frozenset[int]


def validate_scenario_groups(
    *, population: dict[str, list[AttackSeedGroup]], requirements: SeedGroupRequirements
) -> None:
    """
    Reject malformed scenario data before sampling, regardless of technique policy.

    Raises:
        DatasetConstraintError: Naming the first group that fails the scenario contract, and why.
    """
    for name, groups in population.items():
        for group in groups:
            errors = requirements.check(seed_group=group)
            if not errors:
                continue
            details = ""
            for requirement in requirements.metadata:
                try:
                    requirement.validator(group.objective.metadata)
                except ValueError as error:
                    details = f" Details: {error}"
                    break
            raise DatasetConstraintError(
                f"Dataset '{name}' has a seed group that doesn't meet the scenario's requirements "
                f"('{group.objective.value[:80]}'). {' '.join(errors)}{details}"
            )


def assess_technique_groups(
    *,
    name: str,
    groups: Sequence[AttackSeedGroup],
    requirements: SeedGroupRequirements,
    seed_technique: AttackTechniqueSeedGroup | None = None,
    validate_group: Callable[[AttackSeedGroup], None] | None = None,
) -> tuple[list[AttackSeedGroup], list[str]]:
    """
    Run the same preparation and prompt-slot checks as execution, without constructing attacks.

    Returns:
        tuple[list[AttackSeedGroup], list[str]]: The usable original groups, and why the others are skipped.
    """
    usable: list[AttackSeedGroup] = []
    failures: Counter[str] = Counter()
    for group in groups:
        try:
            prepare_seed_group(
                seed_group=group,
                requirements=requirements,
                seed_technique=seed_technique,
                validate_group=validate_group,
            )
        except IncompatibleTechniqueError as error:
            failures[str(error)] += 1
        else:
            usable.append(group)
    return usable, [f"'{name}' skips {count} group(s): {message}" for message, count in failures.items()]


def assess_candidate_profiles(
    *, profiles: Sequence[SeedDatasetGroupProfile], requirements: SeedGroupRequirements
) -> list[str]:
    """
    Use stored structure only to rule a dataset out; it never proves the contents valid.

    Returns:
        list[str]: Why the dataset can't comply, or nothing when its contents still need checking.
    """
    failures: Counter[str] = Counter()
    for profile in profiles:
        if requirements.metadata and not profile.single_objective:
            failures["Each attack seed group needs exactly one objective."] += profile.group_count
        for requirement in requirements.metadata:
            if requirement.key not in profile.metadata_keys:
                failures[f"Requires valid {requirement.description} ({requirement.name})."] += profile.group_count
        if requirements.required_user_prompt and not profile.has_user_prompt:
            failures["A stored user prompt is required."] += profile.group_count
        if requirements.objective_only and not requirements.try_adapt and not profile.single_seed:
            failures["Only objective-only inputs are accepted."] += profile.group_count
        if requirements.user_prompts_only and (profile.has_non_user_prompt or profile.has_simulated_conversation):
            failures["Only static user prompts are accepted."] += profile.group_count
    return [f"{message} ({count} group(s))" for message, count in failures.items()]
