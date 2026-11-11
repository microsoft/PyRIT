# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Requirements for the dataset input supplied to a technique."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pyrit.models.seeds.attack_seed_group import AttackSeedGroup


@dataclass(frozen=True)
class SeedMetadataRequirement:
    """A schema check owned by its parser; bump ``version`` when its accepted population changes."""

    name: str
    key: str
    description: str
    validator: Callable[[Mapping[str, Any] | None], object]
    version: int = 1


@dataclass(frozen=True)
class SeedGroupRequirements:
    """Dataset input constraints and permission for objective-only adaptation."""

    objective_only: bool = False
    try_adapt: bool = False
    user_prompts_only: bool = False
    required_user_prompt: bool = False
    required_adversarial_prompt: bool = False
    metadata: tuple[SeedMetadataRequirement, ...] = ()

    def __post_init__(self) -> None:
        """
        Reject adaptation permission without an input constraint.

        Raises:
            ValueError: If try_adapt is enabled without objective_only.
        """
        if self.try_adapt and not self.objective_only:
            raise ValueError("try_adapt requires an objective_only input constraint.")

    def check(self, *, seed_group: AttackSeedGroup) -> list[str]:
        """
        Inspect the source group without changing it or applying adaptation.

        Args:
            seed_group (AttackSeedGroup): The incoming dataset group, without technique seeds.

        Returns:
            list[str]: Unmet constraints, including those that could be adapted.
        """
        errors: list[str] = []
        if self.objective_only and len(seed_group.seeds) != 1:
            errors.append(
                "Technique accepts only an objective; dataset prompts and simulated conversations are not accepted."
            )
        if self.user_prompts_only and (
            seed_group.has_simulated_conversation or any(prompt.role != "user" for prompt in seed_group.prompts)
        ):
            errors.append(
                "Technique accepts only an objective and static user prompts; "
                "non-user history and simulated conversations are not accepted."
            )
        if self.required_user_prompt and not any(prompt.role == "user" for prompt in seed_group.prompts):
            errors.append("A stored user prompt is required.")
        if self.required_adversarial_prompt and seed_group.adversarial_prompts is None:
            errors.append("Stored adversarial prompts are required.")
        metadata = seed_group.objective.metadata
        for requirement in self.metadata:
            if metadata is None or requirement.key not in metadata:
                errors.append(f"Requires valid {requirement.description} ({requirement.name}).")
                continue
            try:
                requirement.validator(metadata)
            except ValueError:
                errors.append(f"Requires valid {requirement.description} ({requirement.name}).")
        return errors

    def to_identity(self) -> dict[str, Any]:
        """
        Describe behavioral requirements without serializing executable validators.

        Returns:
            dict[str, Any]: Stable requirement names and schema versions.
        """
        identity: dict[str, Any] = {
            "objective_only": self.objective_only,
            "try_adapt": self.try_adapt,
            "user_prompts_only": self.user_prompts_only,
        }
        if self.required_user_prompt:
            identity["required_user_prompt"] = True
        if self.required_adversarial_prompt:
            identity["required_adversarial_prompt"] = True
        if self.metadata:
            identity["metadata"] = [
                {"name": requirement.name, "key": requirement.key, "version": requirement.version}
                for requirement in self.metadata
            ]
        return identity
