# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from collections.abc import Sequence
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
    OperationDetail,
    OperationListItem,
    OperationUpdateRequest,
)
from pyrit.memory import CentralMemory
from pyrit.models import (
    AttackResult,
    AttackResultSelection,
    Finding,
    FindingCreate,
    FindingEvidence,
    Operation,
    OperationCreate,
)

# Keeps every ID list a batch reads within SQL Server's 2100-parameter limit.
_EVIDENCE_BATCH_SIZE = 500


class OperationNotFoundError(LookupError):
    """Raised when a request names an operation that does not exist."""


class FindingNotFoundError(LookupError):
    """Raised when a finding does not belong to the requested operation."""


class FindingEvidenceNotFoundError(LookupError):
    """Raised when an evidence link or its attack result cannot be found."""


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

    async def get_detail_async(self, operation_id: UUID) -> OperationDetail:
        """
        Read one operation with what refers to it by ID.

        Returns:
            OperationDetail: The operation and its usage.
        """
        operation = await self.get_async(operation_id)
        usage = await CentralMemory.get_memory_instance().get_operation_usage_async(operation_id=operation_id)
        return OperationDetail(**operation.model_dump(), usage=usage)

    async def update_async(self, *, operation_id: UUID, request: OperationUpdateRequest) -> Operation:
        """
        Rename an operation or replace its description.

        Returns:
            Operation: The updated operation.

        Raises:
            OperationNotFoundError: If no operation has this ID.
            DuplicateOperationError: If another operation already has the normalized name.
        """
        try:
            saved = await CentralMemory.get_memory_instance().update_operation_async(
                operation_id=operation_id, name=request.name, description=request.description
            )
        except LookupError as exc:
            raise OperationNotFoundError(f"Operation '{operation_id}' not found.") from exc
        if saved.id != operation_id:
            raise DuplicateOperationError(saved)
        return saved

    async def delete_async(self, operation_id: UUID) -> None:
        """
        Permanently remove an operation that no findings, attacks, or scenario runs refer to by ID.

        Raises:
            OperationNotFoundError: If no operation has this ID.
            OperationInUseError: If findings, attacks, or scenario runs refer to the operation by ID.
        """
        try:
            await CentralMemory.get_memory_instance().delete_operation_async(operation_id=operation_id)
        except LookupError as exc:
            raise OperationNotFoundError(f"Operation '{operation_id}' not found.") from exc

    async def create_finding_async(self, *, operation_id: UUID, request: FindingCreateRequest) -> Finding:
        """
        Save a finding within an existing operation, together with its evidence when an attack result is given.

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
        try:
            saved = await memory.add_finding_async(finding=finding, evidence=evidence)
        except LookupError as exc:
            raise OperationNotFoundError(f"Operation '{operation_id}' not found.") from exc
        if saved.model_dump(exclude={"created_at"}) != finding.model_dump(exclude={"created_at"}) or (
            evidence is not None
            and await memory.get_finding_evidence_by_source_async(
                finding_id=saved.id, attack_result_id=evidence.attack_result_id
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
        Link a saved attack result to a finding.

        The attack can belong to any Operation or none; linking never changes its attribution.

        Returns:
            FindingEvidenceAttachResponse: The evidence and whether it was newly linked.
        """
        await self.get_async(operation_id)
        await self._require_finding_async(operation_id=operation_id, finding_id=finding_id)
        await self._require_evidence_source_async(attack_result_id=request.attack_result_id)
        evidence = FindingEvidence(finding_id=finding_id, attack_result_id=request.attack_result_id)
        try:
            saved = await CentralMemory.get_memory_instance().add_finding_evidence_async(evidence=evidence)
        except LookupError as exc:
            raise FindingNotFoundError(str(exc)) from exc
        return FindingEvidenceAttachResponse(item=saved, created=saved.id == evidence.id)

    async def list_finding_evidence_async(
        self, *, operation_id: UUID, finding_id: UUID, limit: int, offset: int
    ) -> FindingEvidenceListResponse:
        """
        Read one page of a finding's evidence. Links whose attack result is gone are kept and marked unavailable.

        Each item shows its attack's current outcome and score, and the conversation holding that score.

        Returns:
            FindingEvidenceListResponse: The evidence with availability and attack context.
        """
        await self.get_async(operation_id)
        await self._require_finding_async(operation_id=operation_id, finding_id=finding_id)
        evidence = await CentralMemory.get_memory_instance().get_finding_evidence_async(
            finding_id=finding_id, limit=limit + 1, offset=offset
        )
        has_more = len(evidence) > limit
        return FindingEvidenceListResponse(
            items=await self._resolve_evidence_async(evidence=evidence[:limit]),
            has_more=has_more,
            next_offset=offset + limit if has_more else None,
        )

    async def get_findings_with_evidence_async(
        self, *, operation_id: UUID, finding_ids: Sequence[UUID]
    ) -> list[tuple[Finding, list[FindingEvidenceItem]]]:
        """
        Read selected findings of an operation, each with all its evidence as the evidence page shows it.

        Returns:
            list[tuple[Finding, list[FindingEvidenceItem]]]: Each finding with its evidence, newest evidence first,
                ordered as ``list_findings_async`` orders findings.

        Raises:
            OperationNotFoundError: If no operation has this ID.
            FindingNotFoundError: If a requested finding is not in the operation.
        """
        await self.get_async(operation_id)
        memory = CentralMemory.get_memory_instance()
        findings = await memory.get_findings_by_ids_async(operation_id=operation_id, finding_ids=finding_ids)
        found = {finding.id for finding in findings}
        missing = next((finding_id for finding_id in finding_ids if finding_id not in found), None)
        if missing is not None:
            raise FindingNotFoundError(f"Finding '{missing}' not found in operation '{operation_id}'.")
        evidence = await memory.get_finding_evidence_for_findings_async(
            finding_ids=[finding.id for finding in findings]
        )
        resolved = await self._resolve_evidence_async(
            evidence=[item for finding in findings for item in evidence.get(finding.id, [])]
        )
        by_id = {item.item.id: item for item in resolved}
        return [(finding, [by_id[item.id] for item in evidence.get(finding.id, [])]) for finding in findings]

    async def _resolve_evidence_async(self, *, evidence: Sequence[FindingEvidence]) -> list[FindingEvidenceItem]:
        """
        Show each evidence link with its attack's current outcome, score, and conversation.

        Returns:
            list[FindingEvidenceItem]: The evidence in input order.
        """
        items: list[FindingEvidenceItem] = []
        for start in range(0, len(evidence), _EVIDENCE_BATCH_SIZE):
            batch = evidence[start : start + _EVIDENCE_BATCH_SIZE]
            items.extend(await self._resolve_evidence_batch_async(evidence=batch))
        return items

    async def _resolve_evidence_batch_async(self, *, evidence: Sequence[FindingEvidence]) -> list[FindingEvidenceItem]:
        memory = CentralMemory.get_memory_instance()
        attacks = await memory.get_attack_results_async(
            attack_result_ids=list({str(item.attack_result_id) for item in evidence}),
            result_selection=AttackResultSelection.ALL_RESULTS,
        )
        owners = {attack.attack_result_id: attack for attack in attacks}
        linked_ids = {attack.operation_id for attack in attacks if attack.operation_id is not None}
        operation_names = (
            {saved.id: saved.name for saved in await memory.get_operations_async(operation_ids=list(linked_ids))}
            if linked_ids
            else {}
        )
        conversations = await self._score_conversations_async(attacks=attacks)
        stats = await memory.get_conversation_stats_async(conversation_ids=list(set(conversations.values())))
        items = []
        for item in evidence:
            owner = owners.get(str(item.attack_result_id))
            if owner is None:
                items.append(
                    FindingEvidenceItem(
                        item=item, availability=EvidenceAvailability.UNAVAILABLE, scenario_result_id=None
                    )
                )
                continue
            conversation_id = conversations[owner.attack_result_id]
            available = conversation_id in stats and stats[conversation_id].message_count > 0
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
                    outcome=owner.outcome,
                    score=ScoreView.from_domain(owner.last_score, is_objective_score=True)
                    if owner.last_score
                    else None,
                    conversation_id=conversation_id,
                )
            )
        return items

    @staticmethod
    async def _score_conversations_async(*, attacks: Sequence[AttackResult]) -> dict[str, str]:
        """
        Find the conversation holding each attack's score, so the score shown and the transcript opened agree.

        Returns:
            dict[str, str]: By attack result ID, the active conversation holding its score, otherwise its main one.
        """
        piece_ids = [
            str(attack.last_score.message_piece_id)
            for attack in attacks
            if attack.last_score is not None and attack.last_score.message_piece_id is not None
        ]
        pieces = (
            await CentralMemory.get_memory_instance().get_message_pieces_async(prompt_ids=piece_ids)
            if piece_ids
            else []
        )
        scored_in: dict[str, str] = {str(piece.id): str(piece.conversation_id) for piece in pieces}
        conversations: dict[str, str] = {}
        for attack in attacks:
            score = attack.last_score
            scored = scored_in.get(str(score.message_piece_id)) if score is not None else None
            active = attack.get_active_conversation_ids()
            conversations[attack.attack_result_id] = (
                scored if scored is not None and scored in active else attack.conversation_id
            )
        return conversations

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
        Build the evidence to save with a new finding.

        Returns:
            FindingEvidence | None: The evidence, or None when the request names no attack result.
        """
        if request.attack_result_id is None:
            return None
        await self._require_evidence_source_async(attack_result_id=request.attack_result_id)
        return FindingEvidence(finding_id=finding_id, attack_result_id=request.attack_result_id)

    @staticmethod
    async def _require_evidence_source_async(*, attack_result_id: UUID) -> None:
        """
        Check that a saved attack result exists and has saved messages.

        The attack can belong to any Operation or none; linking never changes its attribution.

        Raises:
            FindingEvidenceNotFoundError: If the attack result or its messages are missing.
        """
        memory = CentralMemory.get_memory_instance()
        attacks = await memory.get_attack_results_async(attack_result_ids=[str(attack_result_id)])
        if not attacks:
            raise FindingEvidenceNotFoundError("The attack result does not exist.")
        stats = await memory.get_conversation_stats_async(
            conversation_ids=list(attacks[0].get_active_conversation_ids())
        )
        if not any(stat.message_count > 0 for stat in stats.values()):
            raise FindingEvidenceNotFoundError("The attack result has no saved messages.")

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
