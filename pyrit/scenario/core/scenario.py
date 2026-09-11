# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Scenario class for grouping and executing multiple AtomicAttacks.

This module provides the Scenario class that orchestrates the execution of multiple
AtomicAttack instances sequentially, enabling comprehensive security testing campaigns.
"""

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, final

try:
    # Built-in on Python 3.11+. Fall back to the ``exceptiongroup`` backport on 3.10
    # (declared as a conditional dependency in pyproject.toml).
    from builtins import ExceptionGroup  # type: ignore[attr-defined,ty:unresolved-import]
except ImportError:  # pragma: no cover - exercised only on 3.10
    from exceptiongroup import ExceptionGroup  # type: ignore[no-redef,ty:unresolved-import]

from tqdm.auto import tqdm

from pyrit.common import get_global_default_values
from pyrit.common.utils import to_sha256
from pyrit.exceptions import ScenarioPartialFailureException
from pyrit.executor.attack import AttackExecutor, AttackExecutorResult
from pyrit.memory import CentralMemory
from pyrit.memory.memory_models import ScenarioResultEntry
from pyrit.models import (
    SCENARIO_RUN_PLAN_METADATA_KEY,
    AttackOutcome,
    AttackResult,
    AttackSeedGroup,
    ScenarioDatasetSizeCap,
    ScenarioDatasetSummary,
    ScenarioEvaluationIdentifier,
    ScenarioIdentifier,
    ScenarioResult,
    ScenarioRunPlan,
    ScenarioRunPlanAtomicGroup,
    ScenarioRunPlanSeedGroup,
    ScenarioRunPlanSeedPrompt,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
    ScenarioRunState,
    config_hash,
)
from pyrit.models.parameter import ComponentType, Parameter, RegistryReference
from pyrit.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_requirements import TargetRequirements
from pyrit.registry import ScorerRegistry
from pyrit.registry.resolution import resolve_declared_params, resolve_reference_value
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.core.scenario_target_defaults import get_default_scorer_target
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.score import (
    Scorer,
    SelfAskRefusalScorer,
    SelfAskTrueFalseScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseQuestion,
    TrueFalseScoreAggregator,
    TrueFalseScorer,
)

if TYPE_CHECKING:
    from pyrit.converter import Converter
    from pyrit.models import ComponentIdentifier
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

logger = logging.getLogger(__name__)


#: Param names a scenario must not declare via ``supported_parameters()``. These
#: collide with promoted identity fields on ``ScenarioIdentifier`` and would be
#: silently overwritten during identifier promotion. Only ``version`` is reserved
#: today; a scenario's definition version is owned by the identifier, not a param.
_RESERVED_SCENARIO_PARAM_NAMES: frozenset[str] = frozenset({"version"})


class BaselineAttackPolicy(Enum):
    """
    Declares how a scenario type treats the default baseline atomic attack.

    The baseline is a plain ``PromptSendingAttack`` that sends each objective unmodified,
    used as a comparison point against the scenario's techniques. Each scenario class
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


class Scenario(ABC):
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

    #: Whether the default estimator must mirror matrix-builder seed compatibility.
    RUN_SIZE_USES_FACTORY_COMPATIBILITY: ClassVar[bool] = False

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
        return ()

    def __init__(
        self,
        *,
        name: str = "",
        version: int,
        technique_class: type[ScenarioTechnique],
        default_dataset_config: DatasetAttackConfiguration,
        objective_scorer: Scorer,
        scenario_result_id: uuid.UUID | str | None = None,
    ) -> None:
        """
        Initialize a scenario.

        Args:
            name (str): Descriptive name for the scenario.
            version (int): Version number of the scenario.
            technique_class (type[ScenarioTechnique]): The technique enum class for this scenario.
                The technique used when no ``scenario_techniques`` are passed to
                ``initialize_async`` is the catalog's own default, read from
                ``technique_class.default()``.
            default_dataset_config (DatasetAttackConfiguration): The default dataset configuration used
                when no ``dataset_config`` is passed to ``initialize_async``.
            objective_scorer (Scorer): The objective scorer used to evaluate attack results.
            scenario_result_id (uuid.UUID | str | None): Optional ID of an existing scenario result to resume.
                Can be either a UUID object or a string representation of a UUID.
                If provided and found in memory, the scenario will resume from prior progress.
                All other parameters must still match the stored scenario configuration.

        Note:
            Attack runs are populated by calling initialize_async(), which invokes the
            subclass's _build_atomic_attacks_async() method.

            The scenario description is automatically extracted from the class's docstring (__doc__)
            with whitespace normalized for display.
        """
        from pyrit.registry.registry_metadata import RegistryMetadata

        description = RegistryMetadata.description_from_docstring(self.__class__)

        # The scenario identifier is the canonical per-run identity: the scenario
        # registry produces it and it is persisted on the ScenarioResult (carrying
        # class name / version / resolved techniques / datasets / params and the
        # objective_target / objective_scorer references). The display description
        # and pyrit_version ride alongside it on the ScenarioResult.
        self._version = version
        self._description = description

        # Store technique configuration for use in initialize_async
        self._technique_class = technique_class
        self._default_technique = technique_class.default()
        self._default_dataset_config = default_dataset_config

        # These will be set in initialize_async
        self._objective_target: PromptTarget | None = None
        self._objective_target_identifier: ComponentIdentifier | None = None
        self._estimate_target_is_configured = False
        self._estimate_has_binding_size_cap = False
        self._estimate_full_groups_by_dataset: dict[str, list[AttackSeedGroup]] = {}
        self._memory_labels: dict[str, str] = {}
        self._max_concurrency: int | None = None
        self._max_retries: int = 0

        # Effective dataset configuration for the current run. initialize_async reassigns
        # this to the caller-supplied config (or the default); defaulting it here means the
        # attribute always exists for context construction.
        self._dataset_config: DatasetAttackConfiguration = default_dataset_config

        self._objective_scorer = objective_scorer
        self._objective_scorer_identifier = objective_scorer.get_identifier()

        self._name = name if name else type(self).__name__
        self._memory = CentralMemory.get_memory_instance()
        self._atomic_attacks: list[AtomicAttack] = []
        self._scenario_result_id: str | None = str(scenario_result_id) if scenario_result_id else None
        self._scenario_registry_name: str | None = None
        self._active_atomic_groups: dict[str, str] = {}

        # Store prepared techniques for use in _build_atomic_attacks_async
        self._scenario_techniques: list[ScenarioTechnique] = []

        # Maps concrete technique name → extra request converters to append for that technique.
        self._technique_converters: dict[str, list[Converter]] = {}

        # Maps atomic_attack_name → display_group for user-facing aggregation
        self._display_group_map: dict[str, str] = {}

        # Declared via supported_parameters(); resolved/populated by the registry
        # helper (pyrit.registry.resolution). Subclasses read it in _build_atomic_attacks_async.
        self.params: dict[str, Any] = {}
        # True once the param bag has been resolved (declared defaults materialized,
        # values coerced) by set_params_from_args. initialize_async resolves on demand
        # only when a programmatic caller skipped it, so resolution happens exactly once.
        self._params_resolved: bool = False

        # Resolved effective baseline inclusion for the current run. Set in initialize_async
        # before _build_atomic_attacks_async is awaited so overrides can read it.
        self._include_baseline: bool = False

    @property
    def name(self) -> str:
        """The name of the scenario."""
        return self._name

    @property
    def atomic_attack_count(self) -> int:
        """The number of atomic attacks in this scenario."""
        return len(self._atomic_attacks)

    @property
    def active_atomic_group_ids(self) -> frozenset[str]:
        """The stable IDs of atomic groups currently executing."""
        return frozenset(self._active_atomic_groups)

    @property
    def active_atomic_group_names(self) -> tuple[str, ...]:
        """The names of atomic groups currently executing."""
        return tuple(self._active_atomic_groups.values())

    def set_scenario_registry_name(self, *, scenario_registry_name: str) -> None:
        """Record the requested registry name for durable run-plan attribution."""
        self._scenario_registry_name = scenario_registry_name

    @classmethod
    def _common_scenario_parameters(cls) -> list[Parameter]:
        """
        Declare the run-resolved inputs every scenario accepts, once on the base.

        These populate ``self.params`` (via ``set_params_from_args``) and are read by
        ``initialize_async``. ``objective_target`` is a registry reference (resolved by
        name or supplied as an instance); the structured run inputs are ``opaque`` (live
        objects passed by identity — never coerced or copied); the scalars coerce normally.

        Subclasses that need to add their own parameters override ``additional_parameters``;
        those that need to remove or replace a common input override ``supported_parameters``
        and compose against this list with ``super()``:

        - **Add:** override ``additional_parameters`` and return ``[Parameter(...), ...]``
        - **Remove:** ``return [p for p in super().supported_parameters() if p.name != "dataset_config"]``

        Dropping a common input is not silent: ``set_params_from_args`` rejects any value
        supplied for an undeclared parameter, so the registry/CLI/programmatic path fails
        loudly the moment something tries to set it.

        Returns:
            list[Parameter]: The common run-input parameters.
        """
        return [
            Parameter(
                name="objective_target",
                description="Target system under attack: a registered target name or a PromptTarget instance.",
                reference=RegistryReference(component_type=ComponentType.TARGET),
            ),
            Parameter(
                name="scenario_techniques",
                description="Techniques to execute; defaults to the scenario's default aggregate when omitted.",
                opaque=True,
            ),
            Parameter(
                name="technique_converters",
                description="Mapping of concrete technique name to extra request converters to append.",
                opaque=True,
            ),
            Parameter(
                name="dataset_config",
                description="Dataset source configuration; defaults to the scenario's default when omitted.",
                opaque=True,
            ),
            Parameter(
                name="memory_labels",
                description="Additional labels applied to every attack run in the scenario.",
                opaque=True,
            ),
            Parameter(
                name="max_concurrency",
                description="Maximum number of concurrent units of work for the scenario.",
                param_type=int,
                default=4,
            ),
            Parameter(
                name="max_retries",
                description="Maximum number of automatic retries if the scenario raises an exception.",
                param_type=int,
                default=0,
            ),
            Parameter(
                name="include_baseline",
                description="Whether to prepend a baseline atomic attack; None defers to BASELINE_ATTACK_POLICY.",
                param_type=bool,
            ),
        ]

    @classmethod
    def _common_scenario_parameter_names(cls) -> frozenset[str]:
        """
        Return the names of the framework common parameters.

        These are the run inputs the base declares for every scenario (target,
        techniques, dataset config, concurrency, etc.). They are captured in the
        scenario identity through dedicated fields (objective target, techniques,
        datasets) rather than the free-form params dict, and callers use this set
        to separate framework inputs from a scenario's own custom parameters.

        Returns:
            frozenset[str]: The common parameter names.
        """
        return frozenset(p.name for p in Scenario._common_scenario_parameters())

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare the scenario-specific parameters this scenario accepts, beyond the common
        run inputs.

        This is the extension point for the common case: override it to **add** parameters
        without repeating the common inputs. The base ``supported_parameters`` composes
        ``_common_scenario_parameters() + additional_parameters()``, so overrides never need
        to call ``super()`` or risk dropping a common input. To **remove or replace** a common
        input instead, override ``supported_parameters`` directly.

        Returns:
            list[Parameter]: The scenario-specific parameters (default: none).
        """
        return []

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        """
        Declare the parameters this scenario accepts, resolved into ``self.params`` before
        ``initialize_async()`` runs. The base returns the common run inputs (see
        ``_common_scenario_parameters``) plus whatever ``additional_parameters`` declares.

        To **add** scenario-specific parameters, override ``additional_parameters`` (the
        common case). Override *this* method only to **remove or replace** a common input,
        composing against ``super().supported_parameters()``.

        Implemented as a classmethod so ``--list-scenarios`` can introspect without
        instantiating.

        Note: ``PyRITInitializer.supported_parameters`` is an instance ``@property``;
        this asymmetry is intentional pending a future alignment.

        Returns:
            list[Parameter]: Declared parameters (default: common run inputs + additional).
        """
        return cls._common_scenario_parameters() + cls.additional_parameters()

    def _get_default_objective_scorer(self) -> TrueFalseScorer:
        # Deferred import to avoid circular dependency.
        from pyrit.setup.initializers.scorers import ScorerInitializerTags

        # first check if the registry has a default objective scorer
        # if available either itself, or its chat target will be used
        chat_target: PromptTarget | None = None
        registry_default_scorer: TrueFalseScorer | None = None
        entries = ScorerRegistry.get_registry_singleton().instances.get_by_tag(
            tag=ScorerInitializerTags.DEFAULT_OBJECTIVE_SCORER
        )
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
                SelfAskTrueFalseScorer.from_question(
                    chat_target=chat_target, question=TrueFalseQuestion.from_yaml(path)
                )
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

        The scenario only **declares** its parameters via ``supported_parameters()``;
        the coerce / validate / inject-defaults *mapping* is owned by the registry
        layer (``pyrit.registry.resolution.resolve_declared_params``) so there is a
        single implementation shared by the programmatic, CLI, and registry paths.
        Every declared parameter is guaranteed a key in ``self.params`` after this
        call; params without a declared default land as ``None``.

        Args:
            args (dict[str, Any]): Map of parameter name to raw value. Keys
                with ``None`` values are treated as absent (YAML ``null``).
                Argparse callers should use ``argparse.SUPPRESS``.

        Raises:
            ValueError: Invalid declaration, unknown parameter, coercion
                failure, value not in ``choices``, or a declared parameter using
                a reserved scenario identity name (e.g. ``version``).
        """
        declared = list(self.supported_parameters())
        reserved = sorted({p.name for p in declared} & _RESERVED_SCENARIO_PARAM_NAMES)
        if reserved:
            raise ValueError(
                f"Scenario '{type(self).__name__}' declares reserved parameter(s) {reserved}; "
                "these names are owned by the scenario identity and cannot be scenario params. "
                "Rename the parameter."
            )
        self.params = resolve_declared_params(
            declared=declared,
            raw_args=args,
            owner=f"Scenario '{type(self).__name__}'",
        )
        self._params_resolved = True

    def _resolve_objective_target(self, *, value: Any) -> PromptTarget | None:
        """
        Resolve the bag's ``objective_target`` value into a live ``PromptTarget``.

        The value is a live ``PromptTarget`` instance (used as-is), a registered target
        *name* (resolved against ``TargetRegistry`` — the same registry-reference path
        the constructor-argument resolver uses for converters and scorers), or ``None``
        (falls back to a default registered with ``set_default_value``, preserving the
        initializer-script default-target workflow).

        Args:
            value (Any): The raw ``objective_target`` bag value (a ``PromptTarget``,
                a registered target name, or None).

        Returns:
            PromptTarget | None: The resolved target, or None when neither supplied
                nor available as a global default.

        Raises:
            TypeError: If a configured default or resolved target is not a ``PromptTarget``.
            ValueError: If a target name is supplied that is not registered in ``TargetRegistry``.
        """
        if value is None:
            found, default = get_global_default_values().get_default_value(
                class_type=type(self), parameter_name="objective_target"
            )
            if not found or default is None:
                return None
            if not isinstance(default, PromptTarget):
                raise TypeError(f"Default objective_target must be a PromptTarget, got {type(default).__name__}")
            return default

        resolved = resolve_reference_value(
            component_type=ComponentType.TARGET,
            value=value,
            owner=type(self).__name__,
            name="objective_target",
        )
        if not isinstance(resolved, PromptTarget):
            raise TypeError(f"Resolved objective_target must be a PromptTarget, got {type(resolved).__name__}")
        return resolved

    def _resolve_scenario_techniques(self, *, scenario_techniques: Any) -> list[ScenarioTechnique]:
        """
        Resolve the bag's requested techniques into the concrete technique list.

        The base resolves ``scenario_techniques`` against the scenario's technique enum,
        expanding aggregates and falling back to the default aggregate when omitted.
        Override to widen the accepted technique types or expand composite techniques
        (see ``FoundryScenario``, which pairs attacks with converters).

        Args:
            scenario_techniques (Any): The raw ``scenario_techniques`` bag value
                (a sequence of ``ScenarioTechnique`` members, or None for the default).

        Returns:
            list[ScenarioTechnique]: The concrete techniques to execute.
        """
        return self._technique_class.resolve(scenario_techniques, default=self._default_technique)

    @final
    async def get_default_run_size_estimate_async(self) -> ScenarioRunSizeEstimate:
        """
        Estimate the scenario's default planned execution units without starting a run.

        This resolves declared parameter defaults before delegating to the same
        configured estimate path used by request-specific previews.

        Returns:
            ScenarioRunSizeEstimate: Structured default-run estimate.
        """
        self.set_params_from_args(args={})
        return await self.get_run_size_estimate_async(target_is_configured=False)

    @final
    async def get_run_size_estimate_async(self, *, target_is_configured: bool = False) -> ScenarioRunSizeEstimate:
        """
        Estimate the currently configured run without creating or persisting it.

        ``set_params_from_args`` should be called first for a request-specific
        estimate. Omitted values use the same declared defaults, aggregate
        expansion, dataset selection, and baseline policy as ``initialize_async``.

        Returns:
            ScenarioRunSizeEstimate: Structured configured-run estimate.

        Raises:
            ValueError: If target certainty is asserted without a resolved target.
        """
        self._resolve_runtime_configuration(require_objective_target=False)
        if target_is_configured and self._objective_target is None:
            raise ValueError("target_is_configured requires a resolved objective_target")
        self._estimate_target_is_configured = self._objective_target is not None
        return await self._estimate_run_size_async()

    async def _estimate_run_size_async(self) -> ScenarioRunSizeEstimate:
        """
        Estimate a standard technique-by-seed-group scenario.

        Subclasses override this hook when their outer execution shape adds axes,
        synthesizes technique-specific populations, or selects techniques adaptively.

        Returns:
            ScenarioRunSizeEstimate: Exact default sweep and baseline count.
        """
        selected_groups, datasets = await self._resolve_dataset_groups_for_estimate_async()
        seed_group_count = sum(len(groups) for groups in selected_groups.values())
        components = self._build_technique_size_components(
            selected_groups=selected_groups,
            seed_group_count=seed_group_count,
        )
        if self._include_baseline:
            components.append(
                ScenarioRunSizeComponent(
                    label="Baseline",
                    count=seed_group_count,
                    is_baseline=True,
                    note="One unmodified prompt-sending unit per selected seed group.",
                )
            )

        estimated_attack_count = sum(component.count for component in components)
        minimum_attack_count = None
        maximum_attack_count = None
        note = "Counts planned outer execution units; retries and internal attack turns are excluded."
        if self.RUN_SIZE_USES_FACTORY_COMPATIBILITY and self._estimate_has_binding_size_cap:
            compatibility_bounds = self._get_technique_compatibility_bounds(datasets=datasets)
            if compatibility_bounds is None:
                estimated_attack_count = None
                note += " A binding randomized dataset cap may select a different compatibility mix at launch."
            else:
                baseline_count = seed_group_count if self._include_baseline else 0
                minimum_attack_count = baseline_count + sum(bounds[0] for bounds in compatibility_bounds.values())
                maximum_attack_count = baseline_count + sum(bounds[1] for bounds in compatibility_bounds.values())
                if minimum_attack_count == maximum_attack_count:
                    estimated_attack_count = sum(component.count for component in components)
                    minimum_attack_count = None
                    maximum_attack_count = None
                else:
                    estimated_attack_count = None
                    note += " The range covers every compatibility mix that the randomized per-dataset caps can select."
        return ScenarioRunSizeEstimate(
            estimated_attack_count=estimated_attack_count,
            minimum_attack_count=minimum_attack_count,
            maximum_attack_count=maximum_attack_count,
            components=components,
            datasets=datasets,
            note=note,
        )

    def _build_technique_size_components(
        self,
        *,
        selected_groups: dict[str, list[AttackSeedGroup]],
        seed_group_count: int,
    ) -> list[ScenarioRunSizeComponent]:
        """
        Build the standard sweep, applying matrix-builder compatibility when declared.

        Returns:
            list[ScenarioRunSizeComponent]: Additive technique components.
        """
        if not self.RUN_SIZE_USES_FACTORY_COMPATIBILITY:
            technique_count = len(self._scenario_techniques)
            return [
                ScenarioRunSizeComponent(
                    label="Default technique sweep",
                    count=seed_group_count * technique_count,
                )
            ]

        from pyrit.scenario.core.matrix_atomic_attack_builder import (
            filter_compatible_seed_groups,
            resolve_technique_factories_for_techniques,
        )

        factories = resolve_technique_factories_for_techniques(
            scenario_techniques=self._scenario_techniques,
            extra_factories=self._get_run_size_extra_factories(),
        )
        components: list[ScenarioRunSizeComponent] = []
        for technique in self._scenario_techniques:
            factory = factories.get(technique.value)
            if factory is None:
                continue
            compatible_count = sum(
                len(filter_compatible_seed_groups(factory=factory, seed_groups=groups))
                for groups in selected_groups.values()
            )
            components.append(
                ScenarioRunSizeComponent(
                    label=technique.value,
                    count=compatible_count,
                )
            )
        return components

    def _get_technique_compatibility_bounds(
        self,
        *,
        datasets: list[ScenarioDatasetSummary],
    ) -> dict[str, tuple[int, int]] | None:
        """
        Calculate selected compatible-group bounds for each technique.

        Args:
            datasets (list[ScenarioDatasetSummary]): Resolved dataset counts and cap provenance.

        Returns:
            dict[str, tuple[int, int]] | None: Technique names mapped to minimum and maximum
                compatible counts, or ``None`` when the configured sampling shape is unsupported.
        """
        from pyrit.scenario.core.matrix_atomic_attack_builder import (
            filter_compatible_seed_groups,
            resolve_technique_factories_for_techniques,
        )

        summaries = {dataset.name: dataset for dataset in datasets}
        factories = resolve_technique_factories_for_techniques(
            scenario_techniques=self._scenario_techniques,
            extra_factories=self._get_run_size_extra_factories(),
        )
        result: dict[str, tuple[int, int]] = {}
        for technique in self._scenario_techniques:
            factory = factories.get(technique.value)
            if factory is None:
                continue
            minimum = 0
            maximum = 0
            for name, full_groups in self._estimate_full_groups_by_dataset.items():
                summary = summaries.get(name)
                if summary is None:
                    return None
                compatible_count = len(filter_compatible_seed_groups(factory=factory, seed_groups=full_groups))
                bounds = self._get_sampled_compatibility_bounds(
                    full_count=len(full_groups),
                    selected_count=summary.selected_seed_group_count,
                    compatible_count=compatible_count,
                    uses_only_per_dataset_caps=bool(summary.configured_caps)
                    and all(cap.configured_on == "dataset" for cap in summary.configured_caps),
                )
                if bounds is None:
                    return None
                minimum += bounds[0]
                maximum += bounds[1]
            result[technique.value] = (minimum, maximum)
        return result

    @staticmethod
    def _get_sampled_compatibility_bounds(
        *,
        full_count: int,
        selected_count: int,
        compatible_count: int,
        uses_only_per_dataset_caps: bool,
    ) -> tuple[int, int] | None:
        """
        Calculate compatible-group bounds for one independently sampled dataset.

        Args:
            full_count (int): Number of groups before sampling.
            selected_count (int): Number of groups selected by the configured cap.
            compatible_count (int): Number of compatible groups before sampling.
            uses_only_per_dataset_caps (bool): Whether selection uses independent per-dataset caps.

        Returns:
            tuple[int, int] | None: Minimum and maximum compatible selected groups, or ``None``
                when the sampling shape is unsupported.
        """
        if selected_count == full_count:
            return compatible_count, compatible_count
        if not uses_only_per_dataset_caps:
            return None
        minimum = max(0, selected_count - (full_count - compatible_count))
        maximum = min(selected_count, compatible_count)
        return minimum, maximum

    def _get_run_size_extra_factories(self) -> dict[str, "AttackTechniqueFactory"] | None:
        """Return scenario-local factories used by compatibility-aware sizing."""
        return None

    async def _resolve_dataset_groups_for_estimate_async(
        self,
    ) -> tuple[dict[str, list[AttackSeedGroup]], list[ScenarioDatasetSummary]]:
        """
        Resolve full and effectively selected logical groups for configured datasets.

        Returns:
            tuple: Selected groups keyed by population and their catalog summaries.
        """
        configured_dataset = self._dataset_config
        self._dataset_config = configured_dataset
        full_groups = await self._resolve_seed_groups_by_dataset_async(apply_sampling=False)
        self._estimate_full_groups_by_dataset = full_groups
        self._dataset_config = configured_dataset
        selected_groups = await self._resolve_seed_groups_by_dataset_async(apply_sampling=True)

        configured_caps = self._dataset_config.size_caps_by_dataset()
        datasets: list[ScenarioDatasetSummary] = []
        for name in dict.fromkeys([*full_groups, *selected_groups]):
            logical_count = len(full_groups.get(name, []))
            selected_count = len(selected_groups.get(name, []))
            selection_note = None
            if selected_count != logical_count:
                selection_note = f"The default selection uses {selected_count} of {logical_count} available objectives."
            datasets.append(
                ScenarioDatasetSummary(
                    name=name,
                    logical_seed_group_count=logical_count,
                    selected_seed_group_count=selected_count,
                    configured_caps=[
                        ScenarioDatasetSizeCap(
                            label=label,
                            count=count,
                            configured_on=configured_on,
                            dataset_name=name,
                        )
                        for label, count, configured_on in configured_caps.get(name, [])
                    ],
                    selection_note=selection_note,
                )
            )
        self._estimate_has_binding_size_cap = bool(configured_caps) and sum(
            dataset.selected_seed_group_count for dataset in datasets
        ) < sum(dataset.logical_seed_group_count for dataset in datasets)
        return selected_groups, datasets

    def _resolve_runtime_configuration(self, *, require_objective_target: bool) -> None:
        """
        Resolve the common parameter bag shared by initialization and estimation.

        Args:
            require_objective_target: Whether an omitted objective target is an error.

        Raises:
            ValueError: If required target or baseline constraints are not satisfied.
        """
        if not self._params_resolved:
            self.set_params_from_args(args=self.params)
        params = self.params
        declared_names = {parameter.name for parameter in self.supported_parameters()}

        if "objective_target" in declared_names:
            raw_objective_target = params.get("objective_target")
            if require_objective_target or raw_objective_target is not None:
                objective_target = self._resolve_objective_target(value=raw_objective_target)
                if objective_target is None:
                    raise ValueError(
                        "objective_target is required. Provide it via "
                        "set_params_from_args(args={'objective_target': ...}) or register a default "
                        "with set_default_value() in an initialization script."
                    )
                self._objective_target = objective_target
                self._objective_target_identifier = objective_target.get_identifier()
                type(self).TARGET_REQUIREMENTS.validate(target=objective_target)
            else:
                self._objective_target = None
                self._objective_target_identifier = None

        dataset_config = params.get("dataset_config")
        self._dataset_config_provided = dataset_config is not None
        self._dataset_config = dataset_config if dataset_config else self._default_dataset_config
        self._max_concurrency = params.get("max_concurrency", 4)
        self._max_retries = params.get("max_retries", 0)
        self._memory_labels = params.get("memory_labels") or {}

        include_baseline = params.get("include_baseline")
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

        self._scenario_techniques = self._resolve_scenario_techniques(
            scenario_techniques=params.get("scenario_techniques")
        )
        self._technique_converters = params.get("technique_converters") or {}

    @final
    async def initialize_async(self) -> None:
        """
        Initialize the scenario by populating self._atomic_attacks and creating the ScenarioResult.

        All run inputs are read from the parameter bag (``self.params``), which is populated by
        ``set_params_from_args`` from the merged CLI / config / programmatic arguments. Callers
        fill the bag then initialize:

        .. code-block:: python

            scenario.set_params_from_args(args={"objective_target": target, "max_concurrency": 8})
            await scenario.initialize_async()

        This method allows scenarios to be initialized with atomic attacks after construction,
        which is useful when atomic attacks require async operations to be built.

        If a scenario_result_id was provided in __init__, this method will check if it exists
        in memory and validate that the stored scenario matches the current configuration.
        If it matches, the scenario will resume from prior progress. If it doesn't match or
        doesn't exist, a new scenario result will be created.

        The common run inputs read from the bag are ``objective_target`` (a ``PromptTarget``
        instance or a registered target name resolved against ``TargetRegistry``),
        ``scenario_techniques``, ``technique_converters``, ``dataset_config``,
        ``max_concurrency``, ``max_retries``, ``memory_labels``, and ``include_baseline``
        (see ``_common_scenario_parameters``). A subclass that removes a common input via
        ``supported_parameters`` falls back to that input's default here.

        Raises:
            ValueError: If ``objective_target`` is declared but not resolvable (neither supplied
                nor registered as a default), if a supplied target name is not registered in
                ``TargetRegistry``, or if ``include_baseline=True`` is set for a scenario whose
                ``BASELINE_ATTACK_POLICY`` is ``Forbidden``.
        """
        self._resolve_runtime_configuration(require_objective_target=True)

        # Build atomic attacks: resolve the seed groups once, snapshot the resolved inputs
        # into a ScenarioContext, and hand it to the subclass extension point. Baseline emission
        # is the scenario's own responsibility — matrix scenarios get it for free (the matrix
        # builder reads ``context.include_baseline``); other scenarios prepend one via
        # ``build_baseline_atomic_attack``. The base only resolves the policy into
        # ``self._include_baseline`` above, which the ScenarioContext carries as
        # ``include_baseline``.
        #
        # On resume, resolve the full, deterministic dataset (no max_dataset_size sampling):
        # the originally-sampled subset was snapshotted into the ScenarioResult metadata and is
        # replayed by _apply_persisted_objectives. Re-drawing a fresh random.sample here would
        # diverge from the persisted hashes and abort resume whenever max_dataset_size is set.
        is_resume = self._scenario_result_id is not None
        seed_groups_by_dataset = await self._resolve_seed_groups_by_dataset_async(apply_sampling=not is_resume)
        context = self._build_scenario_context(seed_groups_by_dataset=seed_groups_by_dataset)
        self._atomic_attacks = await self._build_atomic_attacks_async(context=context)

        # Build the canonical scenario identifier once params/techniques/datasets
        # are resolved, so both the resume check and the new-result branch share the
        # same identity (and its eval hash).
        scenario_identifier = self._build_scenario_identifier()

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

            self._validate_stored_scenario(
                stored_result=existing_results[0],
                current_identifier=scenario_identifier,
            )
            stored_result = existing_results[0]
            stored_plan = self._get_stored_run_plan(stored_result=stored_result)
            if stored_plan is not None:
                self._apply_persisted_run_plan(stored_plan=stored_plan)
            else:
                self._apply_persisted_objectives(stored_result=stored_result)
                reconstructed_plan = self._build_run_plan()
                metadata = dict(stored_result.metadata)
                metadata[SCENARIO_RUN_PLAN_METADATA_KEY] = reconstructed_plan.model_dump(mode="json", exclude_none=True)
                self._memory.update_scenario_metadata(
                    scenario_result_id=self._scenario_result_id,
                    metadata=metadata,
                )
            return  # Valid resume - skip creating new scenario result

        # Build display group mapping from atomic attacks
        self._display_group_map = {aa.atomic_attack_name: aa.display_group for aa in self._atomic_attacks}

        # Create new scenario result
        attack_results: dict[str, list[AttackResult]] = {
            atomic_attack.atomic_attack_name: [] for atomic_attack in self._atomic_attacks
        }

        result = ScenarioResult(
            scenario_identifier=scenario_identifier,
            scenario_description=self._description,
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

        The normalized run plan is always stored. When ``max_dataset_size`` is not
        set, only the run plan is needed because the full dataset is deterministic.

        Returns:
            dict[str, Any]: Metadata payload for the new ScenarioResult.
        """
        metadata: dict[str, Any] = {}
        if getattr(self._dataset_config, "max_dataset_size", None) is not None:
            hashes: list[str] = []
            seen: set[str] = set()
            for aa in self._atomic_attacks:
                for sg in aa.seed_groups:
                    sha = to_sha256(sg.objective.value)
                    if sha not in seen:
                        seen.add(sha)
                        hashes.append(sha)
            metadata["objective_hashes"] = hashes
        metadata[SCENARIO_RUN_PLAN_METADATA_KEY] = self._build_run_plan().model_dump(mode="json", exclude_none=True)
        return metadata

    def _build_run_plan(self) -> ScenarioRunPlan:
        """
        Build the normalized persistent plan for the initialized atomic attacks.

        Returns:
            ScenarioRunPlan: The versioned run plan.
        """
        seed_groups: dict[str, ScenarioRunPlanSeedGroup] = {}
        atomic_groups: list[ScenarioRunPlanAtomicGroup] = []
        for atomic_attack in self._atomic_attacks:
            technique_name = atomic_attack.technique_name
            if not isinstance(technique_name, str):
                technique_name = atomic_attack.display_group
            technique = next(
                (candidate for candidate in self._technique_class if candidate.value == technique_name),
                None,
            )
            seed_group_ids: list[str] = []
            seen_seed_group_ids: set[str] = set()
            for seed_group in atomic_attack.seed_groups:
                seed_group_id = seed_group.logical_id
                if seed_group_id in seen_seed_group_ids:
                    continue
                seen_seed_group_ids.add(seed_group_id)
                seed_group_ids.append(seed_group_id)
                seed_groups.setdefault(
                    seed_group_id,
                    ScenarioRunPlanSeedGroup(
                        id=seed_group_id,
                        objective_sha256=to_sha256(seed_group.objective.value),
                        objective=seed_group.objective.value,
                        prompts=[
                            ScenarioRunPlanSeedPrompt(
                                value=prompt.value,
                                data_type=prompt.data_type,
                                role=prompt.role,
                                sequence=prompt.sequence,
                                parameters=list(prompt.parameters or []),
                            )
                            for prompt in seed_group.prompts
                        ],
                    ),
                )
            technique_eval_hash = str(atomic_attack.technique_eval_hash)
            atomic_group_id = self._get_atomic_group_id(atomic_attack=atomic_attack)
            atomic_groups.append(
                ScenarioRunPlanAtomicGroup(
                    id=atomic_group_id,
                    atomic_attack_name=atomic_attack.atomic_attack_name,
                    display_group=atomic_attack.display_group,
                    technique_name=technique_name if technique else None,
                    technique_eval_hash=technique_eval_hash,
                    seed_group_ids=seed_group_ids,
                    description=technique.description if technique else None,
                    tags=sorted(technique.tags) if technique else [],
                )
            )
        return ScenarioRunPlan(
            scenario_registry_name=self._scenario_registry_name,
            atomic_groups=atomic_groups,
            seed_groups=list(seed_groups.values()),
        )

    @staticmethod
    def _get_atomic_group_id(*, atomic_attack: AtomicAttack) -> str:
        """
        Compute the stable ID of an atomic group from its name and technique.

        Returns:
            str: The atomic-group ID.
        """
        return config_hash(
            {
                "atomic_attack_name": atomic_attack.atomic_attack_name,
                "technique_eval_hash": str(atomic_attack.technique_eval_hash),
            }
        )

    @staticmethod
    def _get_stored_run_plan(*, stored_result: ScenarioResult) -> ScenarioRunPlan | None:
        """
        Load and validate a stored run plan.

        Returns:
            ScenarioRunPlan | None: The plan, or None for a legacy row.
        """
        raw_plan = (stored_result.metadata or {}).get(SCENARIO_RUN_PLAN_METADATA_KEY)
        if raw_plan is None:
            return None
        return ScenarioRunPlan.model_validate(raw_plan)

    def _apply_persisted_run_plan(self, *, stored_plan: ScenarioRunPlan) -> None:
        """
        Validate and replay the exact logical units captured by a stored plan.

        Raises:
            ValueError: If a planned atomic or seed group cannot be reconstructed.
        """
        current_by_id = {
            self._get_atomic_group_id(atomic_attack=atomic_attack): atomic_attack
            for atomic_attack in self._atomic_attacks
        }
        planned_ids = {group.id for group in stored_plan.atomic_groups}
        missing_groups = planned_ids - current_by_id.keys()
        if missing_groups:
            raise ValueError(
                f"Scenario result id '{self._scenario_result_id}' cannot resume: "
                f"{len(missing_groups)} planned atomic group(s) are no longer reconstructable."
            )

        retained_attacks: list[AtomicAttack] = []
        for planned_group in stored_plan.atomic_groups:
            atomic_attack = current_by_id[planned_group.id]
            current_seed_groups = {seed_group.logical_id: seed_group for seed_group in atomic_attack.seed_groups}
            missing_seed_groups = set(planned_group.seed_group_ids) - current_seed_groups.keys()
            if missing_seed_groups:
                raise ValueError(
                    f"Scenario result id '{self._scenario_result_id}' cannot resume: atomic group "
                    f"'{planned_group.atomic_attack_name}' is missing {len(missing_seed_groups)} planned seed group(s)."
                )
            atomic_attack._seed_groups = [current_seed_groups[group_id] for group_id in planned_group.seed_group_ids]
            retained_attacks.append(atomic_attack)
        self._atomic_attacks = retained_attacks
        self._display_group_map = {group.atomic_attack_name: group.display_group for group in stored_plan.atomic_groups}

    def _apply_persisted_objectives(self, *, stored_result: ScenarioResult) -> None:
        """
        On resume, replay the originally-sampled objective subset.

        When the first run used ``max_dataset_size``, the chosen subset was
        recorded in ``ScenarioResult.metadata["objective_hashes"]``. Resume resolves
        the **full, deterministic** dataset (sampling is bypassed on the resume branch of
        ``initialize_async``), so restricting each atomic attack's seed_groups to the
        persisted set here reconstructs exactly the objectives the first run committed to.
        Per-objective atomic attacks outside that subset are removed before scheduling.
        If any persisted hash is no longer present in the dataset, refuse to resume — that
        now signals the dataset itself genuinely changed, not a random resample drift.

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
        retained_attacks: list[AtomicAttack] = []
        for aa in self._atomic_attacks:
            retained |= aa.keep_seed_groups_with_hashes(hashes=persisted_hashes)
            if aa.seed_groups:
                retained_attacks.append(aa)

        missing = persisted_hashes - retained
        if missing:
            sample = sorted(missing)[:3]
            raise ValueError(
                f"Scenario result id '{self._scenario_result_id}' cannot resume: "
                f"{len(missing)} persisted objective hash(es) are no longer present in the dataset "
                f"(missing examples: {', '.join(h[:12] + '...' for h in sample)}). "
                f"Either restore the missing objectives or drop scenario_result_id to start a new scenario."
            )

        self._atomic_attacks = retained_attacks

    def _build_scenario_identifier(self) -> ScenarioIdentifier:
        """
        Build the canonical ``ScenarioIdentifier`` for the current run.

        Combines the definition version, the resolved technique / dataset
        selection, the resolved scenario params, and the objective target / scorer
        references into one identity whose eval hash backs resume drift detection.

        Returns:
            ScenarioIdentifier: The identifier describing this scenario run.
        """
        techniques = sorted({s.value for s in self._scenario_techniques})
        datasets = list(self._dataset_config.dataset_names)
        # Persist only the scenario's own custom params. The framework common inputs
        # (objective_target, techniques, dataset config, ...) are captured through the
        # dedicated identity fields below and are often live, non-JSON-serializable
        # objects, so they must not leak into the free-form params dict.
        common_names = self._common_scenario_parameter_names()
        custom_params = {name: value for name, value in self.params.items() if name not in common_names}
        return ScenarioIdentifier.of(
            self,
            params=custom_params,
            version=self._version,
            techniques=techniques,
            datasets=datasets,
            objective_target=self._objective_target_identifier,
            objective_scorer=self._objective_scorer_identifier,
        )

    def _validate_stored_scenario(
        self, *, stored_result: ScenarioResult, current_identifier: ScenarioIdentifier
    ) -> None:
        """
        Validate that a stored scenario result matches the current configuration.

        Resume is opt-in via ``scenario_result_id``; any divergence from the stored
        result is treated as user error rather than a silent restart, since the
        original progress would otherwise be orphaned without warning. Divergence is
        detected by comparing behavioral eval hashes: the scenario class name /
        module, version, resolved techniques / datasets, params, and objective
        target / scorer all feed the hash, so a mismatch means either a different
        scenario or a changed configuration.

        Args:
            stored_result (ScenarioResult): The scenario result retrieved from memory.
            current_identifier (ScenarioIdentifier): Identifier for the current run.

        Raises:
            ValueError: If the stored scenario identity does not match the current one.
        """
        # Compare behavioral eval hashes. The stored eval_hash is never trusted;
        # ScenarioEvaluationIdentifier recomputes it from the stored identifier's
        # class / params / children, matching how the current identifier is hashed.
        # class_name and class_module both feed the hash, so this also catches a
        # scenario_result_id that belongs to an entirely different scenario.
        stored_eval_hash = ScenarioEvaluationIdentifier(stored_result.scenario_identifier).eval_hash
        current_eval_hash = ScenarioEvaluationIdentifier(current_identifier).eval_hash

        if stored_eval_hash != current_eval_hash:
            raise ValueError(
                f"Scenario result id '{self._scenario_result_id}' does not match the current "
                f"'{type(self).__name__}' configuration (a different scenario, or its version, "
                f"techniques, datasets, parameters, or objective target / scorer changed). "
                f"Drop scenario_result_id to start a new scenario, or pass matching configuration to resume."
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

    async def _resolve_seed_groups_by_dataset_async(
        self, *, apply_sampling: bool = True
    ) -> dict[str, list[AttackSeedGroup]]:
        """
        Resolve the seed groups this scenario attacks, keyed by originating dataset.

        This is the single place seed resolution happens for a run. The base ``Scenario``
        calls it once in the bridge, flattens the result into ``context.seed_groups``, and
        reuses the same population for every atomic attack and the baseline — so sampling
        under ``max_dataset_size`` stays consistent across all of them.

        Override to inject seeds from an alternate source (e.g. deprecated ``objectives``)
        or to filter the resolved groups before attacks are built.

        Args:
            apply_sampling (bool): When True (default), apply ``max_dataset_size`` sampling.
                On resume the base passes False so the full, deterministic dataset is resolved
                and the persisted objective subset is reconstructed exactly (see
                ``_apply_persisted_objectives``) rather than intersected against a fresh,
                divergent ``random.sample`` draw.

        Returns:
            dict[str, list[AttackSeedGroup]]: Seed groups keyed by dataset name.
        """
        return await self._dataset_config.get_attack_groups_by_dataset_async(apply_sampling=apply_sampling)

    def _build_scenario_context(self, *, seed_groups_by_dataset: dict[str, list[AttackSeedGroup]]) -> ScenarioContext:
        """
        Snapshot the resolved runtime inputs into a ``ScenarioContext``.

        Called after ``initialize_async`` has populated the objective target, scorer,
        techniques, dataset config, labels, and baseline flag. The resulting context is
        handed to ``_build_atomic_attacks_async`` so scenario authors never read
        half-initialized ``self._*`` state to build attacks.

        Args:
            seed_groups_by_dataset (dict[str, list[AttackSeedGroup]]): Seed groups already
                resolved once (see ``_resolve_seed_groups_by_dataset_async``). The flat
                ``context.seed_groups`` is derived from these so both views share one sample.

        Returns:
            ScenarioContext: The immutable inputs for atomic-attack construction.

        Raises:
            ValueError: If the scenario has not been initialized.
        """
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        seed_groups = [group for groups in seed_groups_by_dataset.values() for group in groups]

        return ScenarioContext(
            objective_target=self._objective_target,
            scenario_techniques=tuple(self._scenario_techniques),
            dataset_config=self._dataset_config,
            memory_labels=dict(self._memory_labels),
            include_baseline=self._include_baseline,
            seed_groups=seed_groups,
            seed_groups_by_dataset=seed_groups_by_dataset,
        )

    @abstractmethod
    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build this scenario's atomic attacks from the resolved runtime inputs.

        This is the single extension point scenarios override to map techniques, datasets,
        scorers, and any extra axes into ``AtomicAttack`` instances. It is called once by
        ``initialize_async`` after the objective target, scorer, techniques, dataset config,
        labels, and baseline flag have been resolved and snapshot into ``context``.

        Scenario authors build their attacks from ``context.seed_groups`` (or
        ``context.seed_groups_by_dataset``) so sampling under ``max_dataset_size`` stays
        consistent across every atomic attack and the baseline. Each scenario emits its own
        baseline here when ``context.include_baseline`` is set -- matrix scenarios via
        ``build_matrix_atomic_attacks``, hand-built scenarios via ``build_baseline_atomic_attack``.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The generated atomic attacks.
        """
        ...

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
            asyncio.CancelledError: If the scenario task is cancelled. Completed results remain persisted
                and a later call can resume the unfinished objectives.
            ValueError: If the scenario has no atomic attacks configured. If your scenario
                requires initialization, call await scenario.initialize() first.
            ScenarioPartialFailureException: If an atomic attack only partially completes.
            RuntimeError: If the scenario fails for any other reason while executing.

        Example:
            >>> result = await scenario.run_async()
            >>> print(f"Scenario: {result.scenario_name}")
            >>> print(f"Total results: {len(result.attack_results)}")
        """
        if not self._atomic_attacks:
            error = ValueError(
                "Cannot run scenario with no atomic attacks. Either supply them in initialization or "
                "call await scenario.initialize_async() first."
            )
            if self._scenario_result_id:
                self._mark_scenario_failed(scenario_result_id=self._scenario_result_id, error=error)
            raise error

        if not self._scenario_result_id:
            raise ValueError("Scenario not properly initialized. Call await scenario.initialize_async() first.")

        # Type narrowing: create local variable that type checker knows is non-None
        scenario_result_id: str = self._scenario_result_id

        # Implement retry logic
        last_exception = None
        for retry_attempt in range(self._max_retries + 1):  # +1 for initial attempt
            try:
                return await self._execute_scenario_async()
            except asyncio.CancelledError:
                try:
                    self._memory.update_scenario_run_state(
                        scenario_result_id=scenario_result_id,
                        scenario_run_state=ScenarioRunState.CANCELLED,
                        error_message="Scenario run was cancelled",
                        error_type="CancelledError",
                    )
                except Exception:
                    logger.exception(f"Failed to persist cancellation state for scenario '{self._name}'")
                raise
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
                self._mark_scenario_failed(scenario_result_id=scenario_result_id, error=e)
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
            ScenarioPartialFailureException: If an atomic attack only partially completes.
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

        # Mark scenario as in progress
        self._memory.update_scenario_run_state(
            scenario_result_id=scenario_result_id,
            scenario_run_state=ScenarioRunState.IN_PROGRESS,
        )

        # Get remaining atomic attacks (filters out completed ones and updates objectives)
        remaining_attacks = await self._get_remaining_atomic_attacks_async()

        if not remaining_attacks:
            logger.info(f"Scenario '{self._name}' has no remaining objectives to execute")
            # Mark scenario as completed
            self._memory.update_scenario_run_state(
                scenario_result_id=scenario_result_id,
                scenario_run_state=ScenarioRunState.COMPLETED,
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
                scenario_result_id=scenario_result_id,
                scenario_run_state=ScenarioRunState.COMPLETED,
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
        atomic_results: AttackExecutorResult[AttackResult],
    ) -> ScenarioPartialFailureException | None:
        """
        Log the outcome of an atomic attack and return an exception if it didn't
        fully complete.

        Returns:
            ScenarioPartialFailureException | None: An error to raise when the atomic attack
                has incomplete objectives, otherwise ``None``.
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

        return ScenarioPartialFailureException(
            atomic_attack_name=atomic_attack.atomic_attack_name,
            completed_count=completed_in_run,
            incomplete_objectives=atomic_results.incomplete_objectives,
        )

    def _mark_scenario_failed(self, *, scenario_result_id: str, error: BaseException) -> None:
        """Mark the scenario run as FAILED, deriving message/type from ``error``."""
        error_message = str(error)
        if error.__cause__ is not None:
            error_message = f"{error_message} Caused by {type(error.__cause__).__name__}: {str(error.__cause__)}"
        self._memory.update_scenario_run_state(
            scenario_result_id=scenario_result_id,
            scenario_run_state=ScenarioRunState.FAILED,
            error_message=error_message,
            error_type=type(error).__name__,
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
        outcomes: list[tuple[AtomicAttack, AttackExecutorResult[AttackResult]] | BaseException] = []

        async def worker_async() -> None:
            while not stop_event.is_set():
                try:
                    atomic_attack = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                atomic_group_id = atomic_attack.logical_group_id
                self._active_atomic_groups[atomic_group_id] = atomic_attack.atomic_attack_name
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
                    self._active_atomic_groups.pop(atomic_group_id, None)
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
            raise final_error

    def _collect_errors_from_outcomes(
        self,
        *,
        outcomes: list[tuple[AtomicAttack, AttackExecutorResult[AttackResult]] | BaseException],
    ) -> list[BaseException]:
        """
        Convert worker outcomes into a flat list of errors for the caller to raise.

        Each outcome is either:
            - ``BaseException``: the atomic attack raised; log and surface as-is.
            - ``(AtomicAttack, result)``: ran to completion. If the result reports
              incomplete objectives, ``_partial_result_to_exception`` produces a
              ``ScenarioPartialFailureException``.

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
