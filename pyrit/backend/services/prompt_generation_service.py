# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Run prompt generations as background jobs and save their results as datasets.

Generators own model interaction and resumable progress, and memory stores the generation records.
This service owns the job lifecycle: admission, a concurrency limit, persistence of each checkpoint,
review pauses, cancellation, retry from the last checkpoint, and reconciliation after a restart.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from pyrit.backend.models.prompt_generations import (
    PromptGenerationBatchCreateRequest,
    PromptGenerationBatchItem,
    PromptGenerationCreateRequest,
    PromptGenerationPreflightResponse,
    PromptGenerationRequestFromDataset,
    PromptGenerationReview,
    PromptGenerationReviewUpdate,
    PromptGenerationSummary,
    PromptGeneratorDescriptor,
)
from pyrit.backend.services.dataset_service import parse_dataset_selection_key
from pyrit.backend.services.prompt_generation_adapters import (
    PromptGenerationAdapter,
    create_prompt_generation_adapters,
)
from pyrit.backend.services.target_service import get_target_service
from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.memory.dataset_lifecycle import DatasetNameConflictError
from pyrit.models import (
    ACTIVE_GENERATION_STATUSES,
    RETRYABLE_GENERATION_STATUSES,
    PromptGenerationProgress,
    PromptGenerationRecord,
    PromptGenerationStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping

    from pyrit.memory import MemoryInterface
    from pyrit.models import PromptGenerationEstimate, SeedRecord
    from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

_INTERRUPTED_MESSAGE = "The backend stopped before this generation finished. Retry to resume from the last checkpoint."
_CANCELLABLE_STATUSES = frozenset(
    {PromptGenerationStatus.QUEUED, PromptGenerationStatus.RUNNING, PromptGenerationStatus.AWAITING_REVIEW}
)


class PromptGenerationNotFoundError(LookupError):
    """No generation or generated dataset matches the request."""


class PromptGenerationConflictError(Exception):
    """The generation's state doesn't allow the request, or a dataset name is taken."""


class _OwnershipLostError(Exception):
    """Another task or backend cancelled or requeued the generation, so this run attempt stops."""


class PromptGenerationService:
    """
    Own the lifecycle of prompt generation jobs.

    Each change to a record is a compare-and-set on the statuses it comes from. A run takes a fresh
    ``execution_id`` when it starts, and its checkpoint, review, completion, and failure writes require
    it, so a run that another backend cancelled or retried stops at its next write instead of
    overwriting the record. A run stays running until the transaction that saves its dataset completes it.
    """

    MAX_CONCURRENT_GENERATIONS = 1
    DEFAULT_ADDED_BY = "PromptGenerationService"

    def __init__(
        self,
        *,
        memory: MemoryInterface | None = None,
        adapters: Mapping[str, PromptGenerationAdapter] | None = None,
        target_lookup: Callable[[str], PromptTarget | None] | None = None,
    ) -> None:
        """
        Configure memory, generators, and target lookup.

        Args:
            memory (MemoryInterface | None): Memory that stores generations and receives datasets. Defaults to
                the central memory.
            adapters (Mapping[str, PromptGenerationAdapter] | None): Generators by name. Defaults to every
                built-in generator.
            target_lookup (Callable[[str], PromptTarget | None] | None): Resolves a target registry name.
                Defaults to the target registry.
        """
        self._memory = memory or CentralMemory.get_memory_instance()
        self._adapters = dict(adapters) if adapters is not None else create_prompt_generation_adapters()
        self._target_lookup = target_lookup or _lookup_registered_target
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_GENERATIONS)
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._executions: dict[uuid.UUID, uuid.UUID] = {}
        self._cancel_requested: set[uuid.UUID] = set()
        self._saving: set[uuid.UUID] = set()
        self._admission_open = True
        self._reconciled = False

    def list_generators(self) -> list[PromptGeneratorDescriptor]:
        """
        Describe the registered generators.

        Returns:
            list[PromptGeneratorDescriptor]: Generators in registration order.
        """
        return [adapter.descriptor for adapter in self._adapters.values()]

    def has_active_work(self) -> bool:
        """
        Report whether a generation is queued, running, or saving in this process.

        Returns:
            bool: True while any generation task exists.
        """
        return bool(self._tasks)

    def stop_admission(self) -> None:
        """Reject new and resumed work."""
        self._admission_open = False

    async def shutdown_async(self) -> None:
        """
        Stop admission, interrupt generations that are still running, and let dataset saves finish.

        Interrupted generations can be retried after a restart. A save is not cancelled, so its record
        reaches a final state before memory is released.
        """
        self.stop_admission()
        tasks = list(self._tasks.items())
        for generation_id, task in tasks:
            if generation_id not in self._saving:
                task.cancel()
        if tasks:
            await asyncio.wait([task for _, task in tasks])

    async def reconcile_interrupted_async(self) -> int:
        """
        Mark generations that were active when the backend stopped as interrupted.

        The service runs this once on first use. Only file-backed SQLite memory is reconciled: one backend
        owns that database file, so an active record without a task here belonged to a stopped process.
        Shared and unknown memory backends are left alone because another process may own the runs.

        Returns:
            int: The number of reconciled generations.
        """
        self._reconciled = True
        if not isinstance(self._memory, SQLiteMemory):
            logger.info(
                "Skipping interrupted prompt generation reconciliation for shared or unsupported %s memory.",
                type(self._memory).__name__,
            )
            return 0
        reconciled = 0
        for record in await self._memory.get_prompt_generations_async():
            if record.status not in ACTIVE_GENERATION_STATUSES or record.id in self._tasks:
                continue
            interrupted = await self._try_update_async(
                record=record,
                expected_statuses={record.status},
                status=PromptGenerationStatus.INTERRUPTED,
                error=_INTERRUPTED_MESSAGE,
                execution_id=None,
            )
            if interrupted is not None:
                reconciled += 1
        return reconciled

    async def preflight_async(self, request: PromptGenerationCreateRequest) -> PromptGenerationPreflightResponse:
        """
        Check a request and estimate its work without saving it or calling a target.

        Args:
            request (PromptGenerationCreateRequest): The request to check. Missing targets are reported as errors.

        Returns:
            PromptGenerationPreflightResponse: Validation errors, name availability, and an estimate.
        """
        errors: list[str] = []
        estimate: PromptGenerationEstimate | None = None
        adapter = self._adapters.get(request.generator)
        available = await self._memory.is_dataset_name_available_async(dataset_name=request.dataset_name)
        if not available:
            errors.append(_name_taken_message(request.dataset_name))
        if adapter is None:
            errors.append(f"Unknown generator '{request.generator}'.")
            return PromptGenerationPreflightResponse(valid=False, errors=errors, dataset_name_available=available)
        try:
            _require_review_support(adapter=adapter, pause_for_review=request.pause_for_review)
            normalized = adapter.normalize_request(request.request)
            estimate = adapter.estimate(request=normalized, checkpoint=None)
            self._validate_targets(adapter=adapter, request=normalized, checkpoint=None, targets=request.targets)
        except ValueError as error:
            errors.append(_describe_error(error))
        return PromptGenerationPreflightResponse(
            valid=not errors, errors=errors, dataset_name_available=available, estimate=estimate
        )

    async def create_async(
        self, *, request: PromptGenerationCreateRequest, created_by: str | None = None
    ) -> PromptGenerationRecord:
        """
        Queue a generation.

        Args:
            request (PromptGenerationCreateRequest): The generation to create.
            created_by (str | None): The signed-in user, if any.

        Returns:
            PromptGenerationRecord: The queued generation.

        Raises:
            ValueError: If the request or its targets are invalid.
            PromptGenerationConflictError: If the dataset name is taken.
        """
        batch = PromptGenerationBatchCreateRequest(
            generator=request.generator,
            label=request.dataset_name,
            items=[PromptGenerationBatchItem(dataset_name=request.dataset_name, request=request.request)],
            targets=request.targets,
            pause_for_review=request.pause_for_review,
        )
        records = await self._create_records_async(request=batch, created_by=created_by, group_id=None)
        return records[0]

    async def create_batch_async(
        self, *, request: PromptGenerationBatchCreateRequest, created_by: str | None = None
    ) -> list[PromptGenerationRecord]:
        """
        Queue generations that share a generator, targets, and settings, such as an ASSERT suite.

        Args:
            request (PromptGenerationBatchCreateRequest): The generations to create.
            created_by (str | None): The signed-in user, if any.

        Returns:
            list[PromptGenerationRecord]: The queued generations, sharing a ``group_id``.
        """
        return await self._create_records_async(request=request, created_by=created_by, group_id=uuid.uuid4())

    async def list_async(self) -> list[PromptGenerationRecord]:
        """
        List generations, newest first.

        Returns:
            list[PromptGenerationRecord]: The generations.
        """
        await self._reconcile_once_async()
        return await self._memory.get_prompt_generations_async()

    async def get_async(self, generation_id: str) -> PromptGenerationRecord:
        """
        Read one generation.

        Args:
            generation_id (str): The generation ID.

        Returns:
            PromptGenerationRecord: The generation.

        Raises:
            PromptGenerationNotFoundError: If it doesn't exist or the ID isn't a UUID.
        """
        await self._reconcile_once_async()
        return await self._read_async(_parse_generation_id(generation_id))

    async def continue_async(self, generation_id: str) -> PromptGenerationRecord:
        """
        Finish the review and queue the remaining stages.

        Args:
            generation_id (str): The generation ID.

        Returns:
            PromptGenerationRecord: The queued generation.
        """
        return await self._queue_async(
            generation_id=generation_id, allowed={PromptGenerationStatus.AWAITING_REVIEW}, review_completed=True
        )

    async def retry_async(self, generation_id: str) -> PromptGenerationRecord:
        """
        Queue a failed, cancelled, or interrupted generation to resume from its last checkpoint.

        Args:
            generation_id (str): The generation ID.

        Returns:
            PromptGenerationRecord: The queued generation.
        """
        return await self._queue_async(
            generation_id=generation_id, allowed=set(RETRYABLE_GENERATION_STATUSES), error=None
        )

    async def cancel_async(self, generation_id: str) -> PromptGenerationRecord:
        """
        Cancel a queued, running, or paused generation. Its checkpoint is kept for a retry.

        A generation running in another backend, or left running by a backend that stopped, can also be
        cancelled. Its run stops at its next checkpoint, and a dataset save it is running saves nothing,
        because the save requires the run to still own the generation.

        Args:
            generation_id (str): The generation ID.

        Returns:
            PromptGenerationRecord: The cancelled generation.

        Raises:
            PromptGenerationConflictError: If this backend is saving the generation's dataset, or the
                generation already finished.
        """
        record = await self.get_async(generation_id)
        if record.id in self._saving:
            raise PromptGenerationConflictError("The generation is saving its dataset, so it can't be cancelled.")
        if record.status not in _CANCELLABLE_STATUSES:
            raise PromptGenerationConflictError(f"A {record.status.value} generation can't be cancelled.")
        task = self._tasks.get(record.id)
        if task is not None:
            self._cancel_requested.add(record.id)
            task.cancel()
            await asyncio.wait({task})
            self._cancel_requested.discard(record.id)
            record = await self._read_async(record.id)
            # The task's handler records the cancel, unless the task never started or the handler's write
            # failed, for example because memory failed. Then the record is still active and is cancelled below.
            if record.status not in _CANCELLABLE_STATUSES:
                return record
        cancelled = await self._try_update_async(
            record=record,
            expected_statuses=_CANCELLABLE_STATUSES,
            status=PromptGenerationStatus.CANCELLED,
            execution_id=None,
        )
        if cancelled is None:
            current = await self._read_async(record.id)
            raise PromptGenerationConflictError(f"A {current.status.value} generation can't be cancelled.")
        return cancelled

    async def delete_async(self, generation_id: str) -> None:
        """
        Delete a generation record. Its dataset, if saved, is kept.

        Args:
            generation_id (str): The generation ID.

        Raises:
            PromptGenerationConflictError: If the generation is active.
        """
        record = await self.get_async(generation_id)
        if record.status in ACTIVE_GENERATION_STATUSES:
            raise PromptGenerationConflictError("Cancel the generation before deleting it.")
        inactive = set(PromptGenerationStatus) - ACTIVE_GENERATION_STATUSES
        if not await self._memory.delete_prompt_generation_async(generation_id=record.id, expected_statuses=inactive):
            # Raises not found when another request deleted it first; otherwise it became active again.
            await self._read_async(record.id)
            raise PromptGenerationConflictError("Cancel the generation before deleting it.")

    async def get_review_async(self, generation_id: str) -> PromptGenerationReview:
        """
        Read output awaiting review.

        Args:
            generation_id (str): The generation ID.

        Returns:
            PromptGenerationReview: The editable output.

        Raises:
            PromptGenerationConflictError: If the generation isn't awaiting review.
        """
        record = await self.get_async(generation_id)
        if record.status != PromptGenerationStatus.AWAITING_REVIEW or record.checkpoint is None:
            raise PromptGenerationConflictError("The generation isn't awaiting review.")
        stage, payload = self._get_adapter(record.generator).get_review(checkpoint=record.checkpoint)
        return PromptGenerationReview(generation_id=str(record.id), stage=stage, payload=payload)

    async def save_review_async(
        self, *, generation_id: str, update: PromptGenerationReviewUpdate
    ) -> PromptGenerationReview:
        """
        Save edited review output.

        Args:
            generation_id (str): The generation ID.
            update (PromptGenerationReviewUpdate): The edits.

        Returns:
            PromptGenerationReview: The saved review.

        Raises:
            PromptGenerationConflictError: If the generation isn't awaiting review.
        """
        record = await self.get_async(generation_id)
        if record.status != PromptGenerationStatus.AWAITING_REVIEW or record.checkpoint is None:
            raise PromptGenerationConflictError("The generation isn't awaiting review.")
        adapter = self._get_adapter(record.generator)
        checkpoint = adapter.apply_review(checkpoint=record.checkpoint, payload=update.payload)
        saved = await self._try_update_async(
            record=record,
            expected_statuses={PromptGenerationStatus.AWAITING_REVIEW},
            checkpoint=checkpoint,
            progress=adapter.get_progress(request=record.request, checkpoint=checkpoint),
        )
        if saved is None:
            raise PromptGenerationConflictError("The generation isn't awaiting review.")
        return await self.get_review_async(generation_id)

    async def get_estimate_async(self, generation_id: str) -> PromptGenerationEstimate:
        """
        Estimate a generation's remaining work from its checkpoint.

        Args:
            generation_id (str): The generation ID.

        Returns:
            PromptGenerationEstimate: Remaining calls and an approximate token range.
        """
        record = await self.get_async(generation_id)
        return self._get_adapter(record.generator).estimate(request=record.request, checkpoint=record.checkpoint)

    async def request_from_dataset_async(self, selection_key: str) -> PromptGenerationRequestFromDataset:
        """
        Reconstruct the request that generated a stored dataset from its newest objective example.

        Args:
            selection_key (str): A dataset ``selection_key`` from ``GET /datasets``.

        Returns:
            PromptGenerationRequestFromDataset: The generator and its request.

        Raises:
            ValueError: If the selection key doesn't name a dataset.
            PromptGenerationNotFoundError: If no generator recognizes the dataset.
        """
        dataset_name = parse_dataset_selection_key(selection_key)
        if dataset_name is None:
            raise ValueError("Choose a named dataset; unnamed seeds don't record a generation request.")
        seeds = await self._read_sample_seeds_async(dataset_name)
        for name, adapter in self._adapters.items():
            request = adapter.request_from_seeds(seeds)
            if request is not None:
                return PromptGenerationRequestFromDataset(
                    generator=name, request=request, source_dataset_name=dataset_name
                )
        raise PromptGenerationNotFoundError(f"Dataset '{dataset_name}' doesn't record a generation request.")

    async def _reconcile_once_async(self) -> None:
        if not self._reconciled:
            await self.reconcile_interrupted_async()

    async def _create_records_async(
        self, *, request: PromptGenerationBatchCreateRequest, created_by: str | None, group_id: uuid.UUID | None
    ) -> list[PromptGenerationRecord]:
        self._require_admission()
        await self._reconcile_once_async()
        adapter = self._get_adapter(request.generator)
        _require_review_support(adapter=adapter, pause_for_review=request.pause_for_review)
        normalized = [adapter.normalize_request(item.request) for item in request.items]
        for item_request in normalized:
            self._validate_targets(adapter=adapter, request=item_request, checkpoint=None, targets=request.targets)
        names = [item.dataset_name for item in request.items]
        if len(set(names)) != len(names):
            raise ValueError("Each generation in a batch needs a different dataset name.")
        now = datetime.now(UTC)
        records = [
            PromptGenerationRecord(
                id=uuid.uuid4(),
                generator=request.generator,
                dataset_name=item.dataset_name,
                status=PromptGenerationStatus.QUEUED,
                request=item_request,
                targets=dict(request.targets),
                pause_for_review=request.pause_for_review,
                group_id=group_id,
                group_label=request.label if group_id is not None else None,
                group_index=index,
                progress=adapter.get_progress(request=item_request, checkpoint=None),
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            for index, (item, item_request) in enumerate(zip(request.items, normalized, strict=True))
        ]
        try:
            await self._memory.add_prompt_generations_async(generations=records)
        except DatasetNameConflictError as error:
            raise PromptGenerationConflictError(str(error)) from error
        for record in records:
            self._schedule(record.id)
        return records

    async def _queue_async(
        self, *, generation_id: str, allowed: Collection[PromptGenerationStatus], **changes: Any
    ) -> PromptGenerationRecord:
        self._require_admission()
        record = await self.get_async(generation_id)
        if record.status not in allowed:
            raise PromptGenerationConflictError(f"A {record.status.value} generation can't be queued.")
        adapter = self._get_adapter(record.generator)
        self._validate_targets(
            adapter=adapter, request=record.request, checkpoint=record.checkpoint, targets=record.targets
        )
        try:
            queued = await self._try_update_async(
                record=record,
                expected_statuses=allowed,
                reserve_dataset_name=True,
                status=PromptGenerationStatus.QUEUED,
                execution_id=None,
                **changes,
            )
        except DatasetNameConflictError as error:
            # A cancel releases the name, so another dataset or generation may have taken it since.
            raise PromptGenerationConflictError(
                f"{error} This generation can't resume under that name. A new generation under another "
                "name starts over and repeats the model calls."
            ) from error
        if queued is None:
            current = await self._read_async(record.id)
            raise PromptGenerationConflictError(f"A {current.status.value} generation can't be queued.")
        self._schedule(record.id)
        return queued

    def _schedule(self, generation_id: uuid.UUID) -> None:
        task = asyncio.create_task(self._run_async(generation_id), name=f"prompt-generation-{generation_id}")
        self._tasks[generation_id] = task

        def _forget(finished: asyncio.Task[None]) -> None:
            if self._tasks.get(generation_id) is finished:
                del self._tasks[generation_id]

        task.add_done_callback(_forget)

    async def _run_async(self, generation_id: uuid.UUID) -> None:
        try:
            async with self._semaphore:
                await self._execute_async(generation_id)
        except asyncio.CancelledError:
            cancelled = generation_id in self._cancel_requested
            await asyncio.shield(
                self._finish_async(
                    generation_id=generation_id,
                    execution_id=self._executions.get(generation_id),
                    status=PromptGenerationStatus.CANCELLED if cancelled else PromptGenerationStatus.INTERRUPTED,
                    error=None if cancelled else _INTERRUPTED_MESSAGE,
                )
            )
            raise
        except _OwnershipLostError:
            logger.info("Prompt generation %s was cancelled or requeued elsewhere; this run stopped.", generation_id)
        except Exception as error:
            logger.exception("Prompt generation %s failed.", generation_id)
            await self._finish_async(
                generation_id=generation_id,
                execution_id=self._executions.get(generation_id),
                status=PromptGenerationStatus.FAILED,
                error=_describe_error(error),
            )
        finally:
            self._cancel_requested.discard(generation_id)
            self._executions.pop(generation_id, None)

    async def _execute_async(self, generation_id: uuid.UUID) -> None:
        record = await self._read_async(generation_id)
        if record.status != PromptGenerationStatus.QUEUED:
            return
        adapter = self._get_adapter(record.generator)
        targets = self._resolve_targets(
            adapter=adapter, request=record.request, checkpoint=record.checkpoint, targets=record.targets
        )
        execution_id = uuid.uuid4()
        running = await self._try_update_async(
            record=record,
            expected_statuses={PromptGenerationStatus.QUEUED},
            status=PromptGenerationStatus.RUNNING,
            execution_id=execution_id,
            started_at=record.started_at or datetime.now(UTC),
        )
        if running is None:
            raise _OwnershipLostError
        self._executions[generation_id] = execution_id
        current = running

        async def save_checkpoint_async(checkpoint: dict[str, Any], *, progress: PromptGenerationProgress) -> None:
            nonlocal current
            current = await self._write_owned_async(record=current, checkpoint=checkpoint, progress=progress)

        outcome = await adapter.run_async(
            request=record.request,
            targets=targets,
            checkpoint=record.checkpoint,
            pause_for_review=record.pause_for_review and not record.review_completed,
            on_checkpoint=save_checkpoint_async,
        )
        if outcome.awaiting_review:
            await self._write_owned_async(
                record=current,
                status=PromptGenerationStatus.AWAITING_REVIEW,
                checkpoint=outcome.checkpoint,
                progress=outcome.progress,
                execution_id=None,
            )
            return
        # Cancel and shutdown leave a dataset save here alone, so it finishes or fails on its own. The record
        # stays running until the commit completes it in the same transaction that saves the dataset.
        self._saving.add(generation_id)
        try:
            dataset, summary = await adapter.build_dataset_async(
                request=record.request, targets=targets, checkpoint=outcome.checkpoint, dataset_name=record.dataset_name
            )
            completed = _apply(
                current,
                status=PromptGenerationStatus.COMPLETED,
                checkpoint=outcome.checkpoint,
                progress=outcome.progress,
                summary=summary,
                completed_at=datetime.now(UTC),
                execution_id=None,
            )
            try:
                saved = await self._memory.commit_prompt_generation_dataset_async(
                    generation=completed,
                    expected_execution_id=execution_id,
                    dataset=dataset,
                    added_by=record.created_by or self.DEFAULT_ADDED_BY,
                )
            except DatasetNameConflictError:
                raise PromptGenerationConflictError(
                    f"A dataset named '{record.dataset_name}' was created while generating, so this generation "
                    "can't save under that name. A new generation under another name starts over and repeats "
                    "the model calls."
                ) from None
            if not saved:
                raise _OwnershipLostError
        finally:
            self._saving.discard(generation_id)

    async def _write_owned_async(self, *, record: PromptGenerationRecord, **changes: Any) -> PromptGenerationRecord:
        """
        Write a running generation's change only while this run attempt still owns it.

        Returns:
            PromptGenerationRecord: The written generation.

        Raises:
            _OwnershipLostError: If another task or backend cancelled or requeued the generation.
        """
        updated = await self._try_update_async(
            record=record,
            expected_statuses={PromptGenerationStatus.RUNNING},
            expected_execution_id=record.execution_id,
            **changes,
        )
        if updated is None:
            raise _OwnershipLostError
        return updated

    async def _finish_async(
        self,
        *,
        generation_id: uuid.UUID,
        execution_id: uuid.UUID | None,
        status: PromptGenerationStatus,
        error: str | None,
    ) -> None:
        """Record how a run ended, unless another task or backend moved the generation on first."""
        record = await self._memory.get_prompt_generation_async(generation_id=generation_id)
        # A run that never started only owns a queued generation; one that started owns it by execution ID.
        expected = {PromptGenerationStatus.QUEUED} if execution_id is None else {PromptGenerationStatus.RUNNING}
        finished = record is not None and (
            await self._try_update_async(
                record=record,
                expected_statuses=expected,
                expected_execution_id=execution_id,
                status=status,
                error=error,
                execution_id=None,
            )
            is not None
        )
        if not finished:
            logger.info("Prompt generation %s moved on before this run recorded %s.", generation_id, status.value)

    async def _try_update_async(
        self,
        *,
        record: PromptGenerationRecord,
        expected_statuses: Collection[PromptGenerationStatus],
        expected_execution_id: uuid.UUID | None = None,
        reserve_dataset_name: bool = False,
        **changes: Any,
    ) -> PromptGenerationRecord | None:
        updated = _apply(record, **changes)
        # Write only the changed fields, so a transition built from an older read can't revert a
        # concurrent write, such as a saved review or another backend's checkpoint.
        written = await self._memory.try_update_prompt_generation_async(
            generation=updated,
            expected_statuses=expected_statuses,
            expected_execution_id=expected_execution_id,
            reserve_dataset_name=reserve_dataset_name,
            fields=[*changes, "updated_at"],
        )
        return updated if written else None

    async def _read_async(self, generation_id: uuid.UUID) -> PromptGenerationRecord:
        record = await self._memory.get_prompt_generation_async(generation_id=generation_id)
        if record is None:
            raise PromptGenerationNotFoundError(f"Prompt generation '{generation_id}' not found.")
        return record

    async def _read_sample_seeds_async(self, dataset_name: str) -> list[SeedRecord]:
        examples, _, _ = await self._memory.get_seed_examples_async(
            dataset_name=dataset_name, limit=1, seed_types=["objective"]
        )
        return next(iter(examples.values()), [])

    def _validate_targets(
        self,
        *,
        adapter: PromptGenerationAdapter,
        request: Mapping[str, Any],
        checkpoint: Mapping[str, Any] | None,
        targets: Mapping[str, str],
    ) -> None:
        resolved = self._resolve_targets(adapter=adapter, request=request, checkpoint=checkpoint, targets=targets)
        adapter.validate_targets(request=request, checkpoint=checkpoint, targets=resolved)

    def _resolve_targets(
        self,
        *,
        adapter: PromptGenerationAdapter,
        request: Mapping[str, Any],
        checkpoint: Mapping[str, Any] | None,
        targets: Mapping[str, str],
    ) -> dict[str, PromptTarget]:
        """
        Resolve the targets that the remaining stages use. A role whose work is finished needs no target.

        Returns:
            dict[str, PromptTarget]: The resolved targets by role.

        Raises:
            ValueError: If a role is unknown, a required role has no target, or a needed target isn't registered.
        """
        self._check_target_roles(adapter=adapter, targets=targets)
        roles = adapter.get_roles_to_run(request=request, checkpoint=checkpoint)
        resolved: dict[str, PromptTarget] = {}
        for role, name in targets.items():
            if role not in roles:
                continue
            target = self._target_lookup(name)
            if target is None:
                raise ValueError(f"Target '{name}' isn't registered.")
            resolved[role] = target
        return resolved

    @staticmethod
    def _check_target_roles(*, adapter: PromptGenerationAdapter, targets: Mapping[str, str]) -> None:
        roles = {role.role: role for role in adapter.descriptor.target_roles}
        unknown = sorted(set(targets) - set(roles))
        if unknown:
            raise ValueError(f"The {adapter.descriptor.name} generator has no target roles {unknown}.")
        missing = sorted(role.label for role in roles.values() if role.required and role.role not in targets)
        if missing:
            raise ValueError(f"Choose a target for: {', '.join(missing)}.")

    def _get_adapter(self, generator: str) -> PromptGenerationAdapter:
        adapter = self._adapters.get(generator)
        if adapter is None:
            raise ValueError(f"Unknown generator '{generator}'.")
        return adapter

    def _require_admission(self) -> None:
        if not self._admission_open:
            raise PromptGenerationConflictError("The backend is shutting down; try again after it restarts.")


def to_summary(record: PromptGenerationRecord) -> PromptGenerationSummary:
    """
    Project a record without its request and checkpoint.

    Args:
        record (PromptGenerationRecord): The stored record.

    Returns:
        PromptGenerationSummary: The listing view.
    """
    return PromptGenerationSummary.model_validate(
        record.model_dump(mode="json", include=set(PromptGenerationSummary.model_fields))
    )


def _apply(record: PromptGenerationRecord, **changes: Any) -> PromptGenerationRecord:
    return PromptGenerationRecord.model_validate({**record.model_dump(), **changes, "updated_at": datetime.now(UTC)})


def _parse_generation_id(generation_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(generation_id)
    except ValueError as error:
        raise PromptGenerationNotFoundError(f"Prompt generation '{generation_id}' not found.") from error


def _require_review_support(*, adapter: PromptGenerationAdapter, pause_for_review: bool) -> None:
    if pause_for_review and not any(stage.reviewable for stage in adapter.descriptor.stages):
        raise ValueError(
            f"The {adapter.descriptor.display_name} generator has no review step, so it can't pause for review."
        )


def _name_taken_message(dataset_name: str) -> str:
    return f"A dataset or generation named '{dataset_name}' already exists."


def _describe_error(error: BaseException) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'request'}: {item['msg']}" for item in error.errors()
        )
    message = str(error) or type(error).__name__
    root = error
    while root.__cause__ is not None:
        root = root.__cause__
    if root is not error:
        message += f" Cause: {type(root).__name__}: {root}"
    return message


def _lookup_registered_target(name: str) -> PromptTarget | None:
    target: PromptTarget | None = get_target_service().get_target_object(target_registry_name=name)
    return target


_service_instance: PromptGenerationService | None = None


def get_prompt_generation_service() -> PromptGenerationService:
    """
    Get the process-wide prompt generation service.

    Returns:
        PromptGenerationService: The singleton service.
    """
    global _service_instance
    if _service_instance is None:
        _service_instance = PromptGenerationService()
    return _service_instance


def peek_prompt_generation_service() -> PromptGenerationService | None:
    """Return the existing service without constructing one for lifecycle inspection."""
    return _service_instance


async def reset_prompt_generation_service_async() -> None:
    """Interrupt running work, let dataset saves finish, and discard the singleton."""
    global _service_instance
    if _service_instance is not None:
        await _service_instance.shutdown_async()
        _service_instance = None
