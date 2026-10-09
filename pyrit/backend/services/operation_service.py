# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from uuid import UUID

from pyrit.backend.models.operations import (
    EvidenceAvailability,
    FindingEvidenceAttachResponse,
    FindingEvidenceCreateRequest,
    FindingEvidenceItem,
    FindingEvidenceListResponse,
    FindingListItem,
    FindingListResponse,
)
from pyrit.memory import CentralMemory
from pyrit.models import Finding, FindingCreate, FindingEvidence, Operation, OperationCreate


class OperationNotFoundError(LookupError):
    """Raised when a request names an operation that does not exist."""


class FindingNotFoundError(LookupError):
    """Raised when a finding does not belong to the requested operation."""


class FindingEvidenceNotFoundError(LookupError):
    """Raised when the requested association or source cannot be viewed."""


class FindingEvidenceAttributionError(ValueError):
    """Raised when the source lacks the finding's exact Operation attribution."""


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
        operation = Operation(name=request.name)
        saved = await memory.add_operation_async(operation)
        if saved.id != operation.id:
            raise DuplicateOperationError(saved)
        return saved

    async def list_async(self) -> list[Operation]:
        """
        Read every operation in name order.

        Returns:
            list[Operation]: All operations.
        """
        return await CentralMemory.get_memory_instance().get_operations_async()

    async def get_async(self, operation_id: UUID) -> Operation:
        """
        Read one operation.

        Returns:
            Operation: The requested operation.

        Raises:
            OperationNotFoundError: If no operation has this ID.
        """
        operations = await CentralMemory.get_memory_instance().get_operations_async(operation_id=operation_id)
        if not operations:
            raise OperationNotFoundError(f"Operation '{operation_id}' not found.")
        return operations[0]

    async def create_finding_async(self, *, operation_id: UUID, request: FindingCreate) -> Finding:
        """
        Save a finding within an existing operation.

        Returns:
            Finding: The saved finding.
        """
        await self.get_async(operation_id)
        finding = Finding(operation_id=operation_id, **request.model_dump())
        return await CentralMemory.get_memory_instance().add_finding_async(finding)

    async def list_findings_async(
        self, *, operation_id: UUID, limit: int, offset: int, title_query: str | None = None
    ) -> FindingListResponse:
        """
        Read one page of an operation's findings and detect whether another is available.

        Returns:
            FindingListResponse: The page and continuation offset.
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
        Validate the saved owner and attach its active, viewer-addressable conversation.

        Returns:
            FindingEvidenceAttachResponse: The stable association and creation indicator.
        """
        operation = await self.get_async(operation_id)
        await self._require_finding_async(operation_id=operation_id, finding_id=finding_id)
        memory = CentralMemory.get_memory_instance()
        attacks = await memory.get_attack_results_async(attack_result_ids=[str(request.attack_result_id)])
        if not attacks:
            raise FindingEvidenceNotFoundError("The owning attack no longer exists.")
        attack = attacks[0]
        if request.conversation_id not in attack.get_active_conversation_ids():
            raise FindingEvidenceNotFoundError("The conversation is not an active member of this attack.")
        stats = await memory.get_conversation_stats_async(conversation_ids=[request.conversation_id])
        if request.conversation_id not in stats or stats[request.conversation_id].message_count == 0:
            raise FindingEvidenceNotFoundError("The conversation has no saved messages.")
        if attack.operation != operation.name:
            raise FindingEvidenceAttributionError(
                "The owning attack must have exactly the finding's saved Operation name; "
                "missing, differently cased, or whitespace-variant attribution is not eligible."
            )
        evidence = FindingEvidence(
            finding_id=finding_id, conversation_id=request.conversation_id, attack_result_id=request.attack_result_id
        )
        try:
            saved = await memory.add_finding_evidence_async(evidence=evidence)
        except LookupError as exc:
            raise FindingNotFoundError(str(exc)) from exc
        return FindingEvidenceAttachResponse(item=saved, created=saved.id == evidence.id)

    async def list_finding_evidence_async(
        self, *, operation_id: UUID, finding_id: UUID, limit: int, offset: int
    ) -> FindingEvidenceListResponse:
        """
        Read a bounded page, retaining missing sources and propagating storage failures.

        Returns:
            FindingEvidenceListResponse: Associations with current viewer context.
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
        stats = await memory.get_conversation_stats_async(conversation_ids=[item.conversation_id for item in page])
        items = []
        for item in page:
            owner = owners.get(str(item.attack_result_id))
            available = (
                owner is not None
                and item.conversation_id in owner.get_active_conversation_ids()
                and item.conversation_id in stats
                and stats[item.conversation_id].message_count > 0
            )
            items.append(
                FindingEvidenceItem(
                    item=item,
                    availability=EvidenceAvailability.AVAILABLE if available else EvidenceAvailability.UNAVAILABLE,
                    scenario_result_id=UUID(owner.attribution_parent_id)
                    if owner and owner.attribution_parent_id
                    else None,
                )
            )
        return FindingEvidenceListResponse(
            items=items, has_more=has_more, next_offset=offset + limit if has_more else None
        )

    async def detach_finding_evidence_async(self, *, operation_id: UUID, finding_id: UUID, evidence_id: UUID) -> None:
        """Remove only the association owned by the requested finding."""
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
