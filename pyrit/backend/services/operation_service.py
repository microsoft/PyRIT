# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from uuid import UUID, uuid4

from pyrit.backend.models.attacks import ScoreView
from pyrit.backend.models.operations import (
    EvidenceAvailability,
    FindingCreateRequest,
    FindingEvidenceAttachResponse,
    FindingEvidenceCreateRequest,
    FindingEvidenceItem,
    FindingEvidenceListResponse,
    FindingListItem,
    FindingListResponse,
    OperationListItem,
    OperationUpdateRequest,
)
from pyrit.memory import CentralMemory
from pyrit.models import AttackResult, Finding, FindingCreate, FindingEvidence, Operation, OperationCreate
from pyrit.score import ManualScorer


class OperationNotFoundError(LookupError):
    """Raised when a request names an operation that does not exist."""


class FindingNotFoundError(LookupError):
    """Raised when a finding does not belong to the requested operation."""


class FindingEvidenceNotFoundError(LookupError):
    """Raised when an evidence link or its conversation cannot be found."""


class FindingConflictError(ValueError):
    """Raised when a finding ID is reused for different content."""


class DuplicateOperationError(ValueError):
    """Raised when an operation name collides with an existing record."""

    def __init__(self, existing: Operation) -> None:
        """Keep the existing record so callers can point users to it."""
        super().__init__(f"Operation '{existing.name}' already exists.")
        self.existing = existing


class OperationService:
    """Coordinate operations and the findings recorded within them."""

    async def create_async(self, request: OperationCreate) -> Operation:
        """
        Save a new operation unless its normalized name is already taken.

        Returns:
            Operation: The saved operation.

        Raises:
            DuplicateOperationError: If an operation with the same normalized name exists.
        """
        memory = CentralMemory.get_memory_instance()
        operation = Operation(name=request.name, description=request.description)
        saved = await memory.add_operation_async(operation)
        if saved.id != operation.id:
            raise DuplicateOperationError(saved)
        return saved

    async def list_async(self) -> list[OperationListItem]:
        """
        Read every operation in name order with its finding counts by severity.

        Returns:
            list[OperationListItem]: All operations.
        """
        memory = CentralMemory.get_memory_instance()
        operations = await memory.get_operations_async()
        counts = await memory.get_finding_severity_counts_async()
        return [
            OperationListItem(**operation.model_dump(), finding_counts=counts.get(operation.id, {}))
            for operation in operations
        ]

    async def get_async(self, operation_id: UUID) -> Operation:
        """
        Read one operation.

        Returns:
            Operation: The requested operation.

        Raises:
            OperationNotFoundError: If no operation has this ID.
        """
        try:
            return await CentralMemory.get_memory_instance().require_operation_async(operation_id=operation_id)
        except LookupError as exc:
            raise OperationNotFoundError(f"Operation '{operation_id}' not found.") from exc

    async def update_async(self, *, operation_id: UUID, request: OperationUpdateRequest) -> Operation:
        """
        Replace an operation's description.

        Returns:
            Operation: The updated operation.

        Raises:
            OperationNotFoundError: If no operation has this ID.
        """
        try:
            return await CentralMemory.get_memory_instance().update_operation_async(
                operation_id=operation_id, description=request.description
            )
        except LookupError as exc:
            raise OperationNotFoundError(f"Operation '{operation_id}' not found.") from exc

    async def create_finding_async(self, *, operation_id: UUID, request: FindingCreateRequest) -> Finding:
        """
        Save a finding within an existing operation, together with its evidence when a conversation is given.

        Returns:
            Finding: The saved finding, or the one an earlier request with the same ID and content saved.

        Raises:
            FindingConflictError: If the ID already belongs to a different finding.
        """
        await self.get_async(operation_id)
        finding = Finding(
            operation_id=operation_id,
            id=request.id or uuid4(),
            **request.model_dump(include=set(FindingCreate.model_fields)),
        )
        evidence = await self._new_evidence_async(finding_id=finding.id, request=request)
        memory = CentralMemory.get_memory_instance()
        saved = await memory.add_finding_async(finding=finding, evidence=evidence)
        if saved.model_dump(exclude={"created_at"}) != finding.model_dump(exclude={"created_at"}) or (
            evidence is not None
            and await memory.get_finding_evidence_by_source_async(
                finding_id=saved.id, conversation_id=evidence.conversation_id
            )
            is None
        ):
            raise FindingConflictError(f"Finding ID '{finding.id}' is already used by a different finding.")
        return saved

    async def list_findings_async(
        self, *, operation_id: UUID, limit: int, offset: int, title_query: str | None = None
    ) -> FindingListResponse:
        """
        Read one page of an operation's findings and whether there is a next page.

        Returns:
            FindingListResponse: The page and the offset of the next one, if any.
        """
        await self.get_async(operation_id)
        findings = await CentralMemory.get_memory_instance().get_findings_async(
            operation_id=operation_id, limit=limit + 1, offset=offset, title_query=title_query
        )
        has_more = len(findings) > limit
        page = findings[:limit]
        counts = await CentralMemory.get_memory_instance().get_finding_evidence_counts_async(
            finding_ids=[finding.id for finding in page]
        )
        return FindingListResponse(
            items=[FindingListItem(**f.model_dump(), evidence_count=counts.get(f.id, 0)) for f in page],
            has_more=has_more,
            next_offset=offset + limit if has_more else None,
        )

    async def attach_finding_evidence_async(
        self, *, operation_id: UUID, finding_id: UUID, request: FindingEvidenceCreateRequest
    ) -> FindingEvidenceAttachResponse:
        """
        Link a conversation to a finding.

        The attack can belong to any Operation or none; linking never changes its attribution.
        The conversation must be an active, non-empty conversation of that attack.

        Returns:
            FindingEvidenceAttachResponse: The evidence and whether it was newly linked.
        """
        await self.get_async(operation_id)
        await self._require_finding_async(operation_id=operation_id, finding_id=finding_id)
        await self._require_evidence_source_async(
            attack_result_id=request.attack_result_id, conversation_id=request.conversation_id
        )
        evidence = FindingEvidence(
            finding_id=finding_id, conversation_id=request.conversation_id, attack_result_id=request.attack_result_id
        )
        try:
            saved = await CentralMemory.get_memory_instance().add_finding_evidence_async(evidence=evidence)
        except LookupError as exc:
            raise FindingNotFoundError(str(exc)) from exc
        return FindingEvidenceAttachResponse(item=saved, created=saved.id == evidence.id)

    async def list_finding_evidence_async(
        self, *, operation_id: UUID, finding_id: UUID, limit: int, offset: int
    ) -> FindingEvidenceListResponse:
        """
        Read one page of a finding's evidence. Links whose conversation is gone are kept and marked unavailable.

        Returns:
            FindingEvidenceListResponse: The evidence with availability and scanner context.
        """
        await self.get_async(operation_id)
        await self._require_finding_async(operation_id=operation_id, finding_id=finding_id)
        memory = CentralMemory.get_memory_instance()
        evidence = await memory.get_finding_evidence_async(finding_id=finding_id, limit=limit + 1, offset=offset)
        has_more = len(evidence) > limit
        page = evidence[:limit]
        attacks = (
            await memory.get_attack_results_async(attack_result_ids=list({str(item.attack_result_id) for item in page}))
            if page
            else []
        )
        owners = {attack.attack_result_id: attack for attack in attacks}
        linked_ids = {attack.operation_id for attack in attacks if attack.operation_id is not None}
        operation_names = (
            {saved.id: saved.name for saved in await memory.get_operations_async(operation_ids=list(linked_ids))}
            if linked_ids
            else {}
        )
        stats = await memory.get_conversation_stats_async(conversation_ids=[item.conversation_id for item in page])
        items = []
        for item in page:
            owner = owners.get(str(item.attack_result_id))
            if owner is None:
                items.append(
                    FindingEvidenceItem(
                        item=item, availability=EvidenceAvailability.UNAVAILABLE, scenario_result_id=None
                    )
                )
                continue
            available = (
                item.conversation_id in owner.get_active_conversation_ids()
                and item.conversation_id in stats
                and stats[item.conversation_id].message_count > 0
            )
            items.append(
                FindingEvidenceItem(
                    item=item,
                    availability=EvidenceAvailability.AVAILABLE if available else EvidenceAvailability.UNAVAILABLE,
                    scenario_result_id=UUID(owner.attribution_parent_id) if owner.attribution_parent_id else None,
                    source_operation_id=owner.operation_id,
                    source_operation_name=(
                        operation_names.get(owner.operation_id) if owner.operation_id else owner.operation
                    ),
                    objective=owner.objective,
                    is_main_conversation=item.conversation_id == owner.conversation_id,
                    score=(
                        await self._conversation_score_async(attack=owner, conversation_id=item.conversation_id)
                        if available
                        else None
                    ),
                )
            )
        return FindingEvidenceListResponse(
            items=items, has_more=has_more, next_offset=offset + limit if has_more else None
        )

    @staticmethod
    async def _conversation_score_async(*, attack: AttackResult, conversation_id: str) -> ScoreView | None:
        """
        Pick the score that represents one of an attack's conversations, never an auxiliary score.

        The attack's headline score wins when it is in this conversation. Otherwise the latest human score
        wins, then the latest score from the scorer that produced the attack's automated score.

        Returns:
            ScoreView | None: The chosen score, or None when the conversation has no such score.
        """
        scores = await CentralMemory.get_memory_instance().get_prompt_scores_async(conversation_id=conversation_id)
        headline = attack.last_score
        if headline is not None and any(str(score.id) == str(headline.id) for score in scores):
            return ScoreView.from_domain(headline, is_objective_score=True)
        automated = attack.automated_score
        objective_hash = (
            automated.scorer_class_identifier.hash if automated and automated.scorer_class_identifier else None
        )
        scorers = [(score, score.scorer_class_identifier) for score in scores if score.scorer_class_identifier]
        human = [score for score, scorer in scorers if scorer.class_name == ManualScorer.__name__]
        objective = [score for score, scorer in scorers if scorer.hash == objective_hash]
        latest = max(human or objective, key=lambda score: score.timestamp, default=None)
        return ScoreView.from_domain(latest) if latest else None

    async def detach_finding_evidence_async(self, *, operation_id: UUID, finding_id: UUID, evidence_id: UUID) -> None:
        """Unlink evidence, but only if it belongs to the requested finding."""
        await self.get_async(operation_id)
        await self._require_finding_async(operation_id=operation_id, finding_id=finding_id)
        try:
            await CentralMemory.get_memory_instance().delete_finding_evidence_async(
                finding_id=finding_id, evidence_id=evidence_id
            )
        except LookupError as exc:
            raise FindingEvidenceNotFoundError(str(exc)) from exc

    async def _require_finding_async(self, *, operation_id: UUID, finding_id: UUID) -> Finding:
        finding = await CentralMemory.get_memory_instance().get_finding_async(
            operation_id=operation_id, finding_id=finding_id
        )
        if finding is None:
            raise FindingNotFoundError(f"Finding '{finding_id}' not found in operation '{operation_id}'.")
        return finding

    async def _new_evidence_async(self, *, finding_id: UUID, request: FindingCreateRequest) -> FindingEvidence | None:
        """
        Build evidence for a new finding from its conversation, finding the attack when only the conversation is given.

        Returns:
            FindingEvidence | None: The evidence to save with the finding, or None without a conversation.
        """
        if request.conversation_id is None:
            return None
        attack_result_id = request.attack_result_id or await self._find_conversation_attack_async(
            conversation_id=request.conversation_id
        )
        await self._require_evidence_source_async(
            attack_result_id=attack_result_id, conversation_id=request.conversation_id
        )
        return FindingEvidence(
            finding_id=finding_id, conversation_id=request.conversation_id, attack_result_id=attack_result_id
        )

    @staticmethod
    async def _find_conversation_attack_async(*, conversation_id: str) -> UUID:
        """
        Find the attack a conversation belongs to, as its main conversation or as a recorded branch.

        Returns:
            UUID: The owning attack's ID.

        Raises:
            FindingEvidenceNotFoundError: If no saved attack holds the conversation.
        """
        memory = CentralMemory.get_memory_instance()
        attacks = await memory.get_attack_results_async(conversation_id=conversation_id, limit=1)
        if attacks:
            return UUID(attacks[0].attack_result_id)
        conversation = await memory.get_conversation_metadata_async(conversation_id=conversation_id)
        if conversation is None or conversation.attack_result_id is None:
            raise FindingEvidenceNotFoundError("The conversation is not saved as part of an attack.")
        return UUID(conversation.attack_result_id)

    @staticmethod
    async def _require_evidence_source_async(*, attack_result_id: UUID, conversation_id: str) -> None:
        """
        Check that a conversation is an active, non-empty conversation of an existing attack.

        The attack can belong to any Operation or none; linking never changes its attribution.

        Raises:
            FindingEvidenceNotFoundError: If the attack, membership, or messages are missing.
        """
        memory = CentralMemory.get_memory_instance()
        attacks = await memory.get_attack_results_async(attack_result_ids=[str(attack_result_id)])
        if not attacks:
            raise FindingEvidenceNotFoundError("The owning attack no longer exists.")
        if conversation_id not in attacks[0].get_active_conversation_ids():
            raise FindingEvidenceNotFoundError("The conversation is not an active member of this attack.")
        stats = await memory.get_conversation_stats_async(conversation_ids=[conversation_id])
        if conversation_id not in stats or stats[conversation_id].message_count == 0:
            raise FindingEvidenceNotFoundError("The conversation has no saved messages.")

    async def update_finding_async(self, *, operation_id: UUID, finding_id: UUID, request: FindingCreate) -> Finding:
        """
        Replace a finding's editable fields within an existing operation.

        Returns:
            Finding: The updated finding.

        Raises:
            FindingNotFoundError: If the finding is missing or belongs to another operation.
        """
        await self.get_async(operation_id)
        try:
            return await CentralMemory.get_memory_instance().update_finding_async(
                operation_id=operation_id, finding_id=finding_id, request=request
            )
        except LookupError as exc:
            raise FindingNotFoundError(str(exc)) from exc

    async def delete_finding_async(self, *, operation_id: UUID, finding_id: UUID) -> None:
        """
        Permanently remove a finding within an existing operation.

        Raises:
            FindingNotFoundError: If the finding is missing or belongs to another operation.
        """
        await self.get_async(operation_id)
        try:
            await CentralMemory.get_memory_instance().delete_finding_async(
                operation_id=operation_id, finding_id=finding_id
            )
        except LookupError as exc:
            raise FindingNotFoundError(str(exc)) from exc
