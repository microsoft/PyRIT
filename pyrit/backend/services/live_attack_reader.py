# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Read-only port for scan attacks that are still running.

A scan saves each ``AttackResult`` only when its attack finishes. Until then, the attack read APIs
project the live execution through this port. The scenario run service provides the lookup; the
attack service depends only on these types, so it never imports scenario scheduling logic.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pyrit.executor.attack.core.attack_executor import ActiveAttackExecution
from pyrit.memory.memory_interface import AttackConversationPage
from pyrit.models import AttackOutcome, AttackResult, ConversationReference, ConversationType


@dataclass(frozen=True, slots=True)
class LiveAttack:
    """An attack still running in a scan, with at least one objective conversation."""

    scenario_result_id: str
    execution: ActiveAttackExecution

    @classmethod
    def from_execution(cls, *, scenario_result_id: str, execution: ActiveAttackExecution) -> "LiveAttack | None":
        """
        Wrap an execution once it has an objective conversation to read.

        Returns:
            LiveAttack | None: The live attack, or None before its first objective conversation starts.
        """
        if not execution.conversation_ids and execution.current_conversation_id is None:
            return None
        return cls(scenario_result_id=scenario_result_id, execution=execution)

    @property
    def main_conversation_id(self) -> str:
        """The conversation the attack is working on, otherwise its first objective conversation."""
        return self.execution.current_conversation_id or self.execution.conversation_ids[0]

    @property
    def other_conversation_ids(self) -> list[str]:
        """The other objective conversations started so far, in first-use order."""
        main = self.main_conversation_id
        return [conversation_id for conversation_id in self.execution.conversation_ids if conversation_id != main]

    def to_attack_result(self) -> AttackResult:
        """
        Project the unsaved result the attack read APIs show while the attack runs.

        Other objective conversations are user-visible references, like the pruned conversations of a saved result.

        Returns:
            AttackResult: An undetermined, unsaved result owned by the running scan.
        """
        execution = self.execution
        return AttackResult(
            attack_result_id=execution.attack_result_id,
            conversation_id=self.main_conversation_id,
            objective=execution.objective,
            atomic_attack_identifier=execution.atomic_attack_identifier,
            outcome=AttackOutcome.UNDETERMINED,
            timestamp=execution.started_at,
            related_conversations={
                ConversationReference(conversation_id=conversation_id, conversation_type=ConversationType.PRUNED)
                for conversation_id in self.other_conversation_ids
            },
            attribution_parent_id=self.scenario_result_id,
        )

    def conversation_page(
        self, *, offset: int = 0, limit: int = 50, conversation_id: str | None = None
    ) -> AttackConversationPage:
        """
        Page the live conversation references the same way saved references are paged.

        Returns:
            AttackConversationPage: The main conversation first, then the others in first-use order.
        """
        return AttackConversationPage.from_references(
            main_conversation_id=self.main_conversation_id,
            pruned_conversation_ids=self.other_conversation_ids,
            timestamp=self.execution.started_at,
            offset=offset,
            limit=limit,
            conversation_id=conversation_id,
        )


#: Finds a running scan attack by its ``attack_result_id``; None when no active scan is running it.
LiveAttackLookup = Callable[[str], LiveAttack | None]
