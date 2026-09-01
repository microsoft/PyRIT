# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.common import apply_defaults
from pyrit.converter import TextJailbreakConverter
from pyrit.datasets import TextJailBreak
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.models import (
    AttackTechniqueSeedGroup,
    Parameter,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
)
from pyrit.prompt_target import CapabilityName
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.matrix_atomic_attack_builder import (
    MatrixAtomicAttackBuilder,
    build_baseline_atomic_attack,
    resolve_technique_factories,
)
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pyrit.converter import Converter
    from pyrit.models import AttackSeedGroup
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)

# Metadata key under which the resolved jailbreak templates are persisted, so a resumed run
# replays the exact same set even when a random sample was drawn.
_JAILBREAK_TEMPLATES_METADATA_KEY = "jailbreak_templates"

# How many jailbreak templates a bare run draws at random. Kept small so the default run stays fast
# — jailbreak templates multiply against objectives and techniques. Override per run with
# ``num_jailbreaks`` (random count) or ``jailbreak_names`` (an explicit set).
_DEFAULT_NUM_JAILBREAKS = 2

# Scenario-local default techniques. Both "just send" the jailbreak; they differ only in *where* the
# framing lands:
#   - ``prompt_sending`` renders the template inline into the user message (target-agnostic).
#   - ``jailbreak_system_prompt`` sets the template as the system prompt and sends the objective as
#     the user turn (only for targets that natively support editable history + system prompts).
# The registry deliberately omits a bare send, and system-prompt delivery is scenario-specific, so
# both are injected locally (like Leakage's ``first_letter`` / ``image``) and form the default set.
_PROMPT_SENDING = "prompt_sending"
_JAILBREAK_SYSTEM_PROMPT = "jailbreak_system_prompt"
_DEFAULT_TECHNIQUES: frozenset[str] = frozenset({_PROMPT_SENDING, _JAILBREAK_SYSTEM_PROMPT})


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
        name=_PROMPT_SENDING,
        attack_class=PromptSendingAttack,
        technique_tags=["single_turn"],
    )


@cache
def _jailbreak_system_prompt_factory() -> AttackTechniqueFactory:
    """
    Build the scenario-local ``jailbreak_system_prompt`` delivery technique factory.

    This base factory only carries the technique name and tags so the technique appears in the enum
    and the default set. The per-run system framing (which template to deliver) is attached in
    ``_build_atomic_attacks_async`` via ``_build_system_prompt_factory``, because the framing text is
    drawn per run.

    Returns:
        AttackTechniqueFactory: A ``PromptSendingAttack`` placeholder factory used for enum
        construction and technique selection.
    """
    return AttackTechniqueFactory(
        name=_JAILBREAK_SYSTEM_PROMPT,
        attack_class=PromptSendingAttack,
        technique_tags=["single_turn"],
    )


def _extra_default_factories() -> dict[str, AttackTechniqueFactory]:
    """Return the scenario-local default technique factories keyed by name."""
    return {
        _PROMPT_SENDING: _prompt_sending_factory(),
        _JAILBREAK_SYSTEM_PROMPT: _jailbreak_system_prompt_factory(),
    }


@cache
def _build_jailbreak_technique() -> type[ScenarioTechnique]:
    """
    Build the Jailbreak technique class from its two scenario-owned delivery methods.

    Returns:
        type[ScenarioTechnique]: The dynamically generated technique enum class.
    """
    return AttackTechniqueRegistry.build_technique_class_from_factories(  # type: ignore[return-value, ty:invalid-return-type]
        class_name="JailbreakTechnique",
        factories=list(_extra_default_factories().values()),
        default_names=set(_DEFAULT_TECHNIQUES),
    )


class Jailbreak(Scenario):
    """
    Jailbreak scenario implementation for PyRIT.

    Tests how vulnerable a model is to jailbreak templates. A run is the cross-product of three
    selectors:

    - **dataset** — the harmful objectives (HarmBench).
    - **techniques** — two delivery methods for each jailbreak: ``prompt_sending`` (the template
      rendered inline into the user message) and
      ``jailbreak_system_prompt`` (the template set as the system prompt with the objective sent as
      the user turn).
    - **jailbreaks** — which jailbreak templates to run (a random ``num_jailbreaks`` sample or an
      explicit ``jailbreak_names`` set).

    ``prompt_sending`` applies each template as a ``TextJailbreakConverter`` on the outgoing request,
    so the objective is rendered inline into the template's ``{{prompt}}`` slot.
    ``jailbreak_system_prompt`` instead sets the template as a native system prompt and sends the
    objective as its own user turn, so it is only built for targets that natively support editable
    history and system prompts (it is skipped for incapable targets, or raises if it is the only
    selected technique). Responses are scored to determine whether the jailbreak succeeded
    (non-refusal).
    """

    VERSION: int = 4

    #: Baseline (an un-jailbroken prompt-send over the objectives) is included by default: a model
    #: that complies with the bare objective is itself interesting signal. Callers opt out per run
    #: with ``include_baseline=False``.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Enabled

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return a list of dataset names required by this scenario."""
        return ["harmbench"]

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare the run-configurable parameters this scenario accepts (CLI / config file).

        Returns:
            list[Parameter]: The jailbreak-template selectors (``num_jailbreaks``, ``jailbreak_names``)
            and the ``num_jailbreak_attempts`` repeat-count parameter.
        """
        return [
            Parameter(
                name="num_jailbreaks",
                description=(
                    "Draw this many random jailbreak templates for the run. Mutually exclusive with jailbreak_names."
                ),
                param_type=int,
                default=None,
            ),
            Parameter(
                name="num_jailbreak_attempts",
                description="Number of times to try each (technique x jailbreak template x objective).",
                param_type=int,
                default=1,
            ),
            Parameter(
                name="jailbreak_names",
                description=(
                    "Explicit jailbreak template file names to run (e.g. aim.yaml dan_11.yaml). "
                    "When omitted, a random sample is drawn. Mutually exclusive with num_jailbreaks."
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
            default_dataset_config=DatasetAttackConfiguration(dataset_names=["harmbench"], max_dataset_size=4),
            objective_scorer=self._objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    def _resolve_scenario_techniques(self, *, scenario_techniques: Any) -> list[ScenarioTechnique]:
        """
        Resolve techniques while rejecting stale or incompatible enum members.

        Args:
            scenario_techniques (Any): Requested Jailbreak technique members.

        Returns:
            list[ScenarioTechnique]: Compatible concrete techniques.

        Raises:
            ValueError: If a caller supplies members from an older or different
                technique enum.
        """
        if scenario_techniques:
            incompatible = [item for item in scenario_techniques if not isinstance(item, self._technique_class)]
            if incompatible:
                values = [getattr(item, "value", repr(item)) for item in incompatible]
                raise ValueError(
                    "Jailbreak received stale or incompatible techniques "
                    f"{values}. Select 'prompt_sending' or 'jailbreak_system_prompt'."
                )
        return super()._resolve_scenario_techniques(scenario_techniques=scenario_techniques)

    def _resolve_templates(self) -> list[str]:
        """
        Resolve the jailbreak templates for this run, replaying the persisted set on resume.

        On a fresh run this reads the run parameters: an explicit ``jailbreak_names`` set or a random
        ``num_jailbreaks`` sample (defaulting to a small random draw when neither is given). On resume
        the originally chosen set is read back from the stored ``ScenarioResult`` metadata so a random
        sample isn't redrawn (which would diverge from the persisted attacks).

        Returns:
            list[str]: The jailbreak template file names to run.

        Raises:
            ValueError: If both ``num_jailbreaks`` and ``jailbreak_names`` are provided, or if
                ``jailbreak_names`` contains an unknown template.
        """
        if self._scenario_result_id is not None:
            stored = self._memory.get_scenario_results(scenario_result_ids=[self._scenario_result_id])
            if stored:
                persisted = (stored[0].metadata or {}).get(_JAILBREAK_TEMPLATES_METADATA_KEY)
                if persisted:
                    return list(persisted)

        num_jailbreaks = self.params.get("num_jailbreaks")
        jailbreak_names = self.params.get("jailbreak_names")

        if jailbreak_names and num_jailbreaks:
            raise ValueError(
                "Please provide only one of `num_jailbreaks` (random selection)"
                " or `jailbreak_names` (specific selection)."
            )
        if jailbreak_names:
            available = set(TextJailBreak.get_jailbreak_templates())
            diff = set(jailbreak_names) - available
            if diff:
                raise ValueError(f"Error: could not find templates `{diff}`!")
            return list(jailbreak_names)
        return TextJailBreak.get_jailbreak_templates(num_templates=num_jailbreaks or _DEFAULT_NUM_JAILBREAKS)

    def _build_initial_scenario_metadata(self) -> dict[str, Any]:
        """
        Persist the resolved jailbreak templates alongside the base scenario metadata.

        Returns:
            dict[str, Any]: The base metadata plus the resolved jailbreak template set.
        """
        metadata = super()._build_initial_scenario_metadata()
        metadata[_JAILBREAK_TEMPLATES_METADATA_KEY] = list(self._resolved_jailbreaks)
        return metadata

    async def _estimate_run_size_async(self) -> ScenarioRunSizeEstimate:
        """
        Estimate the template and attempt axes, preserving the target capability caveat.

        Returns:
            ScenarioRunSizeEstimate: Conditional target-aware estimate.

        Raises:
            ValueError: If native system-prompt delivery is the only selected
                technique but the selected target cannot support it.
        """
        selected_groups, datasets = await self._resolve_dataset_groups_for_estimate_async()
        seed_group_count = sum(len(groups) for groups in selected_groups.values())
        template_count = len(self.params.get("jailbreak_names") or []) or (
            self.params.get("num_jailbreaks") or _DEFAULT_NUM_JAILBREAKS
        )
        attempt_count = self.params.get("num_jailbreak_attempts") or 1
        technique_names = {technique.value for technique in self._scenario_techniques}
        converter_count = len(technique_names - {_JAILBREAK_SYSTEM_PROMPT})
        system_delivery_selected = _JAILBREAK_SYSTEM_PROMPT in technique_names
        system_delivery_supported = (
            self._target_supports_system_delivery(self._objective_target)
            if system_delivery_selected and self._objective_target is not None
            else None
        )
        if system_delivery_selected and system_delivery_supported is False and converter_count == 0:
            raise ValueError(
                "Technique 'jailbreak_system_prompt' requires an objective target with editable history "
                "and system-prompt support."
            )

        components: list[ScenarioRunSizeComponent] = []
        if self._include_baseline:
            components.append(
                ScenarioRunSizeComponent(
                    label="Baseline",
                    count=seed_group_count,
                    is_baseline=True,
                )
            )
        components.append(
            ScenarioRunSizeComponent(
                label="Inline jailbreak delivery",
                count=seed_group_count * template_count * attempt_count * converter_count,
                note=(
                    "Each planned unit is one template, one selected delivery technique, and one logical seed group. "
                    "num_jailbreaks selects templates; it is not a persisted result or attempt count."
                ),
            )
        )
        if system_delivery_selected and system_delivery_supported is not False:
            components.append(
                ScenarioRunSizeComponent(
                    label="Native system-prompt jailbreak delivery",
                    count=seed_group_count * template_count * attempt_count,
                    note=(
                        "The selected objective target supports native system-prompt delivery."
                        if system_delivery_supported is True
                        else "Included only when the objective target supports editable history and system prompts."
                    ),
                )
            )

        target_agnostic_count = sum(
            component.count for component in components if component.label != "Native system-prompt jailbreak delivery"
        )
        planned_count = sum(component.count for component in components)
        minimum_planned_count = (
            planned_count if system_delivery_selected and converter_count == 0 else target_agnostic_count
        )
        baseline_explanation = (
            f" Baseline adds one unit per selected seed group ({seed_group_count} units)."
            if self._include_baseline
            else " Baseline is disabled."
        )
        formula = (
            f"{template_count} template(s) x {seed_group_count} selected logical seed group(s) x "
            f"{converter_count} selected target-agnostic technique(s) x {attempt_count} configured attempt(s) "
            f"= {seed_group_count * template_count * attempt_count * converter_count} planned unit(s)."
        )
        estimated_attack_count = (
            None if system_delivery_selected and system_delivery_supported is None else planned_count
        )
        if estimated_attack_count is None:
            capability_note = (
                " The selected technique requires native system-prompt delivery; incompatible targets cannot run it."
                if converter_count == 0
                else (
                    f" {target_agnostic_count} total planned units for target-agnostic delivery; "
                    f"{planned_count} when native system-prompt delivery is supported."
                )
            )
        elif system_delivery_selected and system_delivery_supported is True:
            capability_note = " The selected target supports the native system-prompt component."
        elif system_delivery_selected:
            capability_note = " The selected target does not support native system-prompt delivery, so it is omitted."
        else:
            capability_note = ""
        jailbreak_names = self.params.get("jailbreak_names") or []
        effective_parameters: dict[str, int | list[str]] = {
            "num_jailbreak_attempts": attempt_count,
        }
        if jailbreak_names:
            effective_parameters["jailbreak_names"] = list(jailbreak_names)
        else:
            effective_parameters["num_jailbreaks"] = template_count
        return ScenarioRunSizeEstimate(
            estimated_attack_count=estimated_attack_count,
            minimum_attack_count=minimum_planned_count if estimated_attack_count is None else None,
            maximum_attack_count=planned_count if estimated_attack_count is None else None,
            components=components,
            datasets=datasets,
            effective_parameters=effective_parameters,
            note=f"{formula}{baseline_explanation}{capability_note}",
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build one atomic attack per (technique x jailbreak template x dataset x attempt).

        ``prompt_sending`` delivers each jailbreak template as a ``TextJailbreakConverter`` so the
        objective is rendered inline into the template's ``{{prompt}}`` slot on the wire.
        ``jailbreak_system_prompt`` instead delivers the template as a native system prompt (no
        converter) with the objective sent as its own user turn, so it is only built when the
        objective target natively supports editable history and system prompts. Results group by
        jailbreak template so per-template ASR rolls up naturally.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: The atomic attacks to execute.

        Raises:
            ValueError: If the scenario is not properly initialized, or if
                ``jailbreak_system_prompt`` is the only selected technique but the target cannot
                support it.
        """
        if self._objective_target is None:
            raise ValueError(
                "Scenario not properly initialized. Call await scenario.initialize_async() before running."
            )

        self._resolved_jailbreaks = self._resolve_templates()
        num_attempts = self.params.get("num_jailbreak_attempts", 1)

        technique_factories = resolve_technique_factories(context=context, extra_factories=_extra_default_factories())

        prompt_sending_factory = technique_factories.get(_PROMPT_SENDING)
        system_selected = _JAILBREAK_SYSTEM_PROMPT in technique_factories

        build_system_delivery = system_selected and self._target_supports_system_delivery(self._objective_target)
        if system_selected and not build_system_delivery:
            if prompt_sending_factory is None:
                raise ValueError(
                    "The 'jailbreak_system_prompt' technique needs a target that natively supports "
                    "editable history and system prompts. Choose a capable target or a different technique."
                )
            logger.warning(
                "Skipping 'jailbreak_system_prompt' delivery: target does not natively support "
                "editable history and system prompts. Running the remaining techniques."
            )

        builder = MatrixAtomicAttackBuilder(
            objective_target=self._objective_target,
            objective_scorer=self._objective_scorer,
            memory_labels=context.memory_labels,
        )

        atomic_attacks: list[AtomicAttack] = []
        if context.include_baseline:
            atomic_attacks.append(
                build_baseline_atomic_attack(
                    objective_target=context.objective_target,
                    objective_scorer=self._objective_scorer,
                    seed_groups=list(context.seed_groups),
                    memory_labels=context.memory_labels,
                )
            )
        for template_file_name in self._resolved_jailbreaks:
            template_stem = Path(template_file_name).stem

            if prompt_sending_factory is not None:
                jailbreak_converter = TextJailbreakConverter(
                    jailbreak_template=TextJailBreak(template_file_name=template_file_name)
                )
                # Apply the jailbreak before any caller-supplied prompt_sending converters.
                technique_converters = {
                    _PROMPT_SENDING: [
                        jailbreak_converter,
                        *self._technique_converters.get(_PROMPT_SENDING, []),
                    ]
                }
                atomic_attacks.extend(
                    self._build_delivery_attacks(
                        builder=builder,
                        technique_factories={_PROMPT_SENDING: prompt_sending_factory},
                        technique_converters=technique_converters,
                        dataset_groups=context.seed_groups_by_dataset,
                        template_stem=template_stem,
                        num_attempts=num_attempts,
                    )
                )

            if build_system_delivery:
                system_factory = self._build_system_prompt_factory(template_file_name=template_file_name)
                atomic_attacks.extend(
                    self._build_delivery_attacks(
                        builder=builder,
                        technique_factories={_JAILBREAK_SYSTEM_PROMPT: system_factory},
                        technique_converters={},
                        dataset_groups=context.seed_groups_by_dataset,
                        template_stem=template_stem,
                        num_attempts=num_attempts,
                    )
                )
        return atomic_attacks

    def _build_delivery_attacks(
        self,
        *,
        builder: MatrixAtomicAttackBuilder,
        technique_factories: dict[str, AttackTechniqueFactory],
        technique_converters: dict[str, list[Converter]],
        dataset_groups: Mapping[str, list[AttackSeedGroup]],
        template_stem: str,
        num_attempts: int,
    ) -> list[AtomicAttack]:
        """
        Build the (technique x dataset x attempt) atomic attacks for a single jailbreak template.

        Args:
            builder (MatrixAtomicAttackBuilder): The shared matrix builder.
            technique_factories (dict[str, AttackTechniqueFactory]): Factories to build for this
                delivery path (either the converter techniques or the system-prompt technique).
            technique_converters (dict[str, list[Converter]]): Per-technique extra request converters
                (empty for the system-prompt delivery so the jailbreak is not double-applied).
            dataset_groups (Mapping[str, list[AttackSeedGroup]]): Seed groups keyed by dataset name.
            template_stem (str): The jailbreak template stem, used for naming and grouping.
            num_attempts (int): How many times to repeat each combination.

        Returns:
            list[AtomicAttack]: The atomic attacks for this template and delivery path.
        """
        atomic_attacks: list[AtomicAttack] = []
        for attempt in range(num_attempts):
            suffix = f"_attempt{attempt + 1}" if num_attempts > 1 else ""
            atomic_attacks.extend(
                builder.build(
                    technique_factories=technique_factories,
                    dataset_groups=dataset_groups,
                    technique_converters=technique_converters,
                    name_fn=lambda combo, stem=template_stem, suffix=suffix: (
                        f"{combo.technique_name}_{stem}_{combo.dataset_name}{suffix}"
                    ),
                    display_group_fn=lambda combo, stem=template_stem: stem,
                    include_baseline=False,
                )
            )
        return atomic_attacks

    def _build_system_prompt_factory(self, *, template_file_name: str) -> AttackTechniqueFactory:
        """
        Build a ``jailbreak_system_prompt`` factory carrying a single template's framing.

        The template is rendered with an empty prompt so it is pure framing (persona setup) and
        attached as a ``role="system"`` technique seed. The objective is delivered separately as its
        own user turn when the matrix builder merges this technique into each objective seed group,
        so the framing never overwrites the objective.

        Args:
            template_file_name (str): The jailbreak template file name to deliver as a system prompt.

        Returns:
            AttackTechniqueFactory: A ``PromptSendingAttack`` factory whose system-role seed carries
            the rendered jailbreak framing.
        """
        framing = TextJailBreak(template_file_name=template_file_name).get_jailbreak_system_prompt()
        seed_technique = AttackTechniqueSeedGroup.from_system_prompt(framing)
        return AttackTechniqueFactory(
            name=_JAILBREAK_SYSTEM_PROMPT,
            attack_class=PromptSendingAttack,
            technique_tags=["single_turn"],
            seed_technique=seed_technique,
        )

    @staticmethod
    def _target_supports_system_delivery(target: PromptTarget) -> bool:
        """
        Return whether ``target`` can carry the ``jailbreak_system_prompt`` delivery natively.

        System-prompt delivery sets a system prompt and relies on the objective staying a separate
        live user turn; on a target without native editable history that turn is squashed into the
        framing and the objective is silently dropped. Both capabilities must therefore be native.

        Args:
            target (PromptTarget): The objective target for this run.

        Returns:
            bool: ``True`` when the target natively supports editable history and system prompts.
        """
        configuration = target.configuration
        return configuration.includes(capability=CapabilityName.EDITABLE_HISTORY) and configuration.includes(
            capability=CapabilityName.SYSTEM_PROMPT
        )
