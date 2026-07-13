# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.common import apply_defaults
from pyrit.converter import TextJailbreakConverter
from pyrit.datasets import TextJailBreak
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.models import Parameter
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.registry.tag_query import TagQuery
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.matrix_atomic_attack_builder import (
    MatrixAtomicAttackBuilder,
    resolve_technique_factories,
)
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario

if TYPE_CHECKING:
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.score import TrueFalseScorer

# Small curated default set so a bare run stays fast and representative. The full catalog is
# opt-in via ``num_templates`` (random sample) or ``jailbreak_names`` (explicit) — jailbreak
# templates multiply against objectives and techniques, so the default is kept small.
_DEFAULT_JAILBREAK_NAMES = ["aim.yaml", "dan_11.yaml"]

# Metadata key under which the resolved jailbreak templates are persisted, so a resumed run
# replays the exact same set even when ``num_templates`` drew a random sample.
_JAILBREAK_TEMPLATES_METADATA_KEY = "jailbreak_templates"

# Scenario-local "just send" technique. The core catalog intentionally omits a bare
# ``PromptSendingAttack`` (baseline normally covers it), but a jailbreak run needs one so the
# default technique delivers the jailbreak framing without layering another attack on top. It is
# injected locally (like Leakage's ``first_letter`` / ``image``) so it stays out of the global
# registry, and it is this scenario's default technique.
_PROMPT_SENDING_TECHNIQUE_NAME = "prompt_sending"


@cache
def _prompt_sending_factory() -> AttackTechniqueFactory:
    """
    Build the scenario-local ``prompt_sending`` ("just send") technique factory.

    Returns:
        AttackTechniqueFactory: A ``PromptSendingAttack`` factory with no seed technique, so the
        objective (jailbroken inline by the ``TextJailbreakConverter``) is sent directly with no
        additional attack layered on top.
    """
    return AttackTechniqueFactory(
        name=_PROMPT_SENDING_TECHNIQUE_NAME,
        attack_class=PromptSendingAttack,
        technique_tags=["single_turn"],
    )


@cache
def _build_jailbreak_technique() -> type[ScenarioTechnique]:
    """
    Build the Jailbreak technique class dynamically from the registry + local ``prompt_sending``.

    The technique axis is the set of *attack techniques* a jailbreak template is delivered through:
    ``prompt_sending`` (the default "just send") plus the registry techniques (``role_play_*``,
    ``many_shot``, ``tap``, …). Jailbreak templates are a separate selector
    (``num_templates`` / ``jailbreak_names``), so the default technique set is kept to just
    ``prompt_sending`` — crossing every template with every technique explodes quickly.

    Returns:
        type[ScenarioTechnique]: The dynamically generated technique enum class.
    """
    registry = AttackTechniqueRegistry.get_registry_singleton()
    core_factories = list(registry.get_factories_or_raise().values())
    all_factories = core_factories + [_prompt_sending_factory()]
    return AttackTechniqueRegistry.build_technique_class_from_factories(  # type: ignore[return-value, ty:invalid-return-type]
        class_name="JailbreakTechnique",
        factories=all_factories,
        aggregate_tags={
            "single_turn": TagQuery.any_of("single_turn"),
            "multi_turn": TagQuery.any_of("multi_turn"),
        },
        default_technique_names={_PROMPT_SENDING_TECHNIQUE_NAME},
    )


class Jailbreak(Scenario):
    """
    Jailbreak scenario implementation for PyRIT.

    Tests how vulnerable a model is to jailbreak templates. A run is the cross-product of three
    selectors:

    - **dataset** — the harmful objectives (HarmBench).
    - **techniques** — the *attack techniques* each jailbreak is delivered through. The default is
      ``prompt_sending`` ("just send"); the registry techniques (``role_play_*``, ``many_shot``,
      ``tap``, …) are opt-in.
    - **jailbreaks** — which jailbreak templates to run (``num_templates`` random sample or an
      explicit ``jailbreak_names`` set; a small curated default otherwise).

    Each selected jailbreak template is applied as a ``TextJailbreakConverter`` on every selected
    technique's outgoing requests, so the objective is rendered inline into the template's
    ``{{prompt}}`` slot and the target sees the jailbroken prompt exactly as authored. Delivering the
    jailbreak as a request converter (rather than prepended framing) keeps the scenario
    target-agnostic and lets it compose with every technique, including the simulated-conversation
    ones. Responses are scored to determine whether the jailbreak succeeded (non-refusal).
    """

    VERSION: int = 2

    #: Baseline (an un-jailbroken prompt-send over the objectives) is supported but off by default:
    #: the jailbreak templates dominate the run. Callers opt in per run with ``include_baseline=True``.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Disabled

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["harmbench"]

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare the run-configurable parameters this scenario accepts (CLI / config file).

        Returns:
            list[Parameter]: The jailbreak-template selectors (``num_templates``, ``jailbreak_names``)
            and the ``num_attempts`` repeat-count parameter.
        """
        return [
            Parameter(
                name="num_templates",
                description=(
                    "Draw this many random jailbreak templates instead of the curated default set. "
                    "Mutually exclusive with jailbreak_names."
                ),
                param_type=int,
                default=None,
            ),
            Parameter(
                name="num_attempts",
                description="Number of times to try each (technique x jailbreak template x objective).",
                param_type=int,
                default=1,
            ),
            Parameter(
                name="jailbreak_names",
                description=(
                    "Explicit jailbreak template file names to run (e.g. aim.yaml dan_11.yaml). "
                    "When omitted, a small curated default set is used. Mutually exclusive with num_templates."
                ),
                param_type=list[str],
                default=None,
            ),
        ]

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the jailbreak scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Scorer for detecting successful jailbreaks
                (non-refusal). If not provided, defaults to an inverted refusal scorer.
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
        """
        self._objective_scorer: TrueFalseScorer = (
            objective_scorer if objective_scorer else self._get_default_objective_scorer()
        )
        # Resolved lazily at build time (from the run parameter bag) and cached so the same
        # template set feeds both attack construction and the persisted metadata.
        self._resolved_jailbreaks: list[str] = []

        technique_class = _build_jailbreak_technique()

        super().__init__(
            version=self.VERSION,
            technique_class=technique_class,
            default_technique=technique_class("default"),
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["harmbench"], max_dataset_size=4),
            objective_scorer=self._objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    def _resolve_templates(self) -> list[str]:
        """
        Resolve the jailbreak templates for this run, replaying the persisted set on resume.

        On a fresh run this reads the run parameters: an explicit ``jailbreak_names`` set, a
        random ``num_templates`` sample, or the curated default. On resume the originally chosen
        set is read back from the stored ``ScenarioResult`` metadata so a random sample isn't
        redrawn (which would diverge from the persisted attacks).

        Returns:
            list[str]: The jailbreak template file names to run.

        Raises:
            ValueError: If both ``num_templates`` and ``jailbreak_names`` are provided, or if
                ``jailbreak_names`` contains an unknown template.
        """
        if self._scenario_result_id is not None:
            stored = self._memory.get_scenario_results(scenario_result_ids=[self._scenario_result_id])
            if stored:
                persisted = (stored[0].metadata or {}).get(_JAILBREAK_TEMPLATES_METADATA_KEY)
                if persisted:
                    return list(persisted)

        num_templates = self.params.get("num_templates")
        jailbreak_names = self.params.get("jailbreak_names")

        if jailbreak_names and num_templates:
            raise ValueError(
                "Please provide only one of `num_templates` (random selection)"
                " or `jailbreak_names` (specific selection)."
            )
        if jailbreak_names:
            available = set(TextJailBreak.get_jailbreak_templates())
            diff = set(jailbreak_names) - available
            if diff:
                raise ValueError(f"Error: could not find templates `{diff}`!")
            return list(jailbreak_names)
        if num_templates:
            return TextJailBreak.get_jailbreak_templates(num_templates=num_templates)
        return list(_DEFAULT_JAILBREAK_NAMES)

    def _build_initial_scenario_metadata(self) -> dict[str, Any]:
        """
        Persist the resolved jailbreak templates alongside the base scenario metadata.

        Returns:
            dict[str, Any]: The base metadata plus the resolved jailbreak template set.
        """
        metadata = super()._build_initial_scenario_metadata()
        metadata[_JAILBREAK_TEMPLATES_METADATA_KEY] = list(self._resolved_jailbreaks)
        return metadata

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build one atomic attack per (technique x jailbreak template x dataset x attempt).

        Each selected jailbreak template becomes a ``TextJailbreakConverter`` appended to every
        selected technique's request converters, so the objective is rendered inline into the
        template's ``{{prompt}}`` slot on the wire. Delivering the jailbreak as a request converter
        (rather than prepended framing) keeps the scenario target-agnostic and lets it compose with
        every technique, including the simulated-conversation ones. Results group by jailbreak
        template so per-template ASR rolls up naturally.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The atomic attacks to execute.

        Raises:
            ValueError: If the scenario is not properly initialized.
        """
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        self._resolved_jailbreaks = self._resolve_templates()
        num_attempts = self.params.get("num_attempts", 1)

        technique_factories = resolve_technique_factories(
            context=context,
            extra_factories={_PROMPT_SENDING_TECHNIQUE_NAME: _prompt_sending_factory()},
        )
        builder = MatrixAtomicAttackBuilder(
            objective_target=self._objective_target,
            objective_scorer=self._objective_scorer,
            memory_labels=context.memory_labels,
        )

        atomic_attacks: list[AtomicAttack] = []
        for template_file_name in self._resolved_jailbreaks:
            template_stem = Path(template_file_name).stem
            jailbreak_converter = TextJailbreakConverter(
                jailbreak_template=TextJailBreak(template_file_name=template_file_name)
            )
            # Within the extra-converter stack, apply the jailbreak first (wrap the raw objective in
            # the template), then any per-technique converters the caller layered on via
            # ``--techniques <name>:converter.*``. (A technique's own built-in converters, if any,
            # still run ahead of this extra stack inside the factory.)
            technique_converters = {
                technique_name: [jailbreak_converter, *self._technique_converters.get(technique_name, [])]
                for technique_name in technique_factories
            }
            for attempt in range(num_attempts):
                suffix = f"_attempt{attempt + 1}" if num_attempts > 1 else ""
                atomic_attacks.extend(
                    builder.build(
                        technique_factories=technique_factories,
                        dataset_groups=context.seed_groups_by_dataset,
                        technique_converters=technique_converters,
                        name_fn=lambda combo, stem=template_stem, suffix=suffix: (
                            f"{combo.technique_name}_{stem}_{combo.dataset_name}{suffix}"
                        ),
                        display_group_fn=lambda combo, stem=template_stem: stem,
                        include_baseline=False,
                    )
                )
        return atomic_attacks
