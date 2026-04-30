# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario class for grouping and executing multiple AtomicAttacks.

This module provides the Scenario class that orchestrates the execution of multiple
AtomicAttack instances sequentially, enabling comprehensive security testing campaigns.
"""

import asyncio
import logging
import textwrap
import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Optional, Union, cast

from tqdm.auto import tqdm

from pyrit.common import REQUIRED_VALUE, apply_defaults
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.memory.memory_models import ScenarioResultEntry
from pyrit.models import AttackResult, SeedAttackGroup
from pyrit.models.scenario_result import ScenarioIdentifier, ScenarioResult
from pyrit.prompt_target import OpenAIChatTarget, PromptTarget
from pyrit.registry import ScorerRegistry
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import DatasetConfiguration
from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
from pyrit.score import Scorer, SelfAskRefusalScorer, TrueFalseInverterScorer, TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.executor.attack.core.attack_config import AttackScoringConfig
    from pyrit.identifiers import ComponentIdentifier
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

logger = logging.getLogger(__name__)


class Scenario(ABC):
    """
    Groups and executes multiple AtomicAttack instances sequentially.

    A Scenario represents a comprehensive testing campaign composed of multiple
    atomic attack tests (AtomicAttacks). It executes each AtomicAttack in sequence and
    aggregates the results into a ScenarioResult.
    """

    def __init__(
        self,
        *,
        name: str = "",
        version: int,
        strategy_class: type[ScenarioStrategy],
        objective_scorer: Scorer,
        include_default_baseline: bool = True,
        scenario_result_id: Optional[Union[uuid.UUID, str]] = None,
    ) -> None:
        """
        Initialize a scenario.

        Args:
            name (str): Descriptive name for the scenario.
            version (int): Version number of the scenario.
            strategy_class (Type[ScenarioStrategy]): The strategy enum class for this scenario.
            objective_scorer (Scorer): The objective scorer used to evaluate attack results.
            include_default_baseline (bool): Whether to include a baseline atomic attack that sends all objectives
                without modifications. Most scenarios should have some kind of baseline so users can understand
                the impact of strategies, but subclasses can optionally write their own custom baselines.
                Defaults to True.
            scenario_result_id (Optional[Union[uuid.UUID, str]]): Optional ID of an existing scenario result to resume.
                Can be either a UUID object or a string representation of a UUID.
                If provided and found in memory, the scenario will resume from prior progress.
                All other parameters must still match the stored scenario configuration.

        Note:
            Attack runs are populated by calling initialize_async(), which invokes the
            subclass's _get_atomic_attacks_async() method.

            The scenario description is automatically extracted from the class's docstring (__doc__)
            with whitespace normalized for display.
        """
        # Use the class docstring with normalized whitespace as description
        description = " ".join(self.__class__.__doc__.split()) if self.__class__.__doc__ else ""

        self._identifier = ScenarioIdentifier(
            name=type(self).__name__, scenario_version=version, description=description
        )

        # Store strategy configuration for use in initialize_async
        self._strategy_class = strategy_class

        # These will be set in initialize_async
        self._objective_target: Optional[PromptTarget] = None
        self._objective_target_identifier: Optional[ComponentIdentifier] = None
        self._memory_labels: dict[str, str] = {}
        self._max_concurrency: int = 1
        self._max_retries: int = 0

        self._objective_scorer = objective_scorer
        self._objective_scorer_identifier = objective_scorer.get_identifier()

        self._name = name if name else type(self).__name__
        self._memory = CentralMemory.get_memory_instance()
        self._atomic_attacks: list[AtomicAttack] = []
        self._scenario_result_id: Optional[str] = str(scenario_result_id) if scenario_result_id else None
        self._result_lock = asyncio.Lock()

        self._include_baseline = include_default_baseline

        # Store prepared strategies for use in _get_atomic_attacks_async
        self._scenario_strategies: list[ScenarioStrategy] = []

        # Store original objectives for each atomic attack (before any mutations)
        # Key: atomic_attack_name, Value: tuple of original objectives
        self._original_objectives_map: dict[str, tuple[str, ...]] = {}

        # Maps atomic_attack_name → display_group for user-facing aggregation
        self._display_group_map: dict[str, str] = {}

    @property
    def name(self) -> str:
        """Get the name of the scenario."""
        return self._name

    @property
    def atomic_attack_count(self) -> int:
        """Get the number of atomic attacks in this scenario."""
        return len(self._atomic_attacks)

    @classmethod
    @abstractmethod
    def get_strategy_class(cls) -> type[ScenarioStrategy]:
        """
        Get the strategy enum class for this scenario.

        This abstract method must be implemented by all scenario subclasses to return
        the ScenarioStrategy enum class that defines the available attack strategies
        for the scenario.

        Returns:
            Type[ScenarioStrategy]: The strategy enum class (e.g., FoundryStrategy, EncodingStrategy).
        """

    @classmethod
    @abstractmethod
    def get_default_strategy(cls) -> ScenarioStrategy:
        """
        Get the default strategy used when no strategies are specified.

        This abstract method must be implemented by all scenario subclasses to return
        the default aggregate strategy (like EASY, ALL) used when scenario_strategies
        parameter is None.

        Returns:
            ScenarioStrategy: The default aggregate strategy (e.g., FoundryStrategy.EASY, EncodingStrategy.ALL).
        """

    @classmethod
    @abstractmethod
    def default_dataset_config(cls) -> DatasetConfiguration:
        """
        Return the default dataset configuration for this scenario.

        This abstract method must be implemented by all scenario subclasses to return
        a DatasetConfiguration specifying the default datasets to use when no
        dataset_config is provided by the user.

        Returns:
            DatasetConfiguration: The default dataset configuration.
        """

    def _get_attack_technique_factories(self) -> dict[str, "AttackTechniqueFactory"]:
        """
        Return the attack technique factories for this scenario.

        Each key is a technique name (matching a strategy enum value) and each
        value is an ``AttackTechniqueFactory`` that can produce an
        ``AttackTechnique`` for that technique.

        The base implementation lazily populates the
        ``AttackTechniqueRegistry`` singleton with core techniques (via
        ``ScenarioTechniqueRegistrar``) and returns all registered factories.
        Subclasses may override to add, remove, or replace factories.

        Returns:
            dict[str, AttackTechniqueFactory]: Mapping of technique name to factory.
        """
        from pyrit.scenario.core.scenario_techniques import register_scenario_techniques

        register_scenario_techniques()

        from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry

        return AttackTechniqueRegistry.get_registry_singleton().get_factories()

    def _build_display_group(self, *, technique_name: str, seed_group_name: str) -> str:
        """
        Build the display-group label for an atomic attack.

        Each ``AtomicAttack`` has a unique ``atomic_attack_name`` (e.g.
        ``"prompt_sending_airt_hate"``) used for resume tracking.  However,
        user-facing output (console printer, reports) often needs to
        aggregate results along a *different* dimension — for example,
        grouping by harm category rather than by technique.  The display
        group provides that second grouping axis without affecting resume
        behaviour.

        The default groups by technique name.  Subclasses override to
        change the aggregation axis:

        - **By technique** (default): ``return technique_name``
        - **By harm category / dataset**: ``return seed_group_name``
        - **Cross-product**: ``return f"{technique_name}_{seed_group_name}"``

        Note: ``seed_group_name`` is the dataset key from
        ``DatasetConfiguration.get_seed_attack_groups()`` (e.g.
        ``"airt_hate"``), not a ``SeedGroup`` object.

        Args:
            technique_name: The name of the attack technique.
            seed_group_name: The dataset key from the dataset configuration.

        Returns:
            str: The display-group label.
        """
        return technique_name

    def _get_default_objective_scorer(self) -> TrueFalseScorer:
        # Deferred import to avoid circular dependency:
        from pyrit.setup.initializers.components.scorers import ScorerInitializerTags

        entries = ScorerRegistry.get_registry_singleton().get_by_tag(tag=ScorerInitializerTags.DEFAULT_OBJECTIVE_SCORER)
        if entries and isinstance(entries[0].instance, TrueFalseScorer):
            scorer = entries[0].instance
            logger.info(f"Using registered default objective scorer: {type(scorer).__name__}")
            return scorer
        scorer = TrueFalseInverterScorer(scorer=SelfAskRefusalScorer(chat_target=OpenAIChatTarget()))
        logger.info(f"No registered default objective scorer found, using fallback: {type(scorer).__name__}")
        return scorer

    def _prepare_strategies(
        self,
        strategies: Optional[Sequence[ScenarioStrategy]],
    ) -> list[ScenarioStrategy]:
        """
        Resolve strategy inputs into a concrete list for this scenario.

        The default implementation calls resolve() on the strategy class, which handles
        None (use default), empty list (also use default), and aggregate expansion.

        Subclasses with complex composition semantics (e.g., RedTeamAgent with
        FoundryComposite) should override this to build their own composite types.

        Args:
            strategies: Strategy inputs from initialize_async. None or [] both mean use
                default; otherwise a list of strategies to resolve.

        Returns:
            list[ScenarioStrategy]: Ordered, deduplicated concrete strategies.
        """
        return self._strategy_class.resolve(strategies, default=self.get_default_strategy())

    @apply_defaults
    async def initialize_async(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-assignment, ty:invalid-parameter-default]
        scenario_strategies: Optional[Sequence[ScenarioStrategy]] = None,
        dataset_config: Optional[DatasetConfiguration] = None,
        max_concurrency: int = 10,
        max_retries: int = 0,
        memory_labels: Optional[dict[str, str]] = None,
    ) -> None:
        """
        Initialize the scenario by populating self._atomic_attacks and creating the ScenarioResult.

        This method allows scenarios to be initialized with atomic attacks after construction,
        which is useful when atomic attacks require async operations to be built.

        If a scenario_result_id was provided in __init__, this method will check if it exists
        in memory and validate that the stored scenario matches the current configuration.
        If it matches, the scenario will resume from prior progress. If it doesn't match or
        doesn't exist, a new scenario result will be created.

        Args:
            objective_target (PromptTarget): The target system to attack.
            scenario_strategies (Optional[Sequence[ScenarioStrategy]]): The strategies to execute.
                Can be a list of ScenarioStrategy enum members. If None, uses the default aggregate
                from the scenario's configuration.
            dataset_config (Optional[DatasetConfiguration]): Configuration for the dataset source.
                Use this to specify dataset names or maximum dataset size from the CLI.
                If not provided, scenarios use their default_dataset_config().
            max_concurrency (int): Maximum number of concurrent attack executions. Defaults to 1.
            max_retries (int): Maximum number of automatic retries if the scenario raises an exception.
                Set to 0 (default) for no automatic retries. If set to a positive number,
                the scenario will automatically retry up to this many times after an exception.
                For example, max_retries=3 allows up to 4 total attempts (1 initial + 3 retries).
            memory_labels (Optional[Dict[str, str]]): Additional labels to apply to all
                attack runs in the scenario. These help track and categorize the scenario.

        Raises:
            ValueError: If no objective_target is provided.
        """
        # Validate required parameters
        if objective_target is None:
            raise ValueError(
                "objective_target is required. "
                "Provide it either as a parameter or via set_default_value() in an initialization script."
            )

        # Set instance variables from parameters
        self._objective_target = objective_target
        self._objective_target_identifier = objective_target.get_identifier()
        self._dataset_config_provided = dataset_config is not None
        self._dataset_config = dataset_config if dataset_config else self.default_dataset_config()
        self._max_concurrency = max_concurrency
        self._max_retries = max_retries
        self._memory_labels = memory_labels or {}

        # Prepare scenario strategies using the stored configuration
        self._scenario_strategies = self._prepare_strategies(scenario_strategies)

        self._atomic_attacks = await self._get_atomic_attacks_async()

        if self._include_baseline:
            baseline_attack = self._get_baseline()
            self._atomic_attacks.insert(0, baseline_attack)

        # Store original objectives for each atomic attack (before any mutations during execution)
        self._original_objectives_map = {
            atomic_attack.atomic_attack_name: tuple(atomic_attack.objectives) for atomic_attack in self._atomic_attacks
        }

        # Check if we're resuming an existing scenario
        if self._scenario_result_id:
            existing_results = self._memory.get_scenario_results(scenario_result_ids=[self._scenario_result_id])

            if existing_results:
                existing_result = existing_results[0]

                # Validate that the stored scenario matches current configuration
                if self._validate_stored_scenario(stored_result=existing_result):
                    return  # Valid match - skip creating new scenario result
                # Validation failed - will create new scenario result
                self._scenario_result_id = None
            else:
                logger.warning(
                    f"Scenario result ID {self._scenario_result_id} not found in memory. Creating new scenario result."
                )
                self._scenario_result_id = None

        # Build display group mapping from atomic attacks
        self._display_group_map = {aa.atomic_attack_name: aa.display_group for aa in self._atomic_attacks}

        # Create new scenario result
        attack_results: dict[str, list[AttackResult]] = {
            atomic_attack.atomic_attack_name: [] for atomic_attack in self._atomic_attacks
        }

        result = ScenarioResult(
            scenario_identifier=self._identifier,
            objective_target_identifier=self._objective_target_identifier,
            objective_scorer_identifier=self._objective_scorer_identifier,
            labels=self._memory_labels,
            attack_results=attack_results,
            scenario_run_state="CREATED",
            display_group_map=self._display_group_map,
        )

        self._memory.add_scenario_results_to_memory(scenario_results=[result])
        self._scenario_result_id = str(result.id)
        logger.info(f"Created new scenario result with ID: {self._scenario_result_id}")

    def _get_baseline(self) -> AtomicAttack:
        """
        Get a baseline AtomicAttack, which simply sends all the objectives without any modifications.

        If other atomic attacks exist, derives baseline data from the first attack.
        Otherwise, creates a standalone baseline from the dataset configuration and scenario settings.

        Returns:
            AtomicAttack: The baseline AtomicAttack instance.

        Raises:
            ValueError: If required data (seed_groups, objective_target, attack_scoring_config)
                       is not available.
        """
        seed_groups, attack_scoring_config, objective_target = self._get_baseline_data()

        # Create baseline attack with no converters
        attack = PromptSendingAttack(
            objective_target=objective_target,
            attack_scoring_config=attack_scoring_config,
        )

        return AtomicAttack(
            atomic_attack_name="baseline",
            attack_technique=AttackTechnique(attack=attack),
            seed_groups=seed_groups,
            memory_labels=self._memory_labels,
        )

    def _get_baseline_data(self) -> tuple[list["SeedAttackGroup"], "AttackScoringConfig", PromptTarget]:
        """
        Get the data needed to create a baseline attack.

        Returns the scenario-level data

        Returns:
            Tuple containing (seed_groups, attack_scoring_config, objective_target)

        Raises:
            ValueError: If required data is not available.
        """
        # Create from scenario-level settings
        if not self._objective_target:
            raise ValueError("Objective target is required to create baseline attack.")
        if not self._dataset_config:
            raise ValueError("Dataset config is required to create baseline attack.")
        if not self._objective_scorer:
            raise ValueError("Objective scorer is required to create baseline attack.")

        seed_groups = self._dataset_config.get_all_seed_attack_groups()
        if not seed_groups or len(seed_groups) == 0:
            raise ValueError("Seed groups are required to create baseline attack.")

        # Import here to avoid circular imports
        from pyrit.executor.attack.core.attack_config import AttackScoringConfig

        attack_scoring_config = AttackScoringConfig(objective_scorer=cast("TrueFalseScorer", self._objective_scorer))

        if not attack_scoring_config:
            raise ValueError("Attack scoring config is required to create baseline attack.")

        return seed_groups, attack_scoring_config, self._objective_target

    def _raise_dataset_exception(self) -> None:
        error_msg = textwrap.dedent(
            f"""
            Dataset is not available or failed to load.
            Scenarios require datasets loaded in CentralMemory or to be passed explicitly.
            Either load the datasets into the database before running the scenario, or for
            example datasets, you can use the `load_default_datasets` initializer.

            Required datasets: {", ".join(self.default_dataset_config().get_default_dataset_names())}
            """
        )
        raise ValueError(error_msg)

    def _validate_stored_scenario(self, *, stored_result: ScenarioResult) -> bool:
        """
        Validate that a stored scenario result matches the current scenario configuration.

        Args:
            stored_result (ScenarioResult): The scenario result retrieved from memory.

        Returns:
            bool: True if the stored scenario matches current configuration, False otherwise.
        """
        stored_name = stored_result.scenario_identifier.name
        stored_version = stored_result.scenario_identifier.version

        if stored_name != self._identifier.name:
            logger.warning(
                f"Scenario result ID {self._scenario_result_id} has mismatched name: "
                f"stored='{stored_name}', current='{self._identifier.name}'. "
                f"Creating new scenario result."
            )
            return False

        if stored_version != self._identifier.version:
            logger.warning(
                f"Scenario result ID {self._scenario_result_id} has mismatched version: "
                f"stored={stored_version}, current={self._identifier.version}. "
                f"Creating new scenario result."
            )
            return False

        # Valid match - log resumption
        logger.info(
            f"Resuming scenario '{self._name}' from existing result "
            f"(ID: {self._scenario_result_id}, state: {stored_result.scenario_run_state})"
        )
        return True

    def _get_completed_objectives_for_attack(self, *, atomic_attack_name: str) -> set[str]:
        """
        Get the set of objectives that have already been completed for a specific atomic attack.

        Args:
            atomic_attack_name (str): The name of the atomic attack to check.

        Returns:
            Set[str]: Set of objective strings that have been completed.
        """
        if not self._scenario_result_id:
            return set()

        completed_objectives: set[str] = set()

        try:
            # Retrieve the scenario result from memory
            scenario_results = self._memory.get_scenario_results(scenario_result_ids=[self._scenario_result_id])

            if scenario_results:
                scenario_result = scenario_results[0]
                # Get completed objectives for this atomic attack name
                if atomic_attack_name in scenario_result.attack_results:
                    completed_objectives = {
                        result.objective for result in scenario_result.attack_results[atomic_attack_name]
                    }
        except Exception as e:
            logger.warning(
                f"Failed to retrieve completed objectives for atomic attack '{atomic_attack_name}': {str(e)}"
            )

        return completed_objectives

    async def _get_remaining_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Get the list of atomic attacks that still have objectives to complete.

        This method filters out atomic attacks where all objectives have been completed,
        and updates the objectives list for atomic attacks that are partially complete.

        Returns:
            List[AtomicAttack]: List of atomic attacks with uncompleted objectives.
        """
        if not self._scenario_result_id:
            # No scenario result yet, return all atomic attacks
            return self._atomic_attacks

        remaining_attacks: list[AtomicAttack] = []

        for atomic_attack in self._atomic_attacks:
            # Get completed objectives for this atomic attack name
            completed_objectives = self._get_completed_objectives_for_attack(
                atomic_attack_name=atomic_attack.atomic_attack_name
            )

            # Get ORIGINAL objectives (before any mutations) from stored map
            original_objectives = self._original_objectives_map.get(atomic_attack.atomic_attack_name, ())

            # Calculate remaining objectives
            remaining_objectives = [obj for obj in original_objectives if obj not in completed_objectives]

            if remaining_objectives:
                # If there are remaining objectives, update the atomic attack
                if len(remaining_objectives) < len(original_objectives):
                    logger.info(
                        f"Atomic attack '{atomic_attack.atomic_attack_name}' has "
                        f"{len(remaining_objectives)}/{len(original_objectives)} objectives remaining"
                    )
                # Update the objectives for this atomic attack to only include remaining ones
                atomic_attack.filter_seed_groups_by_objectives(remaining_objectives=remaining_objectives)

                remaining_attacks.append(atomic_attack)
            else:
                logger.info(
                    f"Atomic attack '{atomic_attack.atomic_attack_name}' has all objectives completed, skipping"
                )

        return remaining_attacks

    async def _update_scenario_result_async(
        self, *, atomic_attack_name: str, attack_results: list[AttackResult]
    ) -> None:
        """
        Update the scenario result in memory with new attack results (thread-safe).

        This method is thread-safe and can be called from parallel executions.

        Args:
            atomic_attack_name (str): The name of the atomic attack.
            attack_results (List[AttackResult]): The list of new attack results to add.
        """
        if not self._scenario_result_id:
            logger.warning("Cannot update scenario result: no scenario result ID available")
            return

        async with self._result_lock:
            success = self._memory.add_attack_results_to_scenario(
                scenario_result_id=self._scenario_result_id,
                atomic_attack_name=atomic_attack_name,
                attack_results=attack_results,
            )

            if not success:
                logger.error(
                    f"Failed to update scenario result with {len(attack_results)} results "
                    f"for atomic attack '{atomic_attack_name}'"
                )

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Build atomic attacks from the cross-product of selected techniques and datasets.

        Uses ``_get_attack_technique_factories()`` to obtain factories, then
        iterates over every (technique, dataset) pair to create an
        ``AtomicAttack`` for each.  Grouping for display is controlled by
        ``_build_display_group()``.

        Subclasses that do **not** use the factory/registry pattern should
        override this method entirely.

        Returns:
            list[AtomicAttack]: The generated atomic attacks.

        Raises:
            ValueError: If the scenario has not been initialized.
        """
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        from pyrit.executor.attack import AttackScoringConfig
        from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry

        selected_techniques = {s.value for s in self._scenario_strategies}

        factories = self._get_attack_technique_factories()
        seed_groups_by_dataset = self._dataset_config.get_seed_attack_groups()

        scoring_config = AttackScoringConfig(objective_scorer=cast("TrueFalseScorer", self._objective_scorer))
        registry = AttackTechniqueRegistry.get_registry_singleton()

        atomic_attacks: list[AtomicAttack] = []
        for technique_name in selected_techniques:
            factory = factories.get(technique_name)
            if factory is None:
                logger.warning(f"No factory for technique '{technique_name}', skipping.")
                continue

            scoring_for_technique = scoring_config if registry.accepts_scorer_override(technique_name) else None

            for dataset_name, seed_groups in seed_groups_by_dataset.items():
                if factory.seed_technique is not None:
                    compatible_groups = SeedAttackGroup.filter_compatible(
                        seed_groups=seed_groups,
                        technique=factory.seed_technique,
                    )
                    skipped = len(seed_groups) - len(compatible_groups)
                    if skipped:
                        logger.info(
                            f"Skipped {skipped} seed group(s) from '{dataset_name}' for technique "
                            f"'{technique_name}' (prompt sequences overlap with simulated conversation)."
                        )
                    if not compatible_groups:
                        logger.warning(
                            f"No compatible seed groups in '{dataset_name}' for technique "
                            f"'{technique_name}', skipping this (technique, dataset) pair."
                        )
                        continue
                else:
                    compatible_groups = list(seed_groups)

                attack_technique = factory.create(
                    objective_target=self._objective_target,
                    attack_scoring_config_override=scoring_for_technique,
                )
                display_group = self._build_display_group(
                    technique_name=technique_name,
                    seed_group_name=dataset_name,
                )
                atomic_attacks.append(
                    AtomicAttack(
                        atomic_attack_name=f"{technique_name}_{dataset_name}",
                        attack_technique=attack_technique,
                        seed_groups=list(compatible_groups),
                        adversarial_chat=factory.adversarial_chat,
                        objective_scorer=cast("TrueFalseScorer", self._objective_scorer),
                        memory_labels=self._memory_labels,
                        display_group=display_group,
                    )
                )

        return atomic_attacks

    async def run_async(self) -> ScenarioResult:
        """
        Execute all atomic attacks in the scenario sequentially.

        Each AtomicAttack is executed in order, and all results are aggregated
        into a ScenarioResult containing the scenario metadata and all attack results.
        This method supports resumption - if the scenario raises an exception partway through,
        calling run_async again will skip already-completed objectives.

        If max_retries is set, the scenario will automatically retry after an exception up to
        the specified number of times. Each retry will resume from where it left off,
        skipping completed objectives.

        Returns:
            ScenarioResult: Contains scenario identifier and aggregated list of all
                attack results from all atomic attacks.

        Raises:
            ValueError: If the scenario has no atomic attacks configured. If your scenario
                requires initialization, call await scenario.initialize() first.
            ValueError: If the scenario raises an exception after exhausting all retry attempts.
            RuntimeError: If the scenario fails for any other reason while executing.

        Example:
            >>> result = await scenario.run_async()
            >>> print(f"Scenario: {result.scenario_identifier.name}")
            >>> print(f"Total results: {len(result.attack_results)}")
        """
        if not self._atomic_attacks:
            raise ValueError(
                "Cannot run scenario with no atomic attacks. Either supply them in initialization or "
                "call await scenario.initialize_async() first."
            )

        if not self._scenario_result_id:
            raise ValueError("Scenario not properly initialized. Call await scenario.initialize_async() first.")

        # Type narrowing: create local variable that type checker knows is non-None
        scenario_result_id: str = self._scenario_result_id

        # Implement retry logic
        last_exception = None
        for retry_attempt in range(self._max_retries + 1):  # +1 for initial attempt
            try:
                return await self._execute_scenario_async()
            except Exception as e:
                last_exception = e

                # Get current scenario to check number of tries
                scenario_results = self._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
                current_tries = scenario_results[0].number_tries if scenario_results else retry_attempt + 1

                # Check if we have more retries available
                remaining_retries = self._max_retries - retry_attempt

                if remaining_retries > 0:
                    logger.error(
                        f"Scenario '{self._name}' failed on attempt {current_tries} with error: {str(e)}. "
                        f"Retrying... ({remaining_retries} retries remaining)",
                        exc_info=True,
                    )
                    # Continue to next iteration for retry
                    continue
                # No more retries, log final failure
                logger.error(
                    f"Scenario '{self._name}' failed after {current_tries} attempts "
                    f"(initial + {self._max_retries} retries) with error: {str(e)}. Giving up.",
                    exc_info=True,
                )
                raise

        # This should never be reached, but just in case
        if last_exception:
            raise last_exception
        raise RuntimeError(f"Scenario '{self._name}' completed unexpectedly without result")

    async def _execute_scenario_async(self) -> ScenarioResult:
        """
        Perform a single execution attempt of the scenario.

        This method contains the core execution logic and can be called multiple times
        for retry attempts. It increments the try counter, executes remaining atomic attacks,
        and returns the scenario result.

        Returns:
            ScenarioResult: The result of this execution attempt.

        Raises:
            Exception: Any exception that occurs during scenario execution.
            ValueError: If a lookup for a scenario for a given ID fails.
            ValueError: If atomic attack execution fails.
        """
        logger.info(f"Starting scenario '{self._name}' execution with {len(self._atomic_attacks)} atomic attacks")

        # Type narrowing: _scenario_result_id is guaranteed to be non-None at this point
        # (verified in run_async before calling this method)
        if self._scenario_result_id is None:
            raise ValueError("self._scenario_result_id is not initialized")
        scenario_result_id: str = self._scenario_result_id

        # Increment number_tries at the start of each run
        scenario_results = self._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
        if scenario_results:
            current_scenario = scenario_results[0]
            current_scenario.number_tries += 1
            entry = ScenarioResultEntry(entry=current_scenario)
            self._memory._update_entry(entry)
            logger.info(f"Scenario '{self._name}' attempt #{current_scenario.number_tries}")
        else:
            raise ValueError(f"Scenario result with ID {scenario_result_id} not found")

        # Get remaining atomic attacks (filters out completed ones and updates objectives)
        remaining_attacks = await self._get_remaining_atomic_attacks_async()

        if not remaining_attacks:
            logger.info(f"Scenario '{self._name}' has no remaining objectives to execute")
            # Mark scenario as completed
            self._memory.update_scenario_run_state(
                scenario_result_id=scenario_result_id, scenario_run_state="COMPLETED"
            )
            # Retrieve and return the current scenario result
            scenario_results = self._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
            if scenario_results:
                return scenario_results[0]
            raise ValueError(f"Scenario result with ID {scenario_result_id} not found")

        logger.info(
            f"Scenario '{self._name}' has {len(remaining_attacks)} atomic attacks "
            f"with remaining objectives (out of {len(self._atomic_attacks)} total)"
        )

        # Mark scenario as in progress
        self._memory.update_scenario_run_state(scenario_result_id=scenario_result_id, scenario_run_state="IN_PROGRESS")

        # Calculate starting index based on completed attacks
        completed_count = len(self._atomic_attacks) - len(remaining_attacks)

        try:
            for i, atomic_attack in enumerate(
                tqdm(
                    remaining_attacks,
                    desc=f"Executing {self._name}",
                    unit="attack",
                    total=len(self._atomic_attacks),
                    initial=completed_count,
                ),
                start=completed_count + 1,
            ):
                logger.info(
                    f"Executing atomic attack {i}/{len(self._atomic_attacks)} "
                    f"('{atomic_attack.atomic_attack_name}') in scenario '{self._name}'"
                )

                try:
                    atomic_results = await atomic_attack.run_async(
                        max_concurrency=self._max_concurrency,
                        return_partial_on_failure=True,
                    )

                    # Always save completed results, even if some objectives didn't complete
                    if atomic_results.completed_results:
                        await self._update_scenario_result_async(
                            atomic_attack_name=atomic_attack.atomic_attack_name,
                            attack_results=atomic_results.completed_results,
                        )

                    # Check if there were any incomplete objectives
                    if atomic_results.has_incomplete:
                        incomplete_count = len(atomic_results.incomplete_objectives)
                        completed_count = len(atomic_results.completed_results)

                        logger.error(
                            f"Atomic attack {i}/{len(self._atomic_attacks)} "
                            f"('{atomic_attack.atomic_attack_name}') partially completed: "
                            f"{completed_count} completed, {incomplete_count} incomplete"
                        )

                        # Log details of each incomplete objective
                        for obj, exc in atomic_results.incomplete_objectives:
                            logger.error(f"  Incomplete objective '{obj[:50]}...': {str(exc)}")

                        # Mark scenario as failed
                        self._memory.update_scenario_run_state(
                            scenario_result_id=scenario_result_id,
                            scenario_run_state="FAILED",
                        )

                        # Raise exception with detailed information
                        raise ValueError(
                            f"Failed to execute atomic attack {i} ('{atomic_attack.atomic_attack_name}') "
                            f"in scenario '{self._name}': {incomplete_count} of {incomplete_count + completed_count} "
                            f"objectives incomplete. First failure: {atomic_results.incomplete_objectives[0][1]}"
                        ) from atomic_results.incomplete_objectives[0][1]
                    logger.info(
                        f"Atomic attack {i}/{len(self._atomic_attacks)} completed successfully with "
                        f"{len(atomic_results.completed_results)} results"
                    )

                except Exception as e:
                    # Exception was raised either by run_async or by our check above
                    logger.error(
                        f"Atomic attack {i}/{len(self._atomic_attacks)} "
                        f"('{atomic_attack.atomic_attack_name}') failed in scenario '{self._name}': {str(e)}"
                    )

                    # Mark scenario as failed if not already done
                    scenario_results = self._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
                    if scenario_results and scenario_results[0].scenario_run_state != "FAILED":
                        self._memory.update_scenario_run_state(
                            scenario_result_id=scenario_result_id,
                            scenario_run_state="FAILED",
                        )

                    raise

            logger.info(f"Scenario '{self._name}' completed successfully")

            # Mark scenario as completed
            self._memory.update_scenario_run_state(
                scenario_result_id=scenario_result_id, scenario_run_state="COMPLETED"
            )

            # Retrieve and return final scenario result
            scenario_results = self._memory.get_scenario_results(scenario_result_ids=[scenario_result_id])
            if not scenario_results:
                raise ValueError(f"Scenario result with ID {self._scenario_result_id} not found")

            return scenario_results[0]

        except Exception as e:
            logger.error(f"Scenario '{self._name}' failed with error: {str(e)}")
            raise
