# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Policy for manual scoring and the explicit handoff from automated execution."""

import uuid
from collections.abc import Sequence
from typing import Any

from pyrit.backend.services.scorer_service import get_scorer_service
from pyrit.models import AttackResult, MessagePiece, MessageScorable, ScoringExpectation
from pyrit.models.component_spec import RegisteredComponentReference
from pyrit.models.results.attack_result import AttackScoringInputs, ManualContinuation
from pyrit.score import Scorer, TrueFalseScorer


class ChatScoringService:
    """Own chat selection policy; keep executable-component lookup in the scorer catalog."""

    @staticmethod
    def execution_read_only(attack: AttackResult) -> bool:
        """
        Determine whether execution changes require a separate manual result.

        Returns:
            True for automated or unidentified historical executions.
        """
        identifier = attack.get_attack_strategy_identifier()
        return identifier is None or identifier.class_module != "pyrit.backend"

    @classmethod
    def require_manual(cls, attack: AttackResult) -> None:
        """Reject execution changes to the automated source."""
        if cls.execution_read_only(attack):
            raise PermissionError(
                "Automated results are read-only. Choose Continue manually to create a separate attack."
            )

    @staticmethod
    def disabled_reason(scorer: Scorer, *, expectation: ScoringExpectation | None = None) -> str | None:
        """
        Check the scorer's declared evidence and expectation contracts.

        Returns:
            An incompatibility reason or None.
        """
        if not isinstance(scorer, TrueFalseScorer) or MessageScorable not in scorer.get_supported_scorable_types():
            return "Chat objective scoring requires a true/false scorer that accepts message evidence."
        try:
            scorer.prepare_expectation(expectation=expectation or ScoringExpectation(objective="Chat objective"))
        except (ValueError, TypeError) as exc:
            return f"This scorer cannot evaluate the supplied chat criteria: {exc}"
        return None

    def resolve(
        self,
        reference: RegisteredComponentReference,
        *,
        inherited: bool = False,
        expectation: ScoringExpectation | None = None,
    ) -> Scorer:
        """
        Resolve a registered selection without constructing a replacement.

        Returns:
            The existing compatible scorer.
        """
        catalog = get_scorer_service()
        scorer = (
            catalog.find_registered_scorer(reference.identifier_hash)
            if inherited
            else catalog.resolve_registered_scorer(reference)
        )
        if scorer is None:
            raise ValueError("The original objective scorer is no longer registered.")
        reason = self.disabled_reason(scorer, expectation=expectation)
        if reason:
            raise ValueError(reason)
        return scorer

    def inputs(self, attack: AttackResult) -> AttackScoringInputs | None:
        """
        Resolve the attack-owned inputs or the registered original scorer.

        Returns:
            The effective inputs; None means no selection was made.
        """
        stored = attack.scoring_inputs
        if stored is not None:
            return stored
        if not self.execution_read_only(attack):
            return None
        identifier = attack.get_attack_strategy_identifier()
        original = identifier.get_child("objective_scorer") if identifier else None
        if original is None and attack.automated_score is not None:
            original = attack.automated_score.scorer_class_identifier
        if original is None:
            return None
        scorer = get_scorer_service().find_registered_scorer(original.hash)
        if not isinstance(scorer, TrueFalseScorer):
            return None
        return AttackScoringInputs(
            scorer=RegisteredComponentReference(registry_name=original.unique_name, identifier_hash=original.hash),
            revision=f"inherited:{original.hash}",
            inherited=True,
            expectation=attack.automated_score.scored_expectation if attack.automated_score else None,
        )

    def objective_settings(
        self,
        *,
        attack: AttackResult,
        objective: str,
        scorer: RegisteredComponentReference | None,
    ) -> dict[str, Any] | None:
        """
        Reset edited criteria, or apply a default to the first manual objective.

        Returns:
            New settings when the objective change affects scoring, otherwise None.
        """
        current = attack.scoring_inputs
        if current is not None and current.expectation is not None and attack.objective != objective:
            return current.model_copy(update={"expectation": None, "revision": str(uuid.uuid4())}).to_metadata()
        if (
            scorer is None
            or attack.objective.strip()
            or not objective.strip()
            or current is not None
            or self.execution_read_only(attack)
        ):
            return None
        self.resolve(scorer)
        return AttackScoringInputs(scorer=scorer, revision=str(uuid.uuid4())).to_metadata()

    def continue_manually(
        self,
        *,
        source: AttackResult,
        destination: AttackResult,
        source_conversation_id: str,
        retained_pieces: Sequence[MessagePiece],
        explicit_selection: bool,
    ) -> None:
        """Record provenance and copy the original scoring inputs, not the original score."""
        if not self.execution_read_only(source):
            return
        response = max(
            (piece for piece in retained_pieces if piece.role == "assistant"),
            key=lambda piece: (piece.sequence, piece.timestamp),
            default=None,
        )
        destination.metadata[ManualContinuation.METADATA_KEY] = ManualContinuation(
            source_attack_result_id=source.attack_result_id,
            source_conversation_id=source_conversation_id,
            source_response_id=str(response.id) if response else None,
        ).model_dump(mode="json")
        if not explicit_selection:
            inputs = self.inputs(source) or AttackScoringInputs()
            destination.metadata.update(
                inputs.model_copy(
                    update={
                        "revision": str(uuid.uuid4()),
                        "expectation": inputs.expectation if destination.objective == source.objective else None,
                    }
                ).to_metadata()
            )


def get_chat_scoring_service() -> ChatScoringService:
    """Return stateless chat policy using the current registry lifetime."""
    return ChatScoringService()
