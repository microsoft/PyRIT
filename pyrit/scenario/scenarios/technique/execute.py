# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Execute one catalog technique against one objective, with optional stored history."""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pyrit.common import apply_defaults
from pyrit.executor.attack import AttackScoringConfig
from pyrit.executor.attack.component.conversation_manager import get_prepended_turn_count
from pyrit.executor.attack.compound.sequential_attack import (
    SequenceCompletionPolicy,
    SequentialAttack,
    SequentialChildAttack,
)
from pyrit.models import (
    AttackSeedGroup,
    BoundedDatasetSize,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateStatus,
    SeedObjective,
    SeedPrompt,
    config_hash,
)
from pyrit.models.parameter import Parameter
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.registry import AttackTechniqueRegistry
from pyrit.scenario.core import (
    AtomicAttack,
    AttackTechnique,
    BaselineAttackPolicy,
    DatasetAttackConfiguration,
    Scenario,
)
from pyrit.scenario.core.matrix_atomic_attack_builder import (
    build_baseline_atomic_attack,
    filter_compatible_seed_groups,
    resolve_technique_factories,
)

if TYPE_CHECKING:
    from pyrit.models import ScenarioIdentifier, ScenarioResult
    from pyrit.prompt_target import PromptTarget
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.score import TrueFalseScorer


@cache
def _build_execute_technique() -> type[ScenarioTechnique]:
    """
    Build a catalog with one deterministic default and all registered techniques.

    Returns:
        type[ScenarioTechnique]: The current catalog enum.
    """
    factories = list(AttackTechniqueRegistry.get_registry_singleton().get_factories_or_raise().values())
    names = {factory.name for factory in factories}
    default_name = "red_teaming" if "red_teaming" in names else sorted(names)[0]
    return cast(
        "type[ScenarioTechnique]",
        AttackTechniqueRegistry.build_technique_class_from_factories(
            class_name="ExecuteTechnique", factories=factories, default_names={default_name}
        ),
    )


class Execute(Scenario):
    """
    Run one selected attack technique toward one free-text objective.

    Optionally continue from a stored conversation without changing the source.
    Retry on Objective Failure repeats the technique with fresh execution state and
    stops on success. Max Retries handles execution errors separately.
    """

    VERSION: int = 1
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Disabled
    USES_DATASET_SIZE_LIMIT: ClassVar[bool] = False
    MAX_CONCRETE_TECHNIQUES: ClassVar[int | None] = 1
    PREPENDED_CONVERSATION_HASH_KEY: ClassVar[str] = "prepended_conversation_hash"

    @apply_defaults
    def __init__(
        self, *, objective_scorer: TrueFalseScorer | None = None, scenario_result_id: str | None = None
    ) -> None:
        """Initialize the scenario with an optional scorer and resume ID."""
        self._prepended_conversation_hash: str | None = None
        self._objective_scorer: TrueFalseScorer = objective_scorer or self._get_default_objective_scorer()
        super().__init__(
            version=self.VERSION,
            technique_class=_build_execute_technique(),
            default_dataset_config=DatasetAttackConfiguration(seed_groups=[], max_total="all"),
            objective_scorer=self._objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    @classmethod
    def supported_parameters(cls) -> list[Parameter]:
        """
        Declare inputs without dataset configuration.

        Returns:
            list[Parameter]: Supported run inputs.
        """
        return [parameter for parameter in super().supported_parameters() if parameter.name != "dataset_config"]

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare the objective, categories, retries, and source conversation.

        Returns:
            list[Parameter]: Scenario-specific run inputs.
        """
        return [
            Parameter(
                name="objective",
                description="Free-text objective to achieve.",
                param_type=str,
                default=None,
                multiline=True,
            ),
            Parameter(
                name="harm_categories",
                description="Harm categories recorded on results.",
                param_type=list[str],
                default=[],
            ),
            Parameter(
                name="retries_on_objective_failure",
                description="Additional attempts after a non-success outcome; stop on success.",
                param_type=int,
                default=0,
            ),
            Parameter(
                name="prepended_conversation_id",
                description="Stored conversation to copy as history. The source remains unchanged.",
                param_type=str,
                default=None,
            ),
        ]

    def _validate_runtime_configuration(self) -> None:
        super()._validate_runtime_configuration()
        if self.params["retries_on_objective_failure"] < 0:
            raise ValueError("retries_on_objective_failure must be nonnegative")
        conversation_id = self.params.get("prepended_conversation_id")
        if conversation_id is not None and not conversation_id.strip():
            raise ValueError("prepended_conversation_id must not be blank")

    def _get_run_size_budget(self) -> BoundedDatasetSize:
        return BoundedDatasetSize(value=1)

    async def _estimate_run_size_async(self, *, budget: BoundedDatasetSize) -> ScenarioRunSizeEstimate:
        components = [ScenarioRunSizeComponent(label="Selected technique", count=1)]
        if self._include_baseline:
            components.append(ScenarioRunSizeComponent(label="Baseline", count=1, is_baseline=True))
        attempts = self.params["retries_on_objective_failure"] + 1
        return ScenarioRunSizeEstimate(
            status=ScenarioRunSizeEstimateStatus.Approximate,
            total_attack_count=sum(component.count for component in components),
            components=components,
            note=f"One objective. Up to {attempts} technique attempt(s) run inside one unit; baseline is not retried.",
        )

    async def _resolve_seed_groups_by_dataset_async(
        self, *, apply_sampling: bool = True
    ) -> dict[str, list[AttackSeedGroup]]:
        objective = self.params.get("objective")
        if not objective or not objective.strip():
            raise ValueError("technique.execute requires a non-blank objective")
        history = await self._read_history_seeds_async()
        group = AttackSeedGroup(
            seeds=[SeedObjective(value=objective, harm_categories=self.params["harm_categories"]), *history]
        )
        return {"objective": [group]}

    async def _read_history_seeds_async(self) -> list[SeedPrompt]:
        self._prepended_conversation_hash = None
        conversation_id = self.params.get("prepended_conversation_id")
        if conversation_id is None:
            return []
        messages = await self._memory.get_conversation_messages_async(conversation_id=conversation_id)
        if not messages:
            raise ValueError(f"Prepended conversation '{conversation_id}' is empty or does not exist")
        if any(message.is_error() for message in messages):
            raise ValueError(f"Prepended conversation '{conversation_id}' contains blocked or error pieces")
        self._prepended_conversation_hash = config_hash(
            {
                "messages": [
                    [
                        piece.model_dump(
                            mode="json",
                            include={"role", "converted_value", "converted_value_data_type", "prompt_metadata"},
                        )
                        for piece in message.message_pieces
                    ]
                    for message in messages
                ]
            }
        )
        return SeedPrompt.from_messages(list(messages))

    def _build_initial_scenario_metadata(self) -> dict[str, Any]:
        metadata = super()._build_initial_scenario_metadata()
        if self._prepended_conversation_hash is not None:
            metadata[self.PREPENDED_CONVERSATION_HASH_KEY] = self._prepended_conversation_hash
        return metadata

    def _validate_stored_scenario(
        self, *, stored_result: ScenarioResult, current_identifier: ScenarioIdentifier
    ) -> None:
        super()._validate_stored_scenario(stored_result=stored_result, current_identifier=current_identifier)
        if stored_result.metadata.get(self.PREPENDED_CONVERSATION_HASH_KEY) != self._prepended_conversation_hash:
            raise ValueError(
                f"Prepended conversation '{self.params.get('prepended_conversation_id')}' "
                "does not match the saved history. Restore the source history or start a new scenario."
            )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        name, factory = next(iter(resolve_technique_factories(context=context).items()))
        groups = filter_compatible_seed_groups(factory=factory, seed_groups=context.seed_groups)
        if not groups:
            raise ValueError(f"Technique '{name}' has no compatible seed group")
        factory = factory.with_extra_turns(turns=get_prepended_turn_count(groups[0].prepended_conversation))
        converters = self._technique_converters.get(name)
        technique = factory.create(
            objective_target=context.objective_target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=self._objective_scorer),
            extra_request_converters=(
                ConverterConfiguration.from_converters(converters=converters) if converters else None
            ),
        )
        adversarial_chat = factory.resolve_adversarial_chat()
        execution_technique = self._with_objective_retries(
            context=context, technique=technique, group=groups[0], adversarial_chat=adversarial_chat
        )
        attacks = [
            AtomicAttack(
                atomic_attack_name=f"{name}_objective",
                technique_name=name,
                display_group=name,
                attack_technique=execution_technique,
                seed_groups=groups,
                adversarial_chat=adversarial_chat,
                objective_scorer=self._objective_scorer,
                memory_labels=context.memory_labels,
            )
        ]
        if context.include_baseline:
            attacks.insert(
                0,
                build_baseline_atomic_attack(
                    objective_target=context.objective_target,
                    objective_scorer=self._objective_scorer,
                    seed_groups=list(context.seed_groups),
                    memory_labels=context.memory_labels,
                ),
            )
        return attacks

    def _with_objective_retries(
        self,
        *,
        context: ScenarioContext,
        technique: AttackTechnique,
        group: AttackSeedGroup,
        adversarial_chat: PromptTarget | None,
    ) -> AttackTechnique:
        retries = self.params["retries_on_objective_failure"]
        if not retries:
            return technique
        execution_group = (
            group.with_technique(technique=technique.seed_technique) if technique.seed_technique is not None else group
        )
        children = [
            SequentialChildAttack(
                strategy=technique.attack,
                seed_group=execution_group,
                adversarial_chat=adversarial_chat,
                objective_scorer=self._objective_scorer,
                memory_labels={"_objective_attempt": str(attempt + 1)},
                technique_identifier=technique.get_identifier(),
                identity_seed_group=group,
            )
            for attempt in range(retries + 1)
        ]
        return AttackTechnique(
            attack=SequentialAttack(
                objective_target=context.objective_target,
                child_attacks=children,
                completion_policy=SequenceCompletionPolicy.FIRST_SUCCESS,
            )
        )
