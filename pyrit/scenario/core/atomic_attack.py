# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AtomicAttack class for executing single attack configurations against datasets.

This module provides the AtomicAttack class that represents an atomic test combining
an attack, a dataset, and execution parameters. Multiple AtomicAttacks can be grouped
together into larger test scenarios for comprehensive security testing.

Eventually it's a good goal to unify attacks as much as we can. But there are
times when that may not be possible or make sense. So this class exists to
have a common interface for scenarios.
"""

import logging
import warnings
from typing import TYPE_CHECKING, Any, Optional

from pyrit.executor.attack import AttackExecutor, AttackStrategy
from pyrit.executor.attack.core.attack_executor import AttackExecutorResult
from pyrit.identifiers import build_atomic_attack_identifier
from pyrit.identifiers.evaluation_identifier import AtomicAttackEvaluationIdentifier
from pyrit.memory import CentralMemory
from pyrit.memory.memory_models import MAX_IDENTIFIER_VALUE_LENGTH
from pyrit.models import AttackResult, SeedAttackGroup
from pyrit.scenario.core.attack_technique import AttackTechnique

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptChatTarget
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


class AtomicAttack:
    """
    Represents a single atomic attack test combining an attack strategy and dataset.

    An AtomicAttack is an executable unit that executes a configured attack against
    all objectives in a dataset. Multiple AtomicAttacks can be grouped together into
    larger test scenarios for comprehensive security testing and evaluation.

    The AtomicAttack uses SeedAttackGroups as the single source of truth for objectives,
    prepended conversations, and next messages. Each SeedAttackGroup must have an objective set.

    An ``AttackTechnique`` bundles the attack strategy with an optional
    ``SeedAttackTechniqueGroup``, cleanly separating "how to attack" from
    "what to attack" (the objective).
    """

    def __init__(
        self,
        *,
        atomic_attack_name: str,
        display_group: str | None = None,
        attack_technique: AttackTechnique | None = None,
        attack: AttackStrategy[Any, Any] | None = None,
        seed_groups: list[SeedAttackGroup],
        adversarial_chat: Optional["PromptChatTarget"] = None,
        objective_scorer: Optional["TrueFalseScorer"] = None,
        memory_labels: Optional[dict[str, str]] = None,
        **attack_execute_params: Any,
    ) -> None:
        """
        Initialize an atomic attack with an attack strategy and seed groups.

        Args:
            atomic_attack_name: Unique key for this atomic attack.  Used for
                resume tracking and result persistence — must be unique across
                all ``AtomicAttack`` instances in a scenario.
            display_group: Optional label for grouping results in user-facing
                output (console printer, reports).  When ``None``, falls back
                to ``atomic_attack_name``.
            attack_technique: An AttackTechnique bundling the attack strategy and optional
                technique seeds. Preferred over the deprecated ``attack`` parameter.
            attack: Deprecated. The configured attack strategy to execute. Use
                ``attack_technique`` instead.
            seed_groups: List of seed attack groups. Each seed group must
                have an objective set.
            adversarial_chat: Optional chat target for generating
                adversarial prompts or simulated conversations.
            objective_scorer: Optional scorer for evaluating simulated
                conversations.
            memory_labels: Additional labels to apply to prompts.
            **attack_execute_params: Additional parameters to pass to the attack
                execution method.

        Raises:
            ValueError: If seed_groups list is empty or any seed group is missing an objective.
            ValueError: If neither attack_technique nor attack is provided, or both are provided.
        """
        self.atomic_attack_name = atomic_attack_name
        self.display_group = display_group or atomic_attack_name

        if attack_technique is not None and attack is not None:
            raise ValueError("Provide either attack_technique or attack, not both.")

        if attack_technique is not None:
            self._attack_technique = attack_technique
        elif attack is not None:
            warnings.warn(
                "The 'attack' parameter is deprecated. Use 'attack_technique=AttackTechnique(attack=...)' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            self._attack_technique = AttackTechnique(attack=attack)
        else:
            raise ValueError("Either attack_technique or attack must be provided.")

        # Validate seed_groups
        if not seed_groups:
            raise ValueError("seed_groups list cannot be empty")

        # Validate each seed group to ensure they are in a valid state
        for sg in seed_groups:
            sg.validate()

        self._seed_groups = seed_groups
        self._adversarial_chat = adversarial_chat
        self._objective_scorer = objective_scorer
        self._memory_labels = memory_labels or {}
        self._attack_execute_params = attack_execute_params

        logger.info(
            f"Initialized atomic attack with {len(self._seed_groups)} seed groups, "
            f"attack type: {type(self._attack_technique.attack).__name__}"
        )

    @property
    def attack_technique(self) -> AttackTechnique:
        """Get the attack technique for this atomic attack."""
        return self._attack_technique

    @property
    def objectives(self) -> list[str]:
        """
        Get the objectives from the seed groups.

        Returns:
            List[str]: List of objectives from all seed groups.
        """
        return [sg.objective.value for sg in self._seed_groups if sg.objective is not None]

    @property
    def seed_groups(self) -> list[SeedAttackGroup]:
        """
        Get a copy of the seed groups list for this atomic attack.

        Returns:
            List[SeedAttackGroup]: A copy of the seed groups list.
        """
        return list(self._seed_groups)

    def filter_seed_groups_by_objectives(self, *, remaining_objectives: list[str]) -> None:
        """
        Filter seed groups to only those with objectives in the remaining list.

        This is used for scenario resumption to skip already completed objectives.

        Args:
            remaining_objectives (List[str]): List of objectives that still need to be executed.
        """
        remaining_set = set(remaining_objectives)
        self._seed_groups = [
            sg for sg in self._seed_groups if sg.objective is not None and sg.objective.value in remaining_set
        ]

    async def run_async(
        self,
        *,
        max_concurrency: int = 1,
        return_partial_on_failure: bool = True,
        **attack_params: Any,
    ) -> AttackExecutorResult[AttackResult]:
        """
        Execute the atomic attack against all seed groups.

        This method uses AttackExecutor to run the configured attack against
        all seed groups.

        When return_partial_on_failure=True (default), this method will return
        an AttackExecutorResult containing both completed results and incomplete
        objectives (those that didn't finish execution due to exceptions). This allows
        scenarios to save progress and retry only the incomplete objectives.

        Note: "completed" means the execution finished, not that the attack objective
        was achieved. "incomplete" means execution didn't finish (threw an exception).

        Args:
            max_concurrency (int): Maximum number of concurrent attack executions.
                Defaults to 1 for sequential execution.
            return_partial_on_failure (bool): If True, returns partial results even when
                some objectives don't complete execution. If False, raises an exception on
                any execution failure. Defaults to True.
            **attack_params: Additional parameters to pass to the attack strategy.

        Returns:
            AttackExecutorResult[AttackResult]: Result containing completed attack results and
                incomplete objectives (those that didn't finish execution).

        Raises:
            ValueError: If the attack execution fails completely and return_partial_on_failure=False.
        """
        executor = AttackExecutor(max_concurrency=max_concurrency)

        logger.info(
            f"Starting atomic attack execution with {len(self._seed_groups)} seed groups "
            f"and max_concurrency={max_concurrency}"
        )

        try:
            # If the technique has seeds, merge them into each seed group for execution.
            # The original seed_groups are not mutated.
            technique = self._attack_technique
            if technique.seed_technique is not None:
                execution_seed_groups = [
                    sg.with_technique(technique=technique.seed_technique) for sg in self._seed_groups
                ]
            else:
                execution_seed_groups = self._seed_groups

            results = await executor.execute_attack_from_seed_groups_async(
                attack=technique.attack,
                seed_groups=execution_seed_groups,
                adversarial_chat=self._adversarial_chat,
                objective_scorer=self._objective_scorer,
                memory_labels=self._memory_labels,
                return_partial_on_failure=return_partial_on_failure,
                **self._attack_execute_params,
            )

            # Enrich atomic_attack_identifier with seed identifiers
            self._enrich_atomic_attack_identifiers(results=results)

            # Log completion status
            if results.has_incomplete:
                logger.warning(
                    f"Atomic attack execution completed with {len(results.completed_results)} completed "
                    f"and {len(results.incomplete_objectives)} incomplete objectives"
                )
            else:
                logger.info(
                    f"Atomic attack execution completed successfully with {len(results.completed_results)} results"
                )

            return results

        except Exception as e:
            logger.error(f"Atomic attack execution failed: {str(e)}")
            raise ValueError(f"Failed to execute atomic attack: {str(e)}") from e

    def _enrich_atomic_attack_identifiers(self, *, results: AttackExecutorResult[AttackResult]) -> None:
        """
        Enrich each AttackResult's atomic_attack_identifier with seed group and
        technique information, then persist the update to the database.

        Uses ``results.input_indices`` to map each completed result back to its
        originating seed group by index, then rebuilds the atomic_attack_identifier
        to include the seed identifiers and any technique seeds. The enriched
        identifier is then flushed back to the corresponding ``AttackResultEntry`` row.

        Args:
            results: The execution results to enrich.
        """
        memory = CentralMemory.get_memory_instance()

        for result, idx in zip(results.completed_results, results.input_indices, strict=True):
            if idx < len(self._seed_groups):
                result.atomic_attack_identifier = build_atomic_attack_identifier(
                    technique_identifier=self._attack_technique.get_identifier(),
                    seed_group=self._seed_groups[idx],
                )

                # Persist the enriched identifier back to the database.
                # Set eval_hash before truncation so it survives the DB round-trip.
                if result.atomic_attack_identifier.eval_hash is None:
                    result.atomic_attack_identifier = result.atomic_attack_identifier.with_eval_hash(
                        AtomicAttackEvaluationIdentifier(result.atomic_attack_identifier).eval_hash
                    )

                if result.attack_result_id:
                    memory.update_attack_result_by_id(
                        attack_result_id=result.attack_result_id,
                        update_fields={
                            "atomic_attack_identifier": result.atomic_attack_identifier.to_dict(
                                max_value_length=MAX_IDENTIFIER_VALUE_LENGTH,
                            ),
                        },
                    )
