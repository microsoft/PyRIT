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
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pyrit.executor.attack import AttackExecutor, AttackStrategy
from pyrit.executor.attack.core.attack_executor import AttackExecutorResult
from pyrit.models import AttackResult, SeedAttackGroup

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

    Example:
        >>> from pyrit.scenario import AtomicAttack
        >>> from pyrit.attacks import PromptAttack
        >>> from pyrit.prompt_target import OpenAIChatTarget
        >>> from pyrit.models import SeedGroup
        >>>
        >>> target = OpenAIChatTarget()
        >>> attack = PromptAttack(objective_target=target)
        >>>
        >>> # Create seed groups with objectives
        >>> seed_groups = SeedAttackGroup.from_yaml_file("seeds.yaml")
        >>> for sg in seed_groups:
        ...     sg.set_objective("your objective here")
        >>>
        >>> atomic_attack = AtomicAttack(
        ...     atomic_attack_name="test_attack",
        ...     attack=attack,
        ...     seed_groups=seed_groups,
        ...     memory_labels={"test": "run1"}
        ... )
        >>> results = await atomic_attack.run_async(max_concurrency=5)
    """

    def __init__(
        self,
        *,
        atomic_attack_name: str,
        attack: AttackStrategy[Any, Any],
        seed_groups: List[SeedAttackGroup],
        adversarial_chat: Optional["PromptChatTarget"] = None,
        objective_scorer: Optional["TrueFalseScorer"] = None,
        memory_labels: Optional[Dict[str, str]] = None,
        **attack_execute_params: Any,
    ) -> None:
        """
        Initialize an atomic attack with an attack strategy and seed groups.

        Args:
            atomic_attack_name (str): Used to group an AtomicAttack with related attacks for a
                strategy.
            attack (AttackStrategy): The configured attack strategy to execute.
            seed_groups (List[SeedAttackGroup]): List of seed attack groups. Each seed group must
                have an objective set. The seed groups serve as the single source of truth for
                objectives, prepended conversations, and next messages.
            adversarial_chat (Optional[PromptChatTarget]): Optional chat target for generating
                adversarial prompts or simulated conversations. Required when seed groups contain
                SeedSimulatedConversation configurations.
            objective_scorer (Optional[TrueFalseScorer]): Optional scorer for evaluating simulated
                conversations. Required when seed groups contain SeedSimulatedConversation
                configurations.
            memory_labels (Optional[Dict[str, str]]): Additional labels to apply to prompts.
                These labels help track and categorize the atomic attack in memory.
            **attack_execute_params (Any): Additional parameters to pass to the attack
                execution method (e.g., batch_size).

        Raises:
            ValueError: If seed_groups list is empty or any seed group is missing an objective.
        """
        self.atomic_attack_name = atomic_attack_name
        self._attack = attack

        # Validate seed_groups
        if not seed_groups:
            raise ValueError("seed_groups list cannot be empty")

        # Validate each seed group to ensure they are in a valid state
        for sg in seed_groups:
            sg.validate()

        self._seed_groups = seed_groups
        self._adversarial_chat = adversarial_chat
        self._objective_scorer = objective_scorer
        self._objective_scorer = objective_scorer
        self._memory_labels = memory_labels or {}
        self._attack_execute_params = attack_execute_params

        logger.info(
            f"Initialized atomic attack with {len(self._seed_groups)} seed groups, attack type: {type(attack).__name__}"
        )

    @property
    def objectives(self) -> List[str]:
        """
        Get the objectives from the seed groups.

        Returns:
            List[str]: List of objectives from all seed groups.
        """
        return [sg.objective.value for sg in self._seed_groups if sg.objective is not None]

    @property
    def seed_groups(self) -> List[SeedAttackGroup]:
        """
        Get a copy of the seed groups list for this atomic attack.

        Returns:
            List[SeedAttackGroup]: A copy of the seed groups list.
        """
        return list(self._seed_groups)

    def filter_seed_groups_by_objectives(self, *, remaining_objectives: List[str]) -> None:
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
            results = await executor.execute_attack_from_seed_groups_async(
                attack=self._attack,
                seed_groups=self._seed_groups,
                adversarial_chat=self._adversarial_chat,
                objective_scorer=self._objective_scorer,
                memory_labels=self._memory_labels,
                return_partial_on_failure=return_partial_on_failure,
                **self._attack_execute_params,
            )

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
