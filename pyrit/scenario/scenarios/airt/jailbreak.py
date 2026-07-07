# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.common import apply_defaults
from pyrit.datasets import TextJailBreak
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.models import Parameter
from pyrit.prompt_converter import PromptConverter, TextJailbreakConverter
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.matrix_atomic_attack_builder import (
    MatrixAtomicAttackBuilder,
    MatrixCombo,
    resolve_technique_factories,
)
from pyrit.scenario.core.scenario import Scenario
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.score import TrueFalseScorer

if TYPE_CHECKING:
    from pyrit.scenario.core.scenario_strategy import ScenarioStrategy

logger = logging.getLogger(__name__)


class _Unset:
    """Sentinel marking an omitted ``num_templates`` argument (distinct from an explicit ``None``)."""


_UNSET = _Unset()


def _prompt_sending_factory() -> AttackTechniqueFactory:
    """
    Build the scenario-local "just send" technique factory.

    The shared ``core`` technique catalog intentionally omits a bare
    ``PromptSendingAttack`` (the central baseline covers it), but Jailbreak's
    default technique is to send the jailbroken objective unmodified, so it
    supplies its own.

    Returns:
        AttackTechniqueFactory: The ``prompt_sending`` factory.
    """
    return AttackTechniqueFactory(
        name="prompt_sending",
        attack_class=PromptSendingAttack,
        strategy_tags=["single_turn"],
    )


@cache
def _build_jailbreak_strategy() -> type["ScenarioStrategy"]:
    """
    Build the Jailbreak strategy class dynamically from the registered technique factories.

    Mirrors ``RapidResponse``: the technique axis is the shared ``core`` catalog
    (role_play, many_shot, tap, crescendo, …) plus the scenario-local
    ``prompt_sending`` technique. The scenario's ``default_strategy`` is the
    concrete ``prompt_sending`` member, so a no-strategy run "just sends" the
    jailbroken objective; ``single_turn`` / ``multi_turn`` / ``all`` aggregates
    remain available for broader selection.

    Returns:
        type[ScenarioStrategy]: The dynamically generated strategy enum class.
    """
    from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
    from pyrit.registry.tag_query import TagQuery

    registry = AttackTechniqueRegistry.get_registry_singleton()
    core_factories = TagQuery.all("core").filter(list(registry.get_factories_or_raise().values()))
    factories = [_prompt_sending_factory(), *core_factories]

    return AttackTechniqueRegistry.build_strategy_class_from_factories(  # type: ignore[ty:invalid-return-type]
        class_name="JailbreakStrategy",
        factories=factories,
        aggregate_tags={
            "single_turn": TagQuery.any_of("single_turn"),
            "multi_turn": TagQuery.any_of("multi_turn"),
        },
    )


class Jailbreak(Scenario):
    """
    Jailbreak scenario implementation for PyRIT.

    Tests how vulnerable a model is to jailbreak templates along three orthogonal axes:

    * **objectives** — harmful objectives, selected with ``--dataset-names`` (default HarmBench).
    * **techniques** — how each jailbroken objective is delivered; default is to "just send"
      (``prompt_sending``), with the shared ``core`` techniques (role_play, many_shot, …)
      selectable via ``--strategies``.
    * **jailbreaks** — which jailbreak templates to apply, a random sample by default or an
      explicit list via ``jailbreak_names`` / ``--num-templates``.

    Each ``(technique x objective)`` pair is built for every selected jailbreak template, with
    the template applied via a ``TextJailbreakConverter``. Results group by jailbreak template.
    """

    VERSION: int = 3

    #: Number of jailbreak templates sampled by default when neither the constructor argument
    #: nor the ``num_templates`` runtime parameter is supplied. The full catalog ships many
    #: templates; this is a small, fast random subset. Raise ``--num-templates`` for broader
    #: coverage, or pass ``num_templates=None`` to run the full catalog.
    DEFAULT_NUM_TEMPLATES: ClassVar[int] = 10

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["harmbench"]

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        """
        Declare runtime parameters settable from the CLI / config file.

        Returns:
            list[Parameter]: Parameters configurable per-run, exposed as ``--jailbreak-names``,
            ``--num-templates`` and ``--num-attempts``.
        """
        return [
            Parameter(
                name="jailbreak_names",
                description=(
                    "Specific jailbreak template names to run (the jailbreaks axis). When omitted, "
                    "a random sample of size num_templates is drawn. Mutually exclusive with num_templates."
                ),
                param_type=list[str],
                default=[],
            ),
            Parameter(
                name="num_templates",
                description=(
                    "Number of jailbreak templates to randomly sample from the full catalog. "
                    "Lower this for a faster run; raise it for broader coverage."
                ),
                param_type=int,
                default=cls.DEFAULT_NUM_TEMPLATES,
            ),
            Parameter(
                name="num_attempts",
                description="Number of times to run each selected jailbreak template.",
                param_type=int,
                default=1,
            ),
        ]

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
        num_templates: "int | None | _Unset" = _UNSET,
        num_attempts: int | None = None,
        jailbreak_names: list[str] | None = None,
    ) -> None:
        """
        Initialize the jailbreak scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Scorer for detecting successful jailbreaks
                (non-refusal). If not provided, defaults to an inverted refusal scorer.
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
                On resume the template names chosen by the original run are replayed (read from
                ``ScenarioResult.metadata``) so the atomic-attack set stays stable across processes.
            num_templates (int | None): Number of random jailbreak templates to run. When omitted,
                falls back to the ``num_templates`` runtime parameter (default
                ``DEFAULT_NUM_TEMPLATES``). An explicit integer takes precedence over the parameter.
                Pass ``num_templates=None`` to opt out of sampling and run the full catalog.
            num_attempts (int | None): Number of times to try each jailbreak. When omitted, falls back
                to the ``num_attempts`` runtime parameter (default 1).
            jailbreak_names (list[str] | None): Specific jailbreak template names to use. Mutually
                exclusive with ``num_templates`` (random selection).

        Raises:
            ValueError: If both jailbreak_names and num_templates are provided, as random selection
                is incompatible with a predetermined list.
            ValueError: If the jailbreak_names list contains a jailbreak that isn't in the listed
                templates.
        """
        if jailbreak_names is None:
            jailbreak_names = []
        if jailbreak_names and not isinstance(num_templates, _Unset):
            raise ValueError(
                "Please provide only one of `num_templates` (random selection)"
                " or `jailbreak_names` (specific selection)."
            )

        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )

        # Distinguish an omitted argument (use the runtime default) from an explicit ``None``
        # (opt out of sampling and run the full catalog).
        if isinstance(num_templates, _Unset):
            self._num_templates_unset = True
            self._num_templates: int | None = None
        else:
            self._num_templates_unset = False
            self._num_templates = num_templates
        self._num_attempts = num_attempts

        # Template resolution is split by selection mode:
        # * ``jailbreak_names`` (explicit selection) is validated and resolved eagerly here so an
        #   unknown name fails fast at construction time.
        # * Random ``num_templates`` selection is deferred to ``_build_atomic_attacks_async`` so the
        #   ``num_templates`` runtime parameter (populated into ``self.params`` during
        #   ``initialize_async``) is honored — ``self.params`` does not exist yet in ``__init__``.
        if jailbreak_names:
            all_templates = TextJailBreak.get_jailbreak_templates()
            diff = set(jailbreak_names) - set(all_templates)
            if diff:
                raise ValueError(f"Error: could not find templates `{diff}`!")
            self._jailbreaks: list[str] = jailbreak_names
            self._jailbreaks_explicit = True
        else:
            self._jailbreaks = []
            self._jailbreaks_explicit = False

        strategy_class = _build_jailbreak_strategy()

        super().__init__(
            version=self.VERSION,
            strategy_class=strategy_class,
            default_strategy=strategy_class("prompt_sending"),
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["harmbench"], max_dataset_size=4),
            objective_scorer=self._objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    @property
    def selected_jailbreak_names(self) -> list[str]:
        """
        Jailbreak template names selected for this run.

        Populated once ``initialize_async`` has resolved the sample (or replayed the persisted set
        on ``--resume``). For the random-sampling path this is empty before initialization. The same
        list is also persisted to ``ScenarioResult.metadata`` and surfaced in the scenario output.

        Returns:
            list[str]: The jailbreak template names this run executes.
        """
        return list(self._jailbreaks)

    def _load_persisted_jailbreak_names(self) -> list[str] | None:
        """
        Return the template names persisted by a prior run when resuming, otherwise ``None``.

        Template resolution happens inside ``_build_atomic_attacks_async``, which the base class runs
        *before* it applies persisted resume state. Since each template is its own atomic attack,
        the persisted names must be read here (not in ``_apply_persisted_objectives``) so the resumed
        run rebuilds the same atomic attacks instead of drawing a fresh random sample.

        Returns:
            list[str] | None: The persisted template names, or ``None`` when not resuming or when no
            names were persisted.
        """
        if not self._scenario_result_id:
            return None
        stored = self._memory.get_scenario_results(scenario_result_ids=[self._scenario_result_id])
        if not stored:
            return None
        names = (stored[0].metadata or {}).get("jailbreak_template_names")
        if not names:
            return None
        return list(names)

    def _resolve_jailbreaks(self) -> list[str]:
        """
        Resolve the jailbreak templates to run.

        Resolution precedence:

        1. On resume, replay the template names persisted by the original run (deterministic resume).
        2. Explicit constructor ``jailbreak_names`` (resolved and validated in ``__init__``).
        3. The ``jailbreak_names`` runtime parameter (specific selection via CLI / config).
        4. An explicit constructor ``num_templates`` (an integer wins over the runtime parameter; an
           explicit ``None`` opts out of sampling and runs the full catalog).
        5. The ``num_templates`` runtime parameter, which defaults to ``DEFAULT_NUM_TEMPLATES``.

        Returns:
            list[str]: The jailbreak template file names to run.

        Raises:
            ValueError: If the ``jailbreak_names`` runtime parameter contains a name that is not in
                the template catalog.
        """
        persisted = self._load_persisted_jailbreak_names()
        if persisted is not None:
            return persisted
        if self._jailbreaks_explicit:
            return self._jailbreaks
        runtime_names = self.params.get("jailbreak_names") if hasattr(self, "params") else None
        if runtime_names:
            all_templates = TextJailBreak.get_jailbreak_templates()
            diff = set(runtime_names) - set(all_templates)
            if diff:
                raise ValueError(f"Error: could not find templates `{diff}`!")
            return list(runtime_names)
        num_templates = self.params["num_templates"] if self._num_templates_unset else self._num_templates
        return TextJailBreak.get_jailbreak_templates(num_templates=num_templates)

    def _build_initial_scenario_metadata(self) -> dict[str, Any]:
        """
        Persist the resolved template names so ``--resume`` replays the same sample.

        Extends the base ``objective_hashes`` persistence (preserved via ``super()``) with the
        concrete template names chosen for this run, mirroring that pattern for the template axis.

        Returns:
            dict[str, Any]: Metadata payload for the new ScenarioResult.
        """
        metadata = super()._build_initial_scenario_metadata()
        names = list(self._jailbreaks)
        metadata["jailbreak_template_names"] = names
        summary = metadata.setdefault("summary", {})
        summary["Jailbreak templates"] = ", ".join(names)
        return metadata

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build the ``technique x objective`` atomic attacks for every selected jailbreak template.

        Resolves the jailbreak templates now that runtime parameters are populated (and replays the
        persisted sample on ``--resume``), then for each template builds the technique x objective
        cross-product via ``MatrixAtomicAttackBuilder``, injecting that template's
        ``TextJailbreakConverter`` as a request converter for every technique. Results group by
        jailbreak template. The baseline (plain objective, no jailbreak) is emitted centrally by the
        base ``initialize_async``, so this override never prepends one.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: One atomic attack per ``(technique x objective x jailbreak x attempt)``.
        """
        self._jailbreaks = self._resolve_jailbreaks()
        logger.info(
            "Jailbreak scenario running %d template(s): %s",
            len(self._jailbreaks),
            ", ".join(self._jailbreaks),
        )
        num_attempts = self._num_attempts if self._num_attempts is not None else self.params["num_attempts"]

        factories = resolve_technique_factories(
            context=context, extra_factories={"prompt_sending": _prompt_sending_factory()}
        )
        builder = MatrixAtomicAttackBuilder(
            objective_target=context.objective_target,
            objective_scorer=self._objective_scorer,
            memory_labels=context.memory_labels,
        )

        atomic_attacks: list[AtomicAttack] = []
        for template_name in self._jailbreaks:
            stem = Path(template_name).stem
            converter = TextJailbreakConverter(jailbreak_template=TextJailBreak(template_file_name=template_name))
            strategy_converters: dict[str, list[PromptConverter]] = {name: [converter] for name in factories}
            for attempt in range(num_attempts):

                def name_fn(combo: MatrixCombo, *, stem: str = stem, attempt: int = attempt) -> str:
                    base = f"jailbreak_{combo.technique_name}_{stem}_{combo.dataset_name}"
                    return base if num_attempts == 1 else f"{base}_attempt{attempt + 1}"

                atomic_attacks.extend(
                    builder.build(
                        technique_factories=factories,
                        dataset_groups=context.seed_groups_by_dataset,
                        strategy_converters=strategy_converters,
                        name_fn=name_fn,
                        display_group_fn=lambda combo, *, stem=stem: stem,
                        include_baseline=False,
                    )
                )

        return atomic_attacks
