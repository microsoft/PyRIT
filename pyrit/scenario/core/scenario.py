# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario class for grouping and executing multiple AtomicAttacks.

This module provides the Scenario class that orchestrates the execution of multiple
AtomicAttack instances sequentially, enabling comprehensive security testing campaigns.
"""

import asyncio
import copy
import json
import logging
import uuid
from abc import ABC
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

try:
    # Built-in on Python 3.11+. Fall back to the ``exceptiongroup`` backport on 3.10
    # (declared as a conditional dependency in pyproject.toml).
    from builtins import ExceptionGroup  # type: ignore[attr-defined,ty:unresolved-import]
except ImportError:  # pragma: no cover - exercised only on 3.10
    from exceptiongroup import ExceptionGroup  # type: ignore[no-redef,ty:unresolved-import]

from tqdm.auto import tqdm

from pyrit.common import REQUIRED_VALUE, apply_defaults
from pyrit.common.deprecation import print_deprecation_message
from pyrit.common.utils import to_sha256
from pyrit.executor.attack import AttackExecutor
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.memory.memory_models import ScenarioResultEntry
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ScenarioIdentifier,
    ScenarioResult,
    ScenarioRunState,
    SeedAttackGroup,
)
from pyrit.models.parameter import Parameter
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_requirements import TargetRequirements
from pyrit.registry import ScorerRegistry
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.scenario_strategy import ScenarioStrategy
from pyrit.scenario.core.scenario_target_defaults import get_default_scorer_target
from pyrit.score import (
    Scorer,
    SelfAskRefusalScorer,
    SelfAskTrueFalseScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)

if TYPE_CHECKING:
    from pyrit.models import ComponentIdentifier
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

logger = logging.getLogger(__name__)


class BaselineAttackPolicy(Enum):
    """
    Declares how a scenario type treats the default baseline atomic attack.

    The baseline is a plain ``PromptSendingAttack`` that sends each objective unmodified,
    used as a comparison point against the scenario's strategies. Each scenario class
    declares its policy via ``Scenario.BASELINE_ATTACK_POLICY``; callers can still override
    at runtime via ``initialize_async(include_baseline=...)`` for the ``Enabled`` and
    ``Disabled`` states.
    """

    #: Supported and prepended automatically. Caller can opt out at runtime.
    Enabled = "enabled"

    #: Supported but only included when the caller explicitly requests it.
    Disabled = "disabled"

    #: Not supported. Explicit ``include_baseline=True`` at runtime raises ``ValueError``.
    Forbidden = "forbidden"


def _assert_json_serializable(*, params: dict[str, Any]) -> None:
    """
    Raise if any value in ``params`` cannot round-trip through JSON.

    Stage 5 stores ``params`` on ``ScenarioIdentifier.init_data`` for resume
    validation; the underlying memory column is JSON. Catching unserializable
    values here gives a clear error rather than a database failure.

    Args:
        params (dict[str, Any]): Effective parameters to validate.

    Raises:
        ValueError: If any value is not JSON-serializable.
    """
    try:
        json.dumps(params)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Scenario params contain a non-JSON-serializable value (cannot persist for resume): {exc}. "
            f"Use only JSON-safe types (str, int, float, bool, list, dict, None) for scenario parameters."
        ) from exc


def _format_param_key_diff(*, stored: dict[str, Any], current: dict[str, Any]) -> str:
    """
    Render the set-level difference between two param dicts as a short string.

    Lists only key names (no values) so secrets or large blobs in scenario
    parameters do not leak into logs.

    Args:
        stored (dict[str, Any]): Persisted params from the previous run.
        current (dict[str, Any]): Effective params for the current run.

    Returns:
        str: A short summary like ``"added: x, y; removed: z; changed: max_turns"``.
    """
    parts: list[str] = []
    added = sorted(set(current) - set(stored))
    removed = sorted(set(stored) - set(current))
    changed = sorted(k for k in set(stored) & set(current) if stored[k] != current[k])
    if added:
        parts.append(f"added: {', '.join(added)}")
    if removed:
        parts.append(f"removed: {', '.join(removed)}")
    if changed:
        parts.append(f"changed: {', '.join(changed)}")
    return "; ".join(parts) if parts else "no diff details"


class Scenario(ABC):  # noqa: B024 - retained for subclass type-checking even without abstract methods
    """
    Groups and executes multiple AtomicAttack instances sequentially.

    A Scenario represents a comprehensive testing campaign composed of multiple
    atomic attack tests (AtomicAttacks). It executes each AtomicAttack in sequence and
    aggregates the results into a ScenarioResult.

    Subclasses must use the keyword-only constructor shape (``def __init__(self, *, ...)``);
    the contract is enforced at class-definition time via
    ``enforce_keyword_only_init``. See
    ``.github/instructions/scenarios.instructions.md`` for the full contract.
    """

    #: Capability requirements placed on ``objective_target``. Subclasses override to declare
    #: what the scenario needs. Validated in ``initialize_async`` once the target is supplied.
    TARGET_REQUIREMENTS: ClassVar[TargetRequirements] = TargetRequirements()

    #: How this scenario type treats the default baseline atomic attack. Subclasses override
    #: when their semantics call for a different default (``Disabled``) or when a baseline
    #: is meaningless for the comparison the scenario performs (``Forbidden``). Resolved in
    #: ``initialize_async`` and overridable per run via ``include_baseline`` for the
    #: ``Enabled`` and ``Disabled`` states; ``Forbidden`` is a hard constraint and a
    #: caller-supplied ``include_baseline=True`` raises ``ValueError``.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Enabled

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        Enforce the keyword-only constructor contract on subclasses.

        See ``.github/instructions/scenarios.instructions.md`` for the contract.
        """
        super().__init_subclass__(**kwargs)
        # Local import to avoid a circular dependency at package init time.
        from pyrit.common.brick_contract import enforce_keyword_only_init

        enforce_keyword_only_init(cls, base_name="Scenario")

    @classmethod
    def _get_additional_scoring_questions(cls) -> Sequence[Path]:
        """
        Paths to additional true/false question prompts for objective scoring.

        These prompts are used in the default scenario scorer in addition to a simple self-ask scorer.

        Returns:
            Sequence[Path]: Paths to true/false question prompts, or an empty sequence to use the default scorer.
        """
        return []

    def __init__(
        self,
        *,
        name: str = "",
        version: int,
        strategy_class: type[ScenarioStrategy],
        default_strategy: ScenarioStrategy,
        default_dataset_config: DatasetAttackConfiguration,
        objective_scorer: Scorer,
        scenario_result_id: uuid.UUID | str | None = None,
        include_default_baseline: bool | None = None,  # Deprecated. Will be removed in 0.16.0.
    ) -> None:
        """
        Initialize a scenario.

        Args:
            name (str): Descriptive name for the scenario.
            version (int): Version number of the scenario.
            strategy_class (type[ScenarioStrategy]): The strategy enum class for this scenario.
            default_strategy (ScenarioStrategy): The default strategy member used when no
                ``scenario_strategies`` are passed to ``initialize_async``. Usually an aggregate
                member like ``MyStrategy.ALL`` or ``MyStrategy.DEFAULT``.
            default_dataset_config (DatasetAttackConfiguration): The default dataset configuration used
                when no ``dataset_config`` is passed to ``initialize_async``.
            objective_scorer (Scorer): The objective scorer used to evaluate attack results.
            scenario_result_id (uuid.UUID | str | None): Optional ID of an existing scenario result to resume.
                Can be either a UUID object or a string representation of a UUID.
                If provided and found in memory, the scenario will resume from prior progress.
                All other parameters must still match the stored scenario configuration.
            include_default_baseline (bool | None): **Deprecated.** Will be removed in 0.16.0.
                Pass ``include_baseline`` to ``initialize_async`` instead. When set, the value is
                used as the effective ``include_baseline`` for the next ``initialize_async`` call
                unless that call passes its own ``include_baseline``.

        Note:
            Attack runs are populated by calling initialize_async(), which invokes the
            subclass's _get_atomic_attacks_async() method.

            The scenario description is automatically extracted from the class's docstring (__doc__)
            with whitespace normalized for display.
        """
        from pyrit.registry.base import ClassRegistryEntry

        description = ClassRegistryEntry.description_from_docstring(self.__class__)

        self._identifier = ScenarioIdentifier(
            name=type(self).__name__, scenario_version=version, description=description
        )

        # Store strategy configuration for use in initialize_async
        self._strategy_class = strategy_class
        self._default_strategy = default_strategy
        self._default_dataset_config = default_dataset_config

        # These will be set in initialize_async
        self._objective_target: PromptTarget | None = None
        self._objective_target_identifier: ComponentIdentifier | None = None
        self._memory_labels: dict[str, str] = {}
        self._max_concurrency: int | None = None
        self._max_retries: int = 0

        self._objective_scorer = objective_scorer
        self._objective_scorer_identifier = objective_scorer.get_identifier()

        self._name = name if name else type(self).__name__
        self._memory = CentralMemory.get_memory_instance()
        self._atomic_attacks: list[AtomicAttack] = []
        self._scenario_result_id: str | None = str(scenario_result_id) if scenario_result_id else None

        # Store prepared strategies for use in _get_atomic_attacks_async
        self._scenario_strategies: list[ScenarioStrategy] = []

        # Maps atomic_attack_name → display_group for user-facing aggregation
        self._display_group_map: dict[str, str] = {}

        # Custom parameters: declared via supported_parameters(), populated via set_params_from_args().
        self.params: dict[str, Any] = {}
        self._declarations_validated: bool = False

        # Resolved effective baseline inclusion for the current run. Set in initialize_async
        # before _get_atomic_attacks_async is awaited so overrides can read it.
        self._include_baseline: bool = False

        # Deprecated constructor-time baseline override. Will be removed in 0.16.0, along
        # with the include_default_baseline kwarg above and the legacy fallback branch in
        # initialize_async. Subclass shims set this attribute directly to avoid double-warning.
        self._legacy_include_baseline: bool | None = None
        if include_default_baseline is not None:
            print_deprecation_message(
                old_item="Scenario(include_default_baseline=...)",
                new_item="Scenario.initialize_async(include_baseline=...)",
                removed_in="0.16.0",
            )
            self._legacy_include_baseline = include_default_baseline

    @property
    def name(self) -> str:
        """The name of the scenario."""
        return self._name

    @property
    def atomic_attack_count(self) -> int:
        """The number of atomic attacks in this scenario."""
        return len(self._atomic_attacks)

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        """
        Override to declare custom parameters this scenario accepts.

        Declared parameters flow from CLI/config through ``set_params_from_args``
        into ``self.params`` before ``initialize_async()`` runs. Implemented as
        a classmethod so ``--list-scenarios`` can introspect without instantiating.

        Note: ``PyRITInitializer.supported_parameters`` is an instance ``@property``;
        this asymmetry is intentional pending a future alignment.

        Returns:
            list[Parameter]: Declared parameters (default: empty list).
        """
        return []

    def _get_attack_technique_factories(self) -> dict[str, "AttackTechniqueFactory"]:
        """
        Return the attack technique factories for this scenario.

        Each key is a technique name (matching a strategy enum value) and each
        value is an ``AttackTechniqueFactory`` that can produce an
        ``AttackTechnique`` for that technique.

        The base implementation returns every factory currently registered in
        the ``AttackTechniqueRegistry`` singleton. The canonical scenario
        techniques are populated by ``ScenarioTechniqueInitializer``
        (``pyrit.setup.initializers.components.scenario_techniques``); ensure
        that initializer has run before scenarios use this method.
        Subclasses may override to add, remove, or replace factories.

        Returns:
            dict[str, AttackTechniqueFactory]: Mapping of technique name to factory.

        Raises:
            RuntimeError: If the registry is empty (no initializer has run).
        """
        from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry

        registry = AttackTechniqueRegistry.get_registry_singleton()
        return registry.get_factories_or_raise()

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
        ``DatasetAttackConfiguration.get_attack_groups_by_dataset_async()`` (e.g.
        ``"airt_hate"``), not a ``SeedGroup`` object.

        Args:
            technique_name: The name of the attack technique.
            seed_group_name: The dataset key from the dataset configuration.

        Returns:
            str: The display-group label.
        """
        return technique_name

    def _get_default_objective_scorer(self) -> TrueFalseScorer:
        # Deferred import to avoid circular dependency.
        from pyrit.setup.initializers.components.scorers import ScorerInitializerTags

        # first check if the registry has a default objective scorer
        # if available either itself, or its chat target will be used
        chat_target: PromptTarget | None = None
        registry_default_scorer: TrueFalseScorer | None = None
        entries = ScorerRegistry.get_registry_singleton().get_by_tag(tag=ScorerInitializerTags.DEFAULT_OBJECTIVE_SCORER)
        if entries and isinstance(entries[0].instance, TrueFalseScorer):
            registry_default_scorer = entries[0].instance
            chat_target = registry_default_scorer.get_chat_target()
            logger.info(
                f"The registry contains default objective scorer: {type(registry_default_scorer).__name__} "
                f"with chat target: {type(chat_target).__name__ if chat_target else 'None'}"
            )

        chat_target = chat_target or get_default_scorer_target()

        # if the scenario has override composite scorer questions, use them to build a composite scorer
        composite_scorer_questions_paths = type(self)._get_additional_scoring_questions()
        if composite_scorer_questions_paths:
            path_scorers: list[TrueFalseScorer] = [
                SelfAskTrueFalseScorer(chat_target=chat_target, true_false_question_path=path)
                for path in composite_scorer_questions_paths
            ]
            backstop_scorer = TrueFalseInverterScorer(scorer=SelfAskRefusalScorer(chat_target=chat_target))
            scorer = TrueFalseCompositeScorer(
                aggregator=TrueFalseScoreAggregator.AND,
                scorers=[*path_scorers, backstop_scorer],
            )
            logger.info(
                f"Using composite default objective scorer: {type(scorer).__name__} "
                f"with chat target: {type(chat_target).__name__}"
            )
            return scorer

        if registry_default_scorer:
            logger.info(
                f"Using registry default objective scorer: {type(registry_default_scorer).__name__} "
                f"with chat target: {type(chat_target).__name__ if chat_target else 'None'}"
            )
            return registry_default_scorer

        scorer = TrueFalseInverterScorer(scorer=SelfAskRefusalScorer(chat_target=chat_target))
        logger.warning(
            f"Using fallback default objective scorer: {type(scorer).__name__} "
            f"with chat target: {type(chat_target).__name__ if chat_target else 'None'}"
        )
        return scorer

    def set_params_from_args(self, *, args: dict[str, Any]) -> None:
        """
        Populate ``self.params`` from merged CLI / config arguments.

        Coerces each value to its declared ``param_type``, validates, and
        materializes declared defaults for params not in ``args``. Every
        declared parameter is guaranteed a key in ``self.params`` after this
        call; params without a declared default land as ``None``.

        Args:
            args (dict[str, Any]): Map of parameter name to raw value. Keys
                with ``None`` values are treated as absent (YAML ``null``).
                Argparse callers should use ``argparse.SUPPRESS``.

        Raises:
            ValueError: Invalid declaration, unknown parameter, coercion
                failure, or value not in ``choices``.
        """
        declared = list(self.supported_parameters())
        if not self._declarations_validated:
            self._validate_declarations(declared=declared)
            self._declarations_validated = True

        declared_by_name = {p.name: p for p in declared}

        # None values are treated as absent so YAML `key: null` falls through to defaults.
        supplied = {name: value for name, value in args.items() if value is not None}

        coerced: dict[str, Any] = {}
        for name, raw_value in supplied.items():
            param = declared_by_name.get(name)
            if param is None:
                # Stash unknowns so _validate_params can list them all at once.
                coerced[name] = raw_value
                continue
            coerced[name] = param.coerce_value(raw_value)

        self._validate_params(params=coerced, declared=declared)

        for param in declared:
            if param.name in coerced:
                continue
            # Materialize every declared param so scenarios can rely on
            # ``self.params[name]`` never raising ``KeyError``. Params declared
            # without an explicit default land as None, and the scenario raises
            # a domain-specific error at run time if it cannot proceed.
            coerced[param.name] = (
                copy.deepcopy(param.coerce_value(param.default)) if param.default is not None else None
            )

        self.params = coerced

    def _validate_declarations(self, *, declared: list[Parameter]) -> None:
        """
        Validate the scenario's parameter declarations on first use.

        Args:
            declared (list[Parameter]): The ``supported_parameters()`` snapshot.

        Raises:
            ValueError: If declarations contain duplicate names, an
                unsupported ``param_type``, or a default that fails coercion
                (including membership for a constrained scalar).
        """
        seen: set[str] = set()
        for param in declared:
            if param.name in seen:
                raise ValueError(f"Scenario '{type(self).__name__}' declares duplicate parameter name '{param.name}'.")
            seen.add(param.name)

            try:
                param.validate()
            except ValueError as exc:
                raise ValueError(f"Scenario '{type(self).__name__}' {exc}") from exc

            if param.default is not None:
                try:
                    param.coerce_value(param.default)
                except ValueError as exc:
                    raise ValueError(
                        f"Scenario '{type(self).__name__}' parameter '{param.name}' has an invalid default: {exc}"
                    ) from exc

    def _validate_params(self, *, params: dict[str, Any], declared: list[Parameter]) -> None:
        """
        Validate supplied params against the scenario's declarations.

        Args:
            params (dict[str, Any]): Coerced (declared names) or raw (unknown) values.
            declared (list[Parameter]): Declarations snapshot from the caller, so
                the whole call sees one consistent view.

        Raises:
            ValueError: If any keys in ``params`` are not declared.
        """
        declared_names = {p.name for p in declared}

        unknown = sorted(set(params.keys()) - declared_names)
        if unknown:
            raise ValueError(
                f"Scenario '{type(self).__name__}' received unknown parameter(s): {', '.join(unknown)}. "
                f"Supported parameters: "
                f"{', '.join(sorted(declared_names)) if declared_names else 'none'}."
            )

    def _prepare_strategies(
        self,
        strategies: Sequence[ScenarioStrategy] | None,
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
        return self._strategy_class.resolve(strategies, default=self._default_strategy)

    @apply_defaults
    async def initialize_async(
        self,
        *,
        objective_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        scenario_strategies: Sequence[ScenarioStrategy] | None = None,
        dataset_config: DatasetAttackConfiguration | None = None,
        max_concurrency: int = 4,
        max_retries: int = 0,
        memory_labels: dict[str, str] | None = None,
        include_baseline: bool | None = None,
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
            scenario_strategies (Sequence[ScenarioStrategy] | None): The strategies to execute.
                Can be a list of ScenarioStrategy enum members. If None, uses the default aggregate
                from the scenario's configuration.
            dataset_config (DatasetAttackConfiguration | None): Configuration for the dataset source.
                Use this to specify dataset names or maximum dataset size from the CLI.
                If not provided, scenarios use their constructor-supplied default_dataset_config.
            max_concurrency (int): Maximum number of concurrent units of work for the scenario.
                Defaults to 4. A "unit of work" is one parameter-build call (turning a seed
                group into attack parameters) or one attack execution (running a single
                ``objective × attack`` pair). All atomic attacks in the scenario share a
                single ``AttackExecutor`` whose internal semaphore caps in-flight units at
                ``max_concurrency``: e.g. ``max_concurrency=4`` means at most 4 such units
                are in flight at any time, regardless of how many atomic attacks or
                objectives the scenario has.
            max_retries (int): Maximum number of automatic retries if the scenario raises an exception.
                Set to 0 (default) for no automatic retries. If set to a positive number,
                the scenario will automatically retry up to this many times after an exception.
                For example, max_retries=3 allows up to 4 total attempts (1 initial + 3 retries).
            memory_labels (dict[str, str] | None): Additional labels to apply to all
                attack runs in the scenario. These help track and categorize the scenario.
            include_baseline (bool | None): Whether to prepend a baseline atomic attack that sends
                all objectives without modifications, allowing comparison between unmodified prompts
                and the scenario's strategies. If None (the default), the scenario type's
                ``BASELINE_ATTACK_POLICY`` class attribute decides: ``Enabled`` includes it,
                ``Disabled`` omits it, and ``Forbidden`` always omits it (and rejects an
                explicit ``True``). Passing ``True`` to a scenario whose ``BASELINE_ATTACK_POLICY``
                is ``Forbidden`` raises ``ValueError``.

        Raises:
            ValueError: If no objective_target is provided, or if ``include_baseline=True`` is passed
                to a scenario whose ``BASELINE_ATTACK_POLICY`` is ``Forbidden``.
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
        type(self).TARGET_REQUIREMENTS.validate(target=objective_target)
        self._dataset_config_provided = dataset_config is not None
        self._dataset_config = dataset_config if dataset_config else self._default_dataset_config
        self._max_concurrency = max_concurrency
        self._max_retries = max_retries
        self._memory_labels = memory_labels or {}

        # Deprecated. Will be removed in 0.16.0. Honor the legacy constructor-time
        # include_default_baseline (or subclass include_baseline) only when the caller did
        # not supply a runtime value.
        if include_baseline is None and self._legacy_include_baseline is not None:
            include_baseline = self._legacy_include_baseline

        # Resolve the effective include_baseline. Forbidden is checked first so a forbidden
        # scenario type never silently inherits a True default; explicit-True on a forbidden
        # type is a hard error rather than a silent ignore. For the Enabled / Disabled states,
        # a None runtime value defers to the policy.
        if self.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Forbidden:
            if include_baseline is True:
                raise ValueError(
                    f"{type(self).__name__} does not support a default baseline "
                    f"(BASELINE_ATTACK_POLICY = Forbidden); pass include_baseline=False or omit the argument."
                )
            include_baseline = False
        elif include_baseline is None:
            include_baseline = self.BASELINE_ATTACK_POLICY is BaselineAttackPolicy.Enabled

        self._include_baseline = include_baseline

        # Prepare scenario strategies using the stored configuration
        self._scenario_strategies = self._prepare_strategies(scenario_strategies)

        # Materialize declared defaults for programmatic callers that skip the
        # explicit set_params_from_args step. Frontend-driven flows already
        # call it (which sets _declarations_validated=True), so this is a no-op
        # in that path.
        if not self._declarations_validated:
            self.set_params_from_args(args={})

        self._atomic_attacks = await self._get_atomic_attacks_async()

        # Deprecation rescue. Will be removed in 0.16.0. If the override didn't emit baseline,
        # warn and inject. Migrated overrides emit baseline themselves and bypass this branch.
        # Reuse seeds from the first existing attack rather than re-resolving from
        # dataset_config; re-resolution under max_dataset_size would draw a fresh sample
        # (the very ADO 9012 bug this PR fixes). When no atomic attacks exist yet the
        # rescue falls back to the dataset_config one-time resolution.
        if include_baseline and (not self._atomic_attacks or self._atomic_attacks[0].atomic_attack_name != "baseline"):
            print_deprecation_message(
                old_item=f"Implicit baseline injection for {type(self).__name__}._get_atomic_attacks_async()",
                new_item="explicit emission via self._build_baseline_atomic_attack(seed_groups=...) in the override",
                removed_in="0.16.0",
            )
            if self._atomic_attacks:
                seed_groups = self._atomic_attacks[0].seed_groups
            else:
                seed_groups = await self._dataset_config.get_seed_attack_groups_async()
            self._atomic_attacks.insert(0, self._build_baseline_atomic_attack(seed_groups=seed_groups))

        # Snapshot params onto the identifier before the resume branch so the identifier
        # is fully populated regardless of which branch we take. Deep-copy avoids sharing
        # mutable state with self.params.
        params_snapshot = copy.deepcopy(self.params)
        _assert_json_serializable(params=params_snapshot)
        self._identifier.init_data = params_snapshot

        # Check if we're resuming an existing scenario. Any divergence is a hard error
        # rather than a silent restart, so the original progress isn't orphaned without
        # the user knowing.
        if self._scenario_result_id:
            existing_results = self._memory.get_scenario_results(scenario_result_ids=[self._scenario_result_id])

            if not existing_results:
                raise ValueError(
                    f"Scenario result id '{self._scenario_result_id}' not found in memory. "
                    f"Drop scenario_result_id to start a new scenario."
                )

            self._validate_stored_scenario(stored_result=existing_results[0])
            self._apply_persisted_objectives(stored_result=existing_results[0])
            return  # Valid resume - skip creating new scenario result

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
            scenario_run_state=ScenarioRunState.CREATED,
            display_group_map=self._display_group_map,
            metadata=self._build_initial_scenario_metadata(),
        )

        self._memory.add_scenario_results_to_memory(scenario_results=[result])
        self._scenario_result_id = str(result.id)
        logger.info(f"Created new scenario result with ID: {self._scenario_result_id}")

    def _build_initial_scenario_metadata(self) -> dict[str, Any]:
        """
        Build the metadata dict persisted with a freshly-created ``ScenarioResult``.

        When ``max_dataset_size`` is in effect, the dataset config draws an
        unseeded ``random.sample`` and the chosen subset would silently change
        on the next run (e.g. a resume). To make resume reliable, snapshot the
        chosen objective hashes here so the next ``_setup_scenario_async`` can
        replay them via ``keep_seed_groups_with_hashes``.

        When ``max_dataset_size`` is not set, the sample equals the dataset and
        nothing needs pinning; the dict is empty.

        Returns:
            dict[str, Any]: Metadata payload for the new ScenarioResult.
        """
        metadata: dict[str, Any] = {}
        if getattr(self._dataset_config, "max_dataset_size", None) is None:
            return metadata
        hashes: list[str] = []
        seen: set[str] = set()
        for aa in self._atomic_attacks:
            for sg in aa.seed_groups:
                if sg.objective is None:
                    continue
                sha = to_sha256(sg.objective.value)
                if sha not in seen:
                    seen.add(sha)
                    hashes.append(sha)
        metadata["objective_hashes"] = hashes
        return metadata

    def _apply_persisted_objectives(self, *, stored_result: ScenarioResult) -> None:
        """
        On resume, replay the originally-sampled objective subset.

        When the first run used ``max_dataset_size``, the chosen subset was
        recorded in ``ScenarioResult.metadata["objective_hashes"]``.
        Restrict each atomic attack's freshly-resolved seed_groups to that set
        so a fresh ``random.sample`` draw on resume can't silently shift which
        objectives the scenario operates on. If any persisted hash is no longer
        present in the dataset, refuse to resume — running a smaller subset
        than the user committed to would silently produce different results.

        Args:
            stored_result (ScenarioResult): The scenario result loaded from memory.

        Raises:
            ValueError: If any persisted objective hash is missing from the
                currently-resolved dataset.
        """
        metadata = stored_result.metadata or {}
        persisted = metadata.get("objective_hashes")
        if not persisted:
            return

        persisted_hashes: set[str] = set(persisted)
        retained: set[str] = set()
        for aa in self._atomic_attacks:
            retained |= aa.keep_seed_groups_with_hashes(hashes=persisted_hashes)

        missing = persisted_hashes - retained
        if missing:
            sample = sorted(missing)[:3]
            raise ValueError(
                f"Scenario result id '{self._scenario_result_id}' cannot resume: "
                f"{len(missing)} persisted objective hash(es) are no longer present in the dataset "
                f"(missing examples: {', '.join(h[:12] + '...' for h in sample)}). "
                f"Either restore the missing objectives or drop scenario_result_id to start a new scenario."
            )

    def _build_baseline_atomic_attack(self, *, seed_groups: list[SeedAttackGroup]) -> AtomicAttack:
        """
        Build the baseline AtomicAttack from pre-resolved seed groups.

        The baseline sends each objective unmodified, providing a comparison point
        against the scenario's strategy attacks. Pass the same ``seed_groups`` used
        to build the strategy attacks so both populations match.

        Args:
            seed_groups: Seed groups to attack. Used as-is, no further sampling.

        Returns:
            AtomicAttack: The baseline atomic attack.

        Raises:
            ValueError: If ``initialize_async`` has not been called (no objective
                target or scorer set).
        """
        if self._objective_target is None:
            raise ValueError("Objective target is required to create baseline attack.")
        if self._objective_scorer is None:
            raise ValueError("Objective scorer is required to create baseline attack.")

        from pyrit.executor.attack.core.attack_config import AttackScoringConfig

        attack = PromptSendingAttack(
            objective_target=self._objective_target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=cast("TrueFalseScorer", self._objective_scorer)),
        )

        return AtomicAttack(
            atomic_attack_name="baseline",
            attack_technique=AttackTechnique(attack=attack),
            seed_groups=seed_groups,
            memory_labels=self._memory_labels,
        )

    def _validate_stored_scenario(self, *, stored_result: ScenarioResult) -> None:
        """
        Validate that a stored scenario result exactly matches the current scenario configuration.

        Resume is opt-in via ``scenario_result_id``; any divergence from the stored
        result is treated as user error rather than a silent restart, since the
        original progress would otherwise be orphaned without warning.

        Args:
            stored_result (ScenarioResult): The scenario result retrieved from memory.

        Raises:
            ValueError: If the stored scenario name, version, or parameters do not
                match the current configuration.
        """
        stored_name = stored_result.scenario_identifier.name
        stored_version = stored_result.scenario_identifier.version

        if stored_name != self._identifier.name:
            raise ValueError(
                f"Scenario result id '{self._scenario_result_id}' belongs to scenario '{stored_name}' "
                f"but current scenario is '{self._identifier.name}'. "
                f"Drop scenario_result_id to start a new scenario."
            )

        if stored_version != self._identifier.version:
            raise ValueError(
                f"Scenario result id '{self._scenario_result_id}' was created with "
                f"{self._identifier.name} version {stored_version} but current version is "
                f"{self._identifier.version}. Drop scenario_result_id to start a new scenario."
            )

        # Treat None (legacy result without persisted params) as empty. Compare both sides
        # post-JSON-roundtrip so types that the memory column rewrites (tuple → list, non-str
        # dict keys → str) don't surface as false mismatches under param_type=None.
        stored_params = stored_result.scenario_identifier.init_data or {}
        current_params_normalized = json.loads(json.dumps(self.params))
        if stored_params != current_params_normalized:
            diff = _format_param_key_diff(stored=stored_params, current=current_params_normalized)
            raise ValueError(
                f"Scenario result id '{self._scenario_result_id}' has mismatched parameters ({diff}). "
                f"Drop scenario_result_id to start a new scenario, or pass matching parameters to resume."
            )

        logger.info(
            f"Resuming scenario '{self._name}' from existing result "
            f"(ID: {self._scenario_result_id}, state: {stored_result.scenario_run_state})"
        )

    def _get_completed_objective_hashes_for_attack(self, *, atomic_attack: AtomicAttack) -> set[str]:
        """
        Return the set of ``objective_sha256`` values already completed (non-error)
        for a specific atomic attack inside this scenario.

        Queries ``AttackResultEntry`` rows directly by ``attribution_parent_id`` —
        which is stamped at write-time by the attack persistence path — so
        results from an interrupted run are visible even though the
        ``ScenarioResult.attack_results`` aggregate may not yet reflect them.
        Identity is content-derived (``to_sha256(objective)``), so it stays
        stable even if ``get_seed_groups()`` reorders or resamples between runs.

        Rows are matched on ``(parent_collection, parent_eval_hash)`` so that
        two ``AtomicAttack`` instances sharing a name but using different
        techniques (e.g. base64 vs hex encoders) never cross-pollinate their
        completed-hash sets on resume. Rows persisted before
        ``parent_eval_hash`` was introduced (or by callers that don't supply
        one) match name-only as a backward-compatible fallback.

        Args:
            atomic_attack (AtomicAttack): The live atomic attack whose
                ``atomic_attack_name`` and technique identifier scope the query.

        Returns:
            set[str]: ``objective_sha256`` hex strings for completed-without-error rows.
        """
        if not self._scenario_result_id:
            return set()

        atomic_attack_name = atomic_attack.atomic_attack_name
        expected_eval_hash = atomic_attack.technique_eval_hash

        completed_hashes: set[str] = set()
        try:
            rows = self._memory.get_attack_results(scenario_result_id=self._scenario_result_id)
            for row in rows:
                if row.outcome == AttackOutcome.ERROR:
                    continue
                if row.attribution_data is None:
                    continue
                if row.attribution_data.get("parent_collection") != atomic_attack_name:
                    continue
                row_eval_hash = row.attribution_data.get("parent_eval_hash")
                if row_eval_hash is not None and row_eval_hash != expected_eval_hash:
                    continue
                if row.objective:
                    completed_hashes.add(to_sha256(row.objective))
        except Exception as e:
            logger.warning(
                f"Failed to retrieve completed objective hashes for atomic attack '{atomic_attack_name}': {str(e)}"
            )

        return completed_hashes

    async def _get_remaining_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Get the list of atomic attacks that still have objectives to complete.

        Uses ``objective_sha256`` as the stable identity for resume: each
        atomic attack enforces uniqueness of objective hashes at construction
        time, and the executor stamps ``attribution_parent_id`` +
        ``attribution_data["parent_collection"]`` on the row so a content-hash
        join is sufficient.

        Returns:
            list[AtomicAttack]: List of atomic attacks with uncompleted objectives.
        """
        if not self._scenario_result_id:
            # No scenario result yet, return all atomic attacks
            return self._atomic_attacks

        remaining_attacks: list[AtomicAttack] = []

        for atomic_attack in self._atomic_attacks:
            completed_hashes = self._get_completed_objective_hashes_for_attack(atomic_attack=atomic_attack)

            if completed_hashes:
                original_count = len(atomic_attack.seed_groups)
                atomic_attack.drop_seed_groups_with_hashes(hashes=completed_hashes)
                remaining_count = len(atomic_attack.seed_groups)
                if remaining_count == 0:
                    logger.info(
                        f"Atomic attack '{atomic_attack.atomic_attack_name}' has all objectives completed, skipping"
                    )
                    continue
                if remaining_count < original_count:
                    logger.info(
                        f"Atomic attack '{atomic_attack.atomic_attack_name}' has "
                        f"{remaining_count}/{original_count} objectives remaining"
                    )

            remaining_attacks.append(atomic_attack)

        return remaining_attacks

    async def _get_atomic_attacks_async(self) -> list[AtomicAttack]:
        """
        Build atomic attacks from the cross-product of selected techniques and datasets.

        Uses ``_get_attack_technique_factories()`` to obtain factories, then
        iterates over every (technique, dataset) pair to create an
        ``AtomicAttack`` for each.  Grouping for display is controlled by
        ``_build_display_group()``.

        Subclasses that do **not** use the factory/registry pattern should
        override this method entirely. Overrides that want baseline support
        must call ``self._build_baseline_atomic_attack`` with the strategy
        seeds.

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

        selected_techniques = {s.value for s in self._scenario_strategies}

        factories = self._get_attack_technique_factories()
        seed_groups_by_dataset = await self._dataset_config.get_attack_groups_by_dataset_async()

        scoring_config = AttackScoringConfig(objective_scorer=cast("TrueFalseScorer", self._objective_scorer))

        atomic_attacks: list[AtomicAttack] = []
        for technique_name in selected_techniques:
            factory = factories.get(technique_name)
            if factory is None:
                logger.warning(f"No factory for technique '{technique_name}', skipping.")
                continue

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
                    attack_scoring_config=scoring_config,
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

        if self._include_baseline:
            all_seed_groups = [g for groups in seed_groups_by_dataset.values() for g in groups]
            atomic_attacks.insert(0, self._build_baseline_atomic_attack(seed_groups=all_seed_groups))

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

        # Run atomic attacks through a worker pool sharing a single AttackExecutor-level
        # Semaphore(max_concurrency) so the global in-flight budget (parameter-build +
        # attack-execution units of work) never exceeds max_concurrency, regardless of
        # how work is distributed across atomic attacks. At max_concurrency=1 the pool
        # reduces to a single worker, naturally giving serial execution with
        # abort-on-first-failure.
        try:
            await self._execute_atomic_attacks_parallel_async(
                remaining_attacks=remaining_attacks,
                scenario_result_id=scenario_result_id,
                completed_count=completed_count,
            )

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

    def _partial_result_to_exception(
        self,
        *,
        atomic_attack: AtomicAttack,
        atomic_results: Any,
    ) -> ValueError | None:
        """
        Log the outcome of an atomic attack and return an exception if it didn't
        fully complete.

        Returns:
            ValueError | None: An error to raise when the atomic attack has incomplete
            objectives, otherwise ``None`` when all objectives finished successfully.
        """
        if not atomic_results.has_incomplete:
            logger.info(
                f"Atomic attack ('{atomic_attack.atomic_attack_name}') completed successfully with "
                f"{len(atomic_results.completed_results)} results"
            )
            return None

        incomplete_count = len(atomic_results.incomplete_objectives)
        completed_in_run = len(atomic_results.completed_results)
        logger.error(
            f"Atomic attack ('{atomic_attack.atomic_attack_name}') partially completed: "
            f"{completed_in_run} completed, {incomplete_count} incomplete"
        )
        for obj, exc in atomic_results.incomplete_objectives:
            logger.error(f"  Incomplete objective '{obj[:50]}...': {str(exc)}")

        inner = atomic_results.incomplete_objectives[0][1]
        error = ValueError(
            f"Atomic attack '{atomic_attack.atomic_attack_name}' partially failed: "
            f"{incomplete_count} of {incomplete_count + completed_in_run} objectives incomplete. "
            f"See attack results for details."
        )
        if isinstance(inner, BaseException):
            error.__cause__ = inner
        return error

    def _mark_scenario_failed(self, *, scenario_result_id: str, error: BaseException) -> None:
        """Mark the scenario run as FAILED, deriving message/type from ``error``."""
        cause = error.__cause__ if error.__cause__ is not None else error
        self._memory.update_scenario_run_state(
            scenario_result_id=scenario_result_id,
            scenario_run_state="FAILED",
            error_message=str(error),
            error_type=type(cause).__name__,
        )

    async def _execute_atomic_attacks_parallel_async(
        self,
        *,
        remaining_attacks: list[AtomicAttack],
        scenario_result_id: str,
        completed_count: int,
    ) -> None:
        """
        Execute remaining atomic attacks concurrently via a worker pool.

        At most ``max_concurrency`` atomic attacks are in-flight at any time, and all
        of their per-objective tasks share a single ``AttackExecutor`` (and therefore a
        single internal ``Semaphore(max_concurrency)``) so the global concurrent-objective
        budget never exceeds ``max_concurrency`` regardless of how work is distributed
        across atomic attacks.

        Failure semantics: when an in-flight atomic attack raises or returns
        ``has_incomplete``, the worker pool stops pulling new atomic attacks from the
        queue. Already-started atomic attacks are allowed to finish (so their partial
        work persists for resume). If more than one in-flight attack ends up failing,
        every failure is surfaced: a single failure is re-raised as-is, multiple
        failures are wrapped in an ``ExceptionGroup`` so callers see all of them.
        """
        # Type narrowing: initialize_async always sets _max_concurrency to an int. We hold
        # the narrowed value in a local so the type checker can verify all uses below.
        assert self._max_concurrency is not None, "Scenario not initialized; call initialize_async first."
        max_concurrency: int = self._max_concurrency

        shared_executor = AttackExecutor(max_concurrency=max_concurrency)
        pbar = tqdm(
            desc=f"Executing {self._name}",
            unit="attack",
            total=len(self._atomic_attacks),
            initial=completed_count,
        )

        for atomic_attack in remaining_attacks:
            atomic_attack.set_scenario_result_id(scenario_result_id)

        logger.info(
            f"Launching {len(remaining_attacks)} atomic attacks in parallel "
            f"(shared max_concurrency={max_concurrency}) in scenario '{self._name}'"
        )

        queue: asyncio.Queue[AtomicAttack] = asyncio.Queue()
        for atomic_attack in remaining_attacks:
            queue.put_nowait(atomic_attack)

        stop_event = asyncio.Event()
        outcomes: list[tuple[AtomicAttack, Any] | BaseException] = []

        async def worker_async() -> None:
            while not stop_event.is_set():
                try:
                    atomic_attack = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    result = await atomic_attack.run_async(
                        executor=shared_executor,
                        return_partial_on_failure=True,
                    )
                    outcomes.append((atomic_attack, result))
                    if result.has_incomplete:
                        stop_event.set()
                except Exception as exc:
                    outcomes.append(exc)
                    stop_event.set()
                finally:
                    pbar.update(1)

        # Cap workers at max_concurrency: that's also the objective-budget cap, and it's
        # the natural place to enforce "don't start new atomic attacks after a failure"
        # without losing parallelism for the common case where remaining_attacks fits in
        # the budget.
        worker_count = min(max_concurrency, len(remaining_attacks))
        try:
            await asyncio.gather(*(worker_async() for _ in range(worker_count)))
        finally:
            pbar.close()

        errors = self._collect_errors_from_outcomes(outcomes=outcomes)
        if errors:
            # Single failure: re-raise as-is to keep simple cases readable. Multiple
            # failures: wrap in ExceptionGroup so the caller sees every one — logging
            # alone is easy to miss.
            final_error: BaseException = (
                errors[0]
                if len(errors) == 1
                else ExceptionGroup(f"Multiple atomic attacks failed in scenario '{self._name}'", errors)
            )
            self._mark_scenario_failed(scenario_result_id=scenario_result_id, error=final_error)
            raise final_error

    def _collect_errors_from_outcomes(
        self,
        *,
        outcomes: list[tuple[AtomicAttack, Any] | BaseException],
    ) -> list[BaseException]:
        """
        Convert worker outcomes into a flat list of errors for the caller to raise.

        Each outcome is either:
            - ``BaseException``: the atomic attack raised; log and surface as-is.
            - ``(AtomicAttack, result)``: ran to completion. If the result reports
              incomplete objectives, ``_partial_result_to_exception`` produces a
              synthetic ``ValueError`` so partial failures are surfaced the same
              way as raised exceptions.

        Returns:
            list[BaseException]: One exception per failed atomic attack, preserving
                worker-completion order. Empty if every atomic attack succeeded.
        """
        errors: list[BaseException] = []
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                logger.error(f"Atomic attack failed in scenario '{self._name}': {str(outcome)}")
                error: BaseException | None = outcome
            else:
                atomic_attack, atomic_results = outcome
                error = self._partial_result_to_exception(atomic_attack=atomic_attack, atomic_results=atomic_results)
            if error is not None:
                errors.append(error)
        return errors
