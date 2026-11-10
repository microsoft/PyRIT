# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared dataset checks for scenario preparation and read-only assessment."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from pyrit.models.dataset_compatibility import (
    DatasetCompatibility,
    DatasetCompatibilityReason,
    DatasetCompatibilityStatus,
    DatasetRequirementDescription,
    DatasetTechniqueCompatibility,
)
from pyrit.scenario.core.dataset_configuration import DatasetConstraintError
from pyrit.scenario.core.technique_requirements import (
    IncompatibleTechniqueError,
    describe_target_incompatibility,
    prepare_seed_group,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pyrit.models import AttackSeedGroup, AttackTechniqueSeedGroup, SeedDatasetGroupProfile, SeedGroupRequirements


def describe_requirements(requirements: SeedGroupRequirements) -> list[DatasetRequirementDescription]:
    """Return public names and descriptions, never validators or source paths."""
    descriptions = [
        DatasetRequirementDescription(name=item.name, description=item.description) for item in requirements.metadata
    ]
    for enabled, name, description in (
        (requirements.required_user_prompt, "user_prompt", "a stored user prompt"),
        (requirements.required_adversarial_prompt, "adversarial_prompts", "stored adversarial prompts"),
        (requirements.objective_only, "objective_only", "objective-only inputs"),
        (requirements.user_prompts_only, "user_prompts_only", "only static user prompts"),
    ):
        if enabled:
            descriptions.append(DatasetRequirementDescription(name=name, description=description))
    return descriptions


def assess_scenario_groups(
    *, name: str, groups: Sequence[AttackSeedGroup], requirements: SeedGroupRequirements
) -> DatasetCompatibility:
    """
    Check every source group; scenario requirements are strict, not a skip filter.

    Returns:
        DatasetCompatibility: Scenario-level coverage of all source groups.
    """
    failures: Counter[str] = Counter()
    for group in groups:
        failures.update(requirements.check(seed_group=group))
    reasons = [
        DatasetCompatibilityReason(code="scenario_requirement", message=message, count=count)
        for message, count in failures.items()
    ]
    if not groups:
        reasons.append(DatasetCompatibilityReason(code="empty_dataset", message="No attack seed groups remain."))
    return DatasetCompatibility(
        name=name,
        selected=True,
        group_count=len(groups),
        status=DatasetCompatibilityStatus.INCOMPATIBLE if reasons else DatasetCompatibilityStatus.COMPATIBLE,
        reasons=reasons,
    )


def validate_scenario_groups(
    *, population: dict[str, list[AttackSeedGroup]], requirements: SeedGroupRequirements
) -> None:
    """
    Reject malformed scenario data before sampling, regardless of technique policy.

    Raises:
        DatasetConstraintError: If a group fails the scenario contract.
    """
    for name, groups in population.items():
        if not groups:
            continue
        assessment = assess_scenario_groups(name=name, groups=groups, requirements=requirements)
        if assessment.reasons:
            raise DatasetConstraintError(
                f"Dataset '{name}': " + " ".join(reason.message for reason in assessment.reasons)
            )


def assess_technique_groups(
    *,
    name: str,
    groups: Sequence[AttackSeedGroup],
    requirements: SeedGroupRequirements,
    seed_technique: AttackTechniqueSeedGroup | None = None,
    validate_group: Callable[[AttackSeedGroup], None] | None = None,
    is_baseline: bool = False,
    target_errors: Sequence[str] = (),
) -> tuple[DatasetTechniqueCompatibility, list[AttackSeedGroup]]:
    """
    Run the same preparation and prompt-slot checks as execution, without constructing attacks.

    Returns:
        tuple[DatasetTechniqueCompatibility, list[AttackSeedGroup]]: Coverage and original usable source groups.
    """
    usable: list[AttackSeedGroup] = []
    failures: Counter[str] = Counter(
        {describe_target_incompatibility(technique_name=name, errors=target_errors): len(groups)}
        if target_errors
        else {}
    )
    if not target_errors:
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
    return (
        DatasetTechniqueCompatibility(
            technique_name=name,
            is_baseline=is_baseline,
            usable_count=len(usable),
            skipped_count=len(groups) - len(usable),
            reasons=[
                DatasetCompatibilityReason(
                    code="target_requirement" if target_errors else "technique_input",
                    requirement=name,
                    message=message,
                    count=count,
                )
                for message, count in failures.items()
            ],
        ),
        usable,
    )


def assess_candidate_profiles(
    *, name: str, profiles: Sequence[SeedDatasetGroupProfile], requirements: SeedGroupRequirements
) -> DatasetCompatibility:
    """
    Use grouped facts only to disprove compatibility; schema presence cannot prove validity.

    Returns:
        DatasetCompatibility: Negative evidence or an explicitly unverified structural hint.
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
        if requirements.required_adversarial_prompt and not profile.has_adversarial_prompts:
            failures["Stored adversarial prompts are required."] += profile.group_count
        if requirements.objective_only and not requirements.try_adapt and not profile.single_seed:
            failures["Only objective-only inputs are accepted."] += profile.group_count
        if requirements.user_prompts_only and (profile.has_non_user_prompt or profile.has_simulated_conversation):
            failures["Only static user prompts are accepted."] += profile.group_count
    reasons = [
        DatasetCompatibilityReason(code="scenario_requirement", message=message, count=count)
        for message, count in failures.items()
    ]
    if not reasons:
        reasons.append(
            DatasetCompatibilityReason(
                code="not_verified",
                message="Stored structure is a hint only. Load and validate the selected contents.",
            )
        )
    return DatasetCompatibility(
        name=name,
        group_count=sum(profile.group_count for profile in profiles),
        status=DatasetCompatibilityStatus.INCOMPATIBLE if failures else DatasetCompatibilityStatus.NOT_VERIFIED,
        reasons=reasons,
    )
