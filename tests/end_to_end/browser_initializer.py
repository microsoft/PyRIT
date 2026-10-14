# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Offline execution and historical data for browser acceptance tests."""

import asyncio

from pyrit.common import apply_defaults
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.memory.memory_models import AttackResultEntry
from pyrit.models import AttackResult, Message, MessagePiece, SeedObjective
from pyrit.prompt_target import PromptTarget, TargetCapabilities, TargetConfiguration
from pyrit.registry import ScenarioRegistry, TargetRegistry
from pyrit.scenario.core import (
    AtomicAttack,
    AttackTechnique,
    BaselineAttackPolicy,
    DatasetAttackConfiguration,
    Scenario,
    ScenarioTechnique,
)
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.score import SubStringScorer
from pyrit.setup.pyrit_initializer import PyRITInitializer


class BrowserTarget(PromptTarget):
    """Return a deterministic response without provider access."""

    _DEFAULT_CONFIGURATION = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_system_prompt=True,
            supports_editable_history=True,
        )
    )

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        return [
            MessagePiece(
                role="assistant",
                original_value="Offline browser response.",
                conversation_id=normalized_conversation[-1].message_pieces[0].conversation_id,
            ).to_message()
        ]

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        if not normalized_conversation:
            raise ValueError("The browser target requires a message.")


class BrowserTechnique(ScenarioTechnique):
    ALL = ("all", {"all"})
    PromptSending = ("prompt_sending", {"local"})


class BrowserScenario(Scenario):
    """Execute one local prompt with the real attack and scorer."""

    VERSION = 1
    BASELINE_ATTACK_POLICY = BaselineAttackPolicy.Forbidden

    @apply_defaults
    def __init__(self, *, scenario_result_id: str | None = None) -> None:
        super().__init__(
            name="Offline browser scan",
            version=self.VERSION,
            technique_class=BrowserTechnique,
            default_dataset_config=DatasetAttackConfiguration(
                seeds=[SeedObjective(value="Reply to this local message.")]
            ),
            objective_scorer=SubStringScorer(substring="Offline browser"),
            scenario_result_id=scenario_result_id,
            uses_default_adversarial_target=False,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        assert isinstance(self._objective_scorer, SubStringScorer)
        attack = PromptSendingAttack(
            objective_target=context.objective_target,
            attack_scoring_config=AttackScoringConfig(objective_scorer=self._objective_scorer),
        )
        return [
            AtomicAttack(
                atomic_attack_name="browser_prompt",
                technique_name="prompt_sending",
                attack_technique=AttackTechnique(attack=attack),
                seed_groups=list(context.seed_groups),
                memory_labels=context.memory_labels,
            )
        ]


class BrowserInitializer(PyRITInitializer):
    """Register offline components and one pre-Operation-ID historical attack."""

    async def initialize_async(self) -> None:
        memory = CentralMemory.get_memory_instance()
        TargetRegistry.get_registry_singleton().instances.register(BrowserTarget(), name="browser_offline")
        ScenarioRegistry.get_registry_singleton().register_class(BrowserScenario, name="browser_offline")
        await asyncio.to_thread(self._store_legacy_attack)
        await memory.add_message_to_memory_async(
            request=MessagePiece(
                role="user",
                original_value="Historical browser evidence.",
                conversation_id="11111111-1111-4111-8111-111111111112",
            ).to_message()
        )

    @staticmethod
    def _store_legacy_attack() -> None:
        memory = CentralMemory.get_memory_instance()
        # Historical attribution cannot be created through the current admission API.
        with memory._get_session() as session:
            session.add(
                AttackResultEntry(
                    entry=AttackResult(
                        attack_result_id="11111111-1111-4111-8111-111111111111",
                        conversation_id="11111111-1111-4111-8111-111111111112",
                        objective="Historical name-only browser attack",
                        operation="browser_proposed",
                    )
                )
            )
            session.commit()
