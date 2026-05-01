# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, Optional, Union

import pyrit
from pyrit.models import AttackOutcome, AttackResult

if TYPE_CHECKING:
    from pyrit.identifiers.component_identifier import ComponentIdentifier
    from pyrit.score.scorer_evaluation.scorer_metrics import ScorerMetrics

logger = logging.getLogger(__name__)


class ScenarioIdentifier:
    """
    Scenario result class for aggregating results from multiple AtomicAttacks.
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        scenario_version: int = 1,
        init_data: Optional[dict[str, Any]] = None,
        pyrit_version: Optional[str] = None,
    ) -> None:
        """
        Initialize a ScenarioIdentifier.

        Args:
            name (str): Name of the scenario.
            description (str): Description of the scenario.
            scenario_version (int): Version of the scenario.
            init_data (Optional[dict]): Initialization data.
            pyrit_version (Optional[str]): PyRIT version string. If None, uses current version.

        """
        self.name = name
        self.description = description
        self.version = scenario_version
        self.pyrit_version = pyrit_version if pyrit_version is not None else pyrit.__version__
        self.init_data = init_data


ScenarioRunState = Literal["CREATED", "IN_PROGRESS", "COMPLETED", "FAILED"]


class ScenarioResult:
    """
    Scenario result class for aggregating scenario results.
    """

    def __init__(
        self,
        *,
        scenario_identifier: ScenarioIdentifier,
        objective_target_identifier: Union[dict[str, Any], "ComponentIdentifier"],
        attack_results: dict[str, list[AttackResult]],
        objective_scorer_identifier: Union[dict[str, Any], "ComponentIdentifier"],
        scenario_run_state: ScenarioRunState = "CREATED",
        labels: Optional[dict[str, str]] = None,
        completion_time: Optional[datetime] = None,
        number_tries: int = 0,
        id: Optional[uuid.UUID] = None,  # noqa: A002
        display_group_map: Optional[dict[str, str]] = None,
    ) -> None:
        """
        Initialize a scenario result.

        Args:
            scenario_identifier (ScenarioIdentifier): Identifier for the executed scenario.
            objective_target_identifier (Union[Dict[str, Any], TargetIdentifier]): Target identifier.
            attack_results (dict[str, List[AttackResult]]): Results grouped by atomic attack name.
            objective_scorer_identifier (Union[Dict[str, Any], ScorerIdentifier]): Objective scorer identifier.
            scenario_run_state (ScenarioRunState): Current scenario run state.
            labels (Optional[dict[str, str]]): Optional labels.
            completion_time (Optional[datetime]): Optional completion timestamp.
            number_tries (int): Number of run attempts.
            id (Optional[uuid.UUID]): Optional scenario result ID.
            display_group_map (Optional[dict[str, str]]): Optional mapping of
                atomic_attack_name → display group label. Used by the console
                printer to aggregate results for user-facing output.

        """
        from pyrit.identifiers.component_identifier import ComponentIdentifier

        self.id = id if id is not None else uuid.uuid4()
        self.scenario_identifier = scenario_identifier

        # Normalize objective_target_identifier to ComponentIdentifier
        self.objective_target_identifier = ComponentIdentifier.normalize(objective_target_identifier)

        self.objective_scorer_identifier = ComponentIdentifier.normalize(objective_scorer_identifier)

        self.scenario_run_state = scenario_run_state
        self.attack_results = attack_results
        self.labels = labels if labels is not None else {}
        self.completion_time = completion_time if completion_time is not None else datetime.now(timezone.utc)
        self.number_tries = number_tries
        self._display_group_map = display_group_map or {}

    def get_strategies_used(self) -> list[str]:
        """
        Get the list of strategies used in this scenario.

        Returns:
            List[str]: Atomic attack strategy names present in the results.

        """
        return list(self.attack_results.keys())

    def get_display_groups(self) -> dict[str, list[AttackResult]]:
        """
        Aggregate attack results by display group.

        When a ``display_group_map`` was provided, results from multiple
        ``atomic_attack_name`` keys that share the same display group are
        merged into a single list.  When no map was provided, this returns
        the same structure as ``attack_results`` (identity mapping).

        Returns:
            dict[str, list[AttackResult]]: Results grouped by display label.
        """
        if not self._display_group_map:
            return dict(self.attack_results)

        grouped: dict[str, list[AttackResult]] = {}
        for attack_name, results in self.attack_results.items():
            group = self._display_group_map.get(attack_name, attack_name)
            grouped.setdefault(group, []).extend(results)
        return grouped

    def get_objectives(self, *, atomic_attack_name: Optional[str] = None) -> list[str]:
        """
        Get the list of unique objectives for this scenario.

        Args:
            atomic_attack_name (Optional[str]): Name of specific atomic attack to include.
                If None, includes objectives from all atomic attacks. Defaults to None.

        Returns:
            List[str]: Deduplicated list of objectives.

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

    def objective_achieved_rate(self, *, atomic_attack_name: Optional[str] = None) -> int:
        """
        Get the success rate of this scenario.

        Args:
            atomic_attack_name (Optional[str]): Name of specific atomic attack to calculate rate for.
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

        This is the inverse of ScenarioRegistry._class_name_to_scenario_name().

        Args:
            scenario_name: The scenario name to normalize.

        Returns:
            The normalized scenario name suitable for database queries.

        """
        # Check if it looks like snake_case (contains underscore and is lowercase)
        if "_" in scenario_name and scenario_name == scenario_name.lower():
            # Convert snake_case to PascalCase
            # e.g., "content_harms" -> "ContentHarms"
            parts = scenario_name.split("_")
            return "".join(part.capitalize() for part in parts)
        # Already PascalCase or other format, return as-is
        return scenario_name

    def get_scorer_evaluation_metrics(self) -> Optional["ScorerMetrics"]:
        """
        Get the evaluation metrics for the scenario's scorer from the scorer evaluation registry.

        Returns:
            ScorerMetrics: The evaluation metrics object, or None if not found.

        """
        # import here to avoid circular imports
        from pyrit.identifiers.evaluation_identifier import ScorerEvaluationIdentifier
        from pyrit.score.scorer_evaluation.scorer_metrics_io import (
            find_objective_metrics_by_eval_hash,
        )

        if not self.objective_scorer_identifier:
            return None

        eval_hash = ScorerEvaluationIdentifier(self.objective_scorer_identifier).eval_hash

        return find_objective_metrics_by_eval_hash(eval_hash=eval_hash)
