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
from typing import TYPE_CHECKING, Any, Optional

from pyrit.common.deprecation import print_deprecation_message
from pyrit.common.utils import to_sha256
from pyrit.executor.attack import AttackExecutor, AttackStrategy
from pyrit.executor.attack.core.attack_executor import AttackExecutorResult
from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution
from pyrit.identifiers import build_atomic_attack_identifier
from pyrit.identifiers.evaluation_identifier import AtomicAttackEvaluationIdentifier
from pyrit.memory import CentralMemory
from pyrit.memory.memory_models import MAX_IDENTIFIER_VALUE_LENGTH
from pyrit.models import AttackResult, SeedAttackGroup
from pyrit.scenario.core.attack_technique import AttackTechnique

if TYPE_CHECKING:
    from pyrit.prompt_target import PromptTarget
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
        adversarial_chat: Optional["PromptTarget"] = None,
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
            attack: **Deprecated.** Will be removed in v0.16.0. The configured attack
                strategy to execute. Use ``attack_technique`` instead.
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
            print_deprecation_message(
                old_item="AtomicAttack(attack=...)",
                new_item="AtomicAttack(attack_technique=AttackTechnique(attack=...))",
                removed_in="0.16.0",
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
        self._validate_unique_objective_hashes()
        self._adversarial_chat = adversarial_chat
        self._objective_scorer = objective_scorer
        self._memory_labels = memory_labels or {}
        self._attack_execute_params = attack_execute_params
        # Set via set_scenario_result_id() by Scenario._execute_scenario_async
        # before run_async. When set, each persisted AttackResult is linked to
        # the scenario via the attribution_parent_id foreign key on
        # AttackResultEntry.
        self._scenario_result_id: str | None = None

        logger.info(
            f"Initialized atomic attack with {len(self._seed_groups)} seed groups, "
            f"attack type: {type(self._attack_technique.attack).__name__}"
        )

    def set_scenario_result_id(self, scenario_result_id: str | None) -> None:
        """
        Bind this atomic attack to a scenario result for attribution.

        Called by ``Scenario._execute_scenario_async`` before each
        ``run_async`` so persisted ``AttackResult`` rows carry the
        ``attribution_parent_id`` foreign key back to the scenario. Pass
        ``None`` to clear the binding (e.g. when running an atomic attack
        outside of a scenario).

        Args:
            scenario_result_id (str | None): The scenario result UUID this
                atomic attack belongs to, or ``None`` to detach.
        """
        self._scenario_result_id = scenario_result_id

    def _validate_unique_objective_hashes(self) -> None:
        """
        Ensure each seed group in this atomic attack has a unique objective hash.

        Within a single ``AtomicAttack`` (one ``atomic_attack_name``, one
        technique), the objective text identifies a unit of work. Duplicates
        would mean two indistinguishable rows on the write side, which makes
        resume reconciliation ambiguous — the hash-based resume key treats a
        set of hashes as already-done, with no way to distinguish which of two
        duplicate rows is "the one" that is still outstanding.

        The hash is currently derived from objective text only. A future
        iteration may hash the full ``SeedGroup`` (minus technique-specific
        fields) so two seed groups that share an objective string but differ
        in other inputs can coexist in one atomic attack.

        Raises:
            ValueError: If two seed groups share the same ``objective_sha256``.
        """
        seen: dict[str, int] = {}
        for sg in self._seed_groups:
            if sg.objective is None:
                continue
            sha = to_sha256(sg.objective.value)
            if sha in seen:
                raise ValueError(
                    f"AtomicAttack '{self.atomic_attack_name}' has duplicate objective hash "
                    f"{sha[:12]}... across seed_groups; each (objective, technique) pair must be unique."
                )
            seen[sha] = 1

    @property
    def attack_technique(self) -> AttackTechnique:
        """Get the attack technique for this atomic attack."""
        return self._attack_technique

    @property
    def technique_eval_hash(self) -> str:
        """
        Behavioral evaluation hash for this atomic attack's technique configuration.

        Builds an ``AtomicAttack`` identifier from this attack's technique
        (without any seed group) and runs it through
        ``AtomicAttackEvaluationIdentifier`` so target/scorer/seed-identifier
        noise is stripped per the standard atomic-attack eval rules. The
        result is stable across resume runs and across different seed groups,
        which is what makes it usable as the resume disambiguator alongside
        ``atomic_attack_name``.
        """
        composite = build_atomic_attack_identifier(
            technique_identifier=self._attack_technique.get_identifier(),
            seed_group=None,
        )
        return AtomicAttackEvaluationIdentifier(composite).eval_hash

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

    def drop_seed_groups_with_hashes(self, *, hashes: set[str]) -> None:
        """
        Drop seed groups whose ``objective_sha256`` is in ``hashes``.

        This is the resume filter: within an atomic attack, ``objective_sha256``
        is the stable identity (enforced unique by ``__init__``). Content-derived
        keys are robust to reordering and resampling, so resume produces the
        right remaining-work set even when ``get_seed_groups()`` is rebuilt
        from scratch on each ``run_async()``.

        Args:
            hashes (set[str]): SHA256 hashes of objective text for seed groups
                to drop (typically those that have already produced a
                non-error ``AttackResult``).
        """
        self._seed_groups = [
            sg for sg in self._seed_groups if sg.objective is None or to_sha256(sg.objective.value) not in hashes
        ]

    def filter_seed_groups_by_objectives(self, *, remaining_objectives: list[str]) -> None:
        """
        Filter seed groups to only those with objectives in the remaining list.

        .. deprecated::
            Use ``drop_seed_groups_with_hashes`` (or ``keep_seed_groups_with_hashes``)
            which keys on content-addressed ``objective_sha256`` instead of
            objective text. Scheduled for removal in 0.16.0.

        Args:
            remaining_objectives (List[str]): List of objectives that still need to be executed.
        """
        print_deprecation_message(
            old_item="AtomicAttack.filter_seed_groups_by_objectives(remaining_objectives=...)",
            new_item="AtomicAttack.keep_seed_groups_with_hashes(hashes=...)",
            removed_in="0.16.0",
        )
        remaining_set = set(remaining_objectives)
        self._seed_groups = [
            sg for sg in self._seed_groups if sg.objective is not None and sg.objective.value in remaining_set
        ]

    def keep_seed_groups_with_hashes(self, *, hashes: set[str]) -> set[str]:
        """
        Keep only seed groups whose ``objective_sha256`` is in ``hashes``.

        Inverse of ``drop_seed_groups_with_hashes``: used on resume to
        replay the originally-sampled subset and ignore any seed groups that
        were added since (or that landed in this run's fresh ``random.sample``
        draw and are no longer in the persisted set).

        Args:
            hashes (set[str]): SHA256 hashes of objective text for seed
                groups to keep.

        Returns:
            set[str]: The hashes that were actually retained (intersection of
            ``hashes`` and the current seed_groups' hashes). The caller can
            union these across atomic attacks to detect persisted hashes that
            no longer exist in the dataset.
        """
        retained: set[str] = set()
        new_groups: list[SeedAttackGroup] = []
        for sg in self._seed_groups:
            if sg.objective is None:
                continue
            sha = to_sha256(sg.objective.value)
            if sha in hashes:
                retained.add(sha)
                new_groups.append(sg)
        self._seed_groups = new_groups
        return retained

    async def run_async(
        self,
        *,
        executor: AttackExecutor | None = None,
        return_partial_on_failure: bool = True,
        max_concurrency: int | None = None,
        **attack_params: Any,
    ) -> AttackExecutorResult[AttackResult]:
        """
        Execute the atomic attack against all seed groups.

        This method uses ``AttackExecutor`` to run the configured attack against
        all seed groups. Concurrency is owned by the executor: pass a shared
        ``AttackExecutor`` instance to share a single budget across multiple
        atomic attacks (this is how ``Scenario`` parallelizes them).

        When return_partial_on_failure=True (default), this method will return
        an AttackExecutorResult containing both completed results and incomplete
        objectives (those that didn't finish execution due to exceptions). This allows
        scenarios to save progress and retry only the incomplete objectives.

        Note: "completed" means the execution finished, not that the attack objective
        was achieved. "incomplete" means execution didn't finish (threw an exception).

        Args:
            executor (AttackExecutor | None): Optional ``AttackExecutor`` to run the
                attack with. When provided, its concurrency budget is used and is
                shared with anything else holding a reference to it. When ``None``,
                a fresh ``AttackExecutor(max_concurrency=max_concurrency)`` is created
                for this call.
            return_partial_on_failure (bool): If True, returns partial results even when
                some objectives don't complete execution. If False, raises an exception on
                any execution failure. Defaults to True.
            max_concurrency (int | None): **Deprecated.** Will be removed in 0.16.0. Pass
                ``executor=AttackExecutor(max_concurrency=...)`` instead. Passing any
                value here emits a ``DeprecationWarning``. When ``executor`` is also
                provided, this value is silently ignored.
            **attack_params: Additional parameters to pass to the attack strategy.

        Returns:
            AttackExecutorResult[AttackResult]: Result containing completed attack results and
                incomplete objectives (those that didn't finish execution).

        Raises:
            ValueError: If the attack execution fails completely and return_partial_on_failure=False.
        """
        if max_concurrency is not None:
            print_deprecation_message(
                old_item="AtomicAttack.run_async(max_concurrency=...)",
                new_item="AtomicAttack.run_async(executor=AttackExecutor(max_concurrency=...))",
                removed_in="0.16.0",
            )

        if executor is None:
            executor = AttackExecutor(max_concurrency=max_concurrency if max_concurrency is not None else 1)

        logger.info(
            f"Starting atomic attack execution with {len(self._seed_groups)} seed groups "
            f"(executor max_concurrency={getattr(executor, '_max_concurrency', 'unknown')})"
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

            # Build attribution when this atomic attack is being executed inside
            # a Scenario. The same attribution object is stamped on every
            # per-task AttackContext; per-task identity is reconstructed from
            # the row's own objective_sha256 (no positional state required).
            attribution: AttackResultAttribution | None = None
            if self._scenario_result_id is not None:
                attribution = AttackResultAttribution(
                    parent_id=self._scenario_result_id,
                    parent_collection=self.atomic_attack_name,
                    parent_eval_hash=self.technique_eval_hash,
                )

            results = await executor.execute_attack_from_seed_groups_async(
                attack=technique.attack,
                seed_groups=execution_seed_groups,
                adversarial_chat=self._adversarial_chat,
                objective_scorer=self._objective_scorer,
                memory_labels=self._memory_labels,
                return_partial_on_failure=return_partial_on_failure,
                attribution=attribution,
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
