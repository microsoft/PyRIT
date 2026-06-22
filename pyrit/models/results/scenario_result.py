# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

import pyrit
from pyrit.common.deprecation import print_deprecation_message
from pyrit.models.identifiers.component_identifier import (  # noqa: TC001  (runtime-required by Pydantic field annotations)
    ComponentIdentifier,
)
from pyrit.models.results.attack_result import AttackOutcome, AttackResult

logger = logging.getLogger(__name__)


class ScenarioIdentifier(BaseModel):
    """
    Identifier describing the executed scenario.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    #: Name of the scenario.
    name: str
    #: Description of the scenario.
    description: str = ""
    #: Version of the scenario. Accepts the legacy ``scenario_version`` kwarg/wire key.
    version: int = Field(default=1, alias="scenario_version")
    #: PyRIT version string. Defaults to the current installed version.
    pyrit_version: str = Field(default=pyrit.__version__)
    #: Optional initialization data.
    init_data: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize to a JSON-compatible dictionary.

        Deprecated: use ``model_dump(by_alias=True)`` instead.

        Returns:
            dict[str, Any]: Serialized payload.
        """
        print_deprecation_message(
            old_item="ScenarioIdentifier.to_dict()",
            new_item="ScenarioIdentifier.model_dump(by_alias=True)",
            removed_in="0.16.0",
        )
        return self.model_dump(by_alias=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScenarioIdentifier:
        """
        Reconstruct a ScenarioIdentifier from a dictionary.

        Deprecated: use ``model_validate(...)`` instead.

        Args:
            data (dict[str, Any]): Dictionary as produced by ``model_dump(by_alias=True)``.

        Returns:
            ScenarioIdentifier: Reconstructed instance.
        """
        print_deprecation_message(
            old_item="ScenarioIdentifier.from_dict(...)",
            new_item="ScenarioIdentifier.model_validate(...)",
            removed_in="0.16.0",
        )
        return cls.model_validate(data)


class ScenarioRunState(str, Enum):
    """
    Lifecycle state of a scenario run.

    Inherits from ``str`` so values serialize naturally in Pydantic models and
    REST responses, and compare equal to their string form.
    """

    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ScenarioResult(BaseModel):
    """
    Scenario result class for aggregating scenario results.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        validate_assignment=False,
    )

    #: Scenario result ID.
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    #: Identifier for the executed scenario.
    scenario_identifier: ScenarioIdentifier
    #: Target identifier.
    objective_target_identifier: ComponentIdentifier | None
    #: Objective scorer identifier, or None if the scenario has no objective scorer.
    objective_scorer_identifier: ComponentIdentifier | None
    #: Results grouped by atomic attack name.
    attack_results: dict[str, list[AttackResult]]
    #: Current scenario run state.
    scenario_run_state: ScenarioRunState = ScenarioRunState.CREATED
    #: Optional labels.
    labels: dict[str, str] = Field(default_factory=dict)
    #: When the scenario result was created.
    creation_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    #: Optional completion timestamp.
    completion_time: datetime | None = Field(default_factory=lambda: datetime.now(timezone.utc))
    #: Number of run attempts.
    number_tries: int = 0
    #: Mapping of ``atomic_attack_name`` -> display group label. Used by the console
    #: printer to aggregate results for user-facing output.
    display_group_map: dict[str, str] = Field(default_factory=dict)
    #: Scenario-level error message when the run fails.
    error_message: str | None = None
    #: Exception class name when the run fails.
    error_type: str | None = None
    #: IDs of attack results that errored during the scenario run.
    error_attack_result_ids: list[str] = Field(default_factory=list)
    #: Free-form JSON metadata persisted with the scenario result. Currently used to record
    #: ``objective_hashes`` — the objective ``sha256`` set chosen on the first run, replayed
    #: on resume so a fresh ``random.sample`` can't silently change which objectives the
    #: scenario operates on. Keys are not part of any public contract and may evolve.
    metadata: dict[str, Any] = Field(default_factory=dict)

    def get_strategies_used(self) -> list[str]:
        """
        Get the list of strategies used in this scenario.

        Returns:
            list[str]: Atomic attack strategy names present in the results.

        """
        return list(self.attack_results.keys())

    def get_display_groups(self) -> dict[str, list[AttackResult]]:
        """
        Aggregate attack results by display group.

        When a ``display_group_map`` was provided, results from multiple
        ``atomic_attack_name`` keys that share the same display group are
        merged into a single list. When no map was provided, this returns
        the same structure as ``attack_results`` (identity mapping).

        Returns:
            dict[str, list[AttackResult]]: Results grouped by display label.
        """
        if not self.display_group_map:
            return dict(self.attack_results)

        grouped: dict[str, list[AttackResult]] = {}
        for attack_name, results in self.attack_results.items():
            group = self.display_group_map.get(attack_name, attack_name)
            grouped.setdefault(group, []).extend(results)
        return grouped

    def get_objectives(self, *, atomic_attack_name: str | None = None) -> list[str]:
        """
        Get the list of unique objectives for this scenario.

        Args:
            atomic_attack_name (str | None): Name of specific atomic attack to include.
                If None, includes objectives from all atomic attacks. Defaults to None.

        Returns:
            list[str]: Deduplicated list of objectives.

        """
        objectives: list[str] = []
        strategies_to_process: list[list[AttackResult]]

        if not atomic_attack_name:
            # Include all atomic attacks
            strategies_to_process = list(self.attack_results.values())
        else:
            # Include only specified atomic attack
            if atomic_attack_name in self.attack_results:
                strategies_to_process = [self.attack_results[atomic_attack_name]]
            else:
                strategies_to_process = []

        for results in strategies_to_process:
            objectives.extend(result.objective for result in results)

        return list(set(objectives))

    def objective_achieved_rate(self, *, atomic_attack_name: str | None = None) -> int:
        """
        Get the success rate of this scenario.

        Args:
            atomic_attack_name (str | None): Name of specific atomic attack to calculate rate for.
                If None, calculates rate across all atomic attacks. Defaults to None.

        Returns:
            int: Success rate as a percentage (0-100).

        """
        if not atomic_attack_name:
            # Calculate rate across all atomic attacks
            all_results = []
            for results in self.attack_results.values():
                all_results.extend(results)
        else:
            # Calculate rate for specific atomic attack
            if atomic_attack_name in self.attack_results:
                all_results = self.attack_results[atomic_attack_name]
            else:
                return 0

        total_results = len(all_results)
        if total_results == 0:
            return 0

        successful_results = sum(1 for result in all_results if result.outcome == AttackOutcome.SUCCESS)
        return int((successful_results / total_results) * 100)

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this scenario result to a JSON-compatible dictionary.

        Deprecated: use ``model_dump(mode="json", by_alias=True)`` instead.

        Returns:
            dict[str, Any]: Serialized payload suitable for REST APIs or persistence.
        """
        print_deprecation_message(
            old_item="ScenarioResult.to_dict()",
            new_item="ScenarioResult.model_dump(mode='json', by_alias=True)",
            removed_in="0.16.0",
        )
        return self.model_dump(mode="json", by_alias=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScenarioResult:
        """
        Reconstruct a ScenarioResult from a dictionary.

        Deprecated: use ``model_validate(...)`` instead.

        Args:
            data (dict[str, Any]): Dictionary as produced by ``model_dump(mode="json")``.

        Returns:
            ScenarioResult: Reconstructed instance.
        """
        print_deprecation_message(
            old_item="ScenarioResult.from_dict(...)",
            new_item="ScenarioResult.model_validate(...)",
            removed_in="0.16.0",
        )
        return cls.model_validate(data)

    @staticmethod
    def normalize_scenario_name(scenario_name: str) -> str:
        """
        Normalize a scenario name to match the stored class name format.

        Converts CLI-style snake_case names (e.g., "foundry" or "content_harms") to
        PascalCase class names (e.g., "Foundry" or "ContentHarms") for database queries.
        If the input is already in PascalCase or doesn't match the snake_case pattern,
        it is returned unchanged.

        This is the inverse of ScenarioRegistry._class_name_to_scenario_name().

        Args:
            scenario_name (str): The scenario name to normalize.

        Returns:
            str: The normalized scenario name suitable for database queries.

        """
        # Check if it looks like snake_case (contains underscore and is lowercase)
        if "_" in scenario_name and scenario_name == scenario_name.lower():
            # Convert snake_case to PascalCase
            # e.g., "content_harms" -> "ContentHarms"
            parts = scenario_name.split("_")
            return "".join(part.capitalize() for part in parts)
        # Already PascalCase or other format, return as-is
        return scenario_name
