# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

# Runtime-required by Pydantic field / computed-field annotations.
from pyrit.models.identifiers.scenario_identifier import ScenarioIdentifier  # noqa: TC001
from pyrit.models.identifiers.scorer_identifier import ScorerIdentifier  # noqa: TC001
from pyrit.models.identifiers.target_identifier import TargetIdentifier  # noqa: TC001
from pyrit.models.results.attack_result import AttackOutcome, AttackResult

logger = logging.getLogger(__name__)


__all__ = ["ScenarioResult", "ScenarioRunState"]


#: Denormalized identity fields exposed as ``@computed_field`` projections of
#: ``scenario_identifier``. They appear in ``model_dump`` output but are not
#: settable inputs, so they are dropped when reconstructing from a dump.
_COMPUTED_IDENTITY_FIELDS = frozenset(
    {
        "scenario_name",
        "scenario_version",
        "pyrit_version",
        "objective_target_identifier",
        "objective_scorer_identifier",
    }
)


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
    #: Canonical scenario identity for this run. Carries the scenario class name,
    #: definition version, resolved techniques / datasets, the resolved scenario
    #: params, and the ``objective_target`` / ``objective_scorer`` child references.
    #: Its eval hash backs resume drift detection.
    scenario_identifier: ScenarioIdentifier
    #: Human-readable scenario description (the scenario class docstring). Display /
    #: catalog metadata snapshotted on the result — not part of scenario identity.
    scenario_description: str = ""
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

    @model_validator(mode="before")
    @classmethod
    def _drop_computed_identity_fields(cls, data: Any) -> Any:
        """
        Ignore denormalized computed identity fields when reconstructing from a dump.

        ``scenario_name`` / ``scenario_version`` / ``pyrit_version`` /
        ``objective_target_identifier`` / ``objective_scorer_identifier`` are
        ``@computed_field`` projections of ``scenario_identifier`` that show up in
        ``model_dump`` output but are not settable inputs. Dropping them lets
        ``model_validate(model_dump(...))`` round-trip under ``extra="forbid"``.

        Args:
            data (Any): Raw input passed to validation (a dict when reconstructing from a dump).

        Returns:
            Any: The input with computed identity keys removed when it is a dict; otherwise unchanged.
        """
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if key not in _COMPUTED_IDENTITY_FIELDS}
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scenario_name(self) -> str:
        """Scenario class name (e.g. ``"ContentHarms"``), delegated to the identifier."""
        return self.scenario_identifier.class_name

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scenario_version(self) -> int:
        """Scenario definition version, delegated to the identifier (defaults to 1)."""
        version = self.scenario_identifier.version
        return version if version is not None else 1

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pyrit_version(self) -> str:
        """PyRIT version the scenario ran under, delegated to the identifier."""
        return self.scenario_identifier.pyrit_version

    @computed_field  # type: ignore[prop-decorator]
    @property
    def objective_target_identifier(self) -> TargetIdentifier | None:
        """Target the scenario attacks, delegated to the identifier."""
        return self.scenario_identifier.objective_target

    @computed_field  # type: ignore[prop-decorator]
    @property
    def objective_scorer_identifier(self) -> ScorerIdentifier | None:
        """Primary scorer the scenario evaluates with, delegated to the identifier."""
        return self.scenario_identifier.objective_scorer

    def get_techniques_used(self) -> list[str]:
        """
        Get the list of techniques used in this scenario.

        Returns:
            list[str]: Atomic attack technique names present in the results.

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
        techniques_to_process: list[list[AttackResult]]

        if not atomic_attack_name:
            # Include all atomic attacks
            techniques_to_process = list(self.attack_results.values())
        else:
            # Include only specified atomic attack
            if atomic_attack_name in self.attack_results:
                techniques_to_process = [self.attack_results[atomic_attack_name]]
            else:
                techniques_to_process = []

        for results in techniques_to_process:
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

    @staticmethod
    def normalize_scenario_name(scenario_name: str) -> str:
        """
        Normalize a scenario name to match the stored class name format.

        Converts CLI-style snake_case names (e.g., "foundry" or "content_harms") to
        PascalCase class names (e.g., "Foundry" or "ContentHarms") for database queries.
        If the input is already in PascalCase or doesn't match the snake_case pattern,
        it is returned unchanged.

        This is the inverse of the snake_case registry-name conversion
        (``class_name_to_snake_case``) applied to scenario class names during
        discovery.

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
