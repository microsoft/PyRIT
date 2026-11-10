# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Run prompt generations as background jobs and save their results as datasets.

Generators own model interaction and resumable progress. This service owns the job lifecycle:
admission, a concurrency limit, persistence of each checkpoint, review pauses, cancellation,
retry from the last checkpoint, dataset-name reservation, and reconciliation after a restart.
One backend instance owns the generation records.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from pyrit.backend.models.prompt_generations import (
    ACTIVE_GENERATION_STATUSES,
    RETRYABLE_GENERATION_STATUSES,
    PromptGenerationBatchCreateRequest,
    PromptGenerationBatchItem,
    PromptGenerationCreateRequest,
    PromptGenerationPreflightResponse,
    PromptGenerationProgress,
    PromptGenerationRecord,
    PromptGenerationRequestFromDataset,
    PromptGenerationReview,
    PromptGenerationReviewUpdate,
    PromptGenerationStatus,
    PromptGenerationSummary,
    PromptGeneratorDescriptor,
)
from pyrit.backend.services.dataset_service import parse_dataset_selection_key
from pyrit.backend.services.prompt_generation_adapters import (
    PromptGenerationAdapter,
    create_prompt_generation_adapters,
)
from pyrit.backend.services.prompt_generation_store import PromptGenerationStore
from pyrit.backend.services.target_service import get_target_service
from pyrit.memory import CentralMemory

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pyrit.memory import MemoryInterface
    from pyrit.models import PromptGenerationEstimate, SeedDataset, SeedRecord
    from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)

_INTERRUPTED_MESSAGE = "The backend stopped before this generation finished. Retry to resume from the last checkpoint."
_NAME_RELEASING_STATUSES = frozenset({PromptGenerationStatus.COMPLETED, PromptGenerationStatus.CANCELLED})


class PromptGenerationNotFoundError(LookupError):
    """No generation or generated dataset matches the request."""


class PromptGenerationConflictError(Exception):
    """The generation's state doesn't allow the request, or a dataset name is taken."""


class PromptGenerationService:
    """Own the lifecycle of prompt generation jobs."""

    DEFAULT_MAX_CONCURRENT_GENERATIONS = 1
    DEFAULT_ADDED_BY = "PromptGenerationService"

    def __init__(
        self,
        *,
        memory: MemoryInterface | None = None,
        store: PromptGenerationStore | None = None,
        adapters: Mapping[str, PromptGenerationAdapter] | None = None,
        target_lookup: Callable[[str], PromptTarget | None] | None = None,
        max_concurrent_generations: int = DEFAULT_MAX_CONCURRENT_GENERATIONS,
    ) -> None:
        """
        Configure storage, generators, target lookup, and the concurrency limit.

        Args:
            memory (MemoryInterface | None): Memory that receives datasets. Defaults to the central memory.
            store (PromptGenerationStore | None): Record storage. Defaults to the memory's results storage.
            adapters (Mapping[str, PromptGenerationAdapter] | None): Generators by name. Defaults to every
                built-in generator.
            target_lookup (Callable[[str], PromptTarget | None] | None): Resolves a target registry name.
                Defaults to the target registry.
            max_concurrent_generations (int): Generations that run at once. Others wait in the queue.

        Raises:
            ValueError: If max_concurrent_generations is less than one.
        """
        if max_concurrent_generations < 1:
            raise ValueError("max_concurrent_generations must be at least 1.")
        self._memory = memory or CentralMemory.get_memory_instance()
        self._store = store or PromptGenerationStore(memory=self._memory)
        self._adapters = dict(adapters) if adapters is not None else create_prompt_generation_adapters()
        self._target_lookup = target_lookup or _lookup_registered_target
        self._semaphore = asyncio.Semaphore(max_concurrent_generations)
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_requested: set[str] = set()
        self._saving: set[str] = set()
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

        The service runs this once on first use. One backend instance owns the records and a new
        service owns no tasks yet, so any record that still looks active belonged to a stopped process.

        Returns:
            int: The number of reconciled generations.
        """
        self._reconciled = True
        reconciled = 0
        for record in await self._store.list_async():
            if record.status in ACTIVE_GENERATION_STATUSES and record.id not in self._tasks:
                await self._update_async(
                    generation_id=record.id, status=PromptGenerationStatus.INTERRUPTED, error=_INTERRUPTED_MESSAGE
                )
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
        taken = await self._get_taken_dataset_names_async()
        available = request.dataset_name not in taken
        if not available:
            errors.append(_name_taken_message(request.dataset_name))
        if adapter is None:
            errors.append(f"Unknown generator '{request.generator}'.")
            return PromptGenerationPreflightResponse(valid=False, errors=errors, dataset_name_available=available)
        try:
            normalized = adapter.normalize_request(request.request)
            estimate = adapter.estimate(request=normalized, checkpoint=None)
            self._validate_targets(adapter=adapter, request=normalized, targets=request.targets)
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
        return await self._create_records_async(request=request, created_by=created_by, group_id=str(uuid.uuid4()))

    async def list_async(self) -> list[PromptGenerationRecord]:
        """
        List generations, newest first.

        Returns:
            list[PromptGenerationRecord]: The generations.
        """
        await self._reconcile_once_async()
        return await self._store.list_async()

    async def get_async(self, generation_id: str) -> PromptGenerationRecord:
        """
        Read one generation.

        Args:
            generation_id (str): The generation ID.

        Returns:
            PromptGenerationRecord: The generation.

        Raises:
            PromptGenerationNotFoundError: If it doesn't exist.
        """
        await self._reconcile_once_async()
        record = await self._store.get_async(generation_id)
        if record is None:
            raise PromptGenerationNotFoundError(f"Prompt generation '{generation_id}' not found.")
        return record

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

        A generation whose dataset was already saved before an interruption finishes without saving again.

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

        Args:
            generation_id (str): The generation ID.

        Returns:
            PromptGenerationRecord: The cancelled generation.

        Raises:
            PromptGenerationConflictError: If the generation is saving its dataset or already finished.
        """
        record = await self.get_async(generation_id)
        cancellable = {
            PromptGenerationStatus.QUEUED,
            PromptGenerationStatus.RUNNING,
            PromptGenerationStatus.AWAITING_REVIEW,
        }
        if record.status not in cancellable or generation_id in self._saving:
            raise PromptGenerationConflictError(f"A {record.status.value} generation can't be cancelled.")
        task = self._tasks.get(generation_id)
        if task is not None:
            self._cancel_requested.add(generation_id)
            task.cancel()
            await asyncio.wait({task})
            self._cancel_requested.discard(generation_id)
            record = await self.get_async(generation_id)
            # A task cancelled before it started never ran its handler, so its record is still queued.
            if record.status != PromptGenerationStatus.QUEUED:
                return record
        return await self._update_async(generation_id=generation_id, status=PromptGenerationStatus.CANCELLED)

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
        await self._store.delete_async(generation_id)

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
        return PromptGenerationReview(generation_id=record.id, stage=stage, payload=payload)

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
        async with self._lock:
            await self._update_locked_async(
                generation_id=generation_id,
                checkpoint=checkpoint,
                progress=adapter.get_progress(request=record.request, checkpoint=checkpoint),
            )
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
        self, *, request: PromptGenerationBatchCreateRequest, created_by: str | None, group_id: str | None
    ) -> list[PromptGenerationRecord]:
        self._require_admission()
        await self._reconcile_once_async()
        adapter = self._get_adapter(request.generator)
        normalized = [adapter.normalize_request(item.request) for item in request.items]
        for item_request in normalized:
            self._validate_targets(adapter=adapter, request=item_request, targets=request.targets)
        names = [item.dataset_name for item in request.items]
        if len(set(names)) != len(names):
            raise ValueError("Each generation in a batch needs a different dataset name.")
        now = datetime.now(UTC)
        records = [
            PromptGenerationRecord(
                id=str(uuid.uuid4()),
                generator=request.generator,
                dataset_name=item.dataset_name,
                status=PromptGenerationStatus.QUEUED,
                request=item_request,
                targets=dict(request.targets),
                pause_for_review=request.pause_for_review,
                group_id=group_id,
                group_label=request.label if group_id is not None else None,
                progress=adapter.get_progress(request=item_request, checkpoint=None),
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            for item, item_request in zip(request.items, normalized, strict=True)
        ]
        async with self._lock:
            _require_names_available(names=names, taken=await self._get_taken_dataset_names_async())
            for record in records:
                await self._store.save_async(record)
        for record in records:
            self._schedule(record.id)
        return records

    async def _queue_async(
        self, *, generation_id: str, allowed: set[PromptGenerationStatus], **changes: Any
    ) -> PromptGenerationRecord:
        self._require_admission()
        record = await self.get_async(generation_id)
        if record.status not in allowed:
            raise PromptGenerationConflictError(f"A {record.status.value} generation can't be queued.")
        adapter = self._get_adapter(record.generator)
        self._validate_targets(adapter=adapter, request=record.request, targets=record.targets)
        async with self._lock:
            taken = await self._get_taken_dataset_names_async(exclude_id=generation_id)
            if record.dataset_name in taken and not await self._dataset_came_from_async(record=record):
                raise PromptGenerationConflictError(_name_taken_message(record.dataset_name))
            queued = await self._update_locked_async(
                generation_id=generation_id, status=PromptGenerationStatus.QUEUED, **changes
            )
        self._schedule(generation_id)
        return queued

    def _schedule(self, generation_id: str) -> None:
        task = asyncio.create_task(self._run_async(generation_id), name=f"prompt-generation-{generation_id}")
        self._tasks[generation_id] = task

        def _forget(finished: asyncio.Task[None]) -> None:
            if self._tasks.get(generation_id) is finished:
                del self._tasks[generation_id]

        task.add_done_callback(_forget)

    async def _run_async(self, generation_id: str) -> None:
        try:
            async with self._semaphore:
                await self._execute_async(generation_id)
        except asyncio.CancelledError:
            cancelled = generation_id in self._cancel_requested
            status = PromptGenerationStatus.CANCELLED if cancelled else PromptGenerationStatus.INTERRUPTED
            await asyncio.shield(
                self._update_async(
                    generation_id=generation_id, status=status, error=None if cancelled else _INTERRUPTED_MESSAGE
                )
            )
            raise
        except Exception as error:
            logger.exception("Prompt generation %s failed.", generation_id)
            await self._update_async(
                generation_id=generation_id, status=PromptGenerationStatus.FAILED, error=_describe_error(error)
            )
        finally:
            self._cancel_requested.discard(generation_id)

    async def _execute_async(self, generation_id: str) -> None:
        record = await self.get_async(generation_id)
        if record.status != PromptGenerationStatus.QUEUED:
            return
        adapter = self._get_adapter(record.generator)
        targets = self._resolve_targets(adapter=adapter, targets=record.targets)
        await self._update_async(
            generation_id=generation_id,
            status=PromptGenerationStatus.RUNNING,
            started_at=record.started_at or datetime.now(UTC),
        )

        async def save_checkpoint_async(checkpoint: dict[str, Any], progress: PromptGenerationProgress) -> None:
            await self._update_async(generation_id=generation_id, checkpoint=checkpoint, progress=progress)

        outcome = await adapter.run_async(
            request=record.request,
            targets=targets,
            checkpoint=record.checkpoint,
            pause_for_review=record.pause_for_review and not record.review_completed,
            on_checkpoint=save_checkpoint_async,
        )
        if outcome.awaiting_review:
            await self._update_async(
                generation_id=generation_id,
                status=PromptGenerationStatus.AWAITING_REVIEW,
                checkpoint=outcome.checkpoint,
                progress=outcome.progress,
            )
            return
        # Shutdown waits for saving generations instead of cancelling them, so a saved dataset always
        # reaches a completed record.
        self._saving.add(generation_id)
        try:
            saving = await self._update_async(
                generation_id=generation_id,
                status=PromptGenerationStatus.SAVING,
                checkpoint=outcome.checkpoint,
                progress=outcome.progress,
            )
            dataset, summary = await adapter.build_dataset_async(
                request=record.request, targets=targets, checkpoint=outcome.checkpoint, dataset_name=record.dataset_name
            )
            await self._save_dataset_async(record=saving, dataset=dataset)
            await self._update_async(
                generation_id=generation_id,
                status=PromptGenerationStatus.COMPLETED,
                summary=summary,
                completed_at=datetime.now(UTC),
            )
        finally:
            self._saving.discard(generation_id)

    async def _save_dataset_async(self, *, record: PromptGenerationRecord, dataset: SeedDataset) -> None:
        if record.dataset_name in await self._memory.get_seed_dataset_names_async():
            if await self._dataset_came_from_async(record=record):
                return
            raise PromptGenerationConflictError(
                f"A dataset named '{record.dataset_name}' was created while generating. "
                "Use Edit & regenerate to save this request under a new name."
            )
        await self._memory.add_seed_datasets_to_memory_async(
            datasets=[dataset], added_by=record.created_by or self.DEFAULT_ADDED_BY
        )

    async def _dataset_came_from_async(self, *, record: PromptGenerationRecord) -> bool:
        if record.checkpoint is None:
            return False
        seeds = await self._read_sample_seeds_async(record.dataset_name)
        adapter = self._get_adapter(record.generator)
        return bool(seeds) and adapter.dataset_matches_checkpoint(seeds=seeds, checkpoint=record.checkpoint)

    async def _read_sample_seeds_async(self, dataset_name: str) -> list[SeedRecord]:
        examples, _, _ = await self._memory.get_seed_examples_async(
            dataset_name=dataset_name, limit=1, seed_types=["objective"]
        )
        return next(iter(examples.values()), [])

    async def _update_async(self, *, generation_id: str, **changes: Any) -> PromptGenerationRecord:
        async with self._lock:
            return await self._update_locked_async(generation_id=generation_id, **changes)

    async def _update_locked_async(self, *, generation_id: str, **changes: Any) -> PromptGenerationRecord:
        record = await self.get_async(generation_id)
        updated = PromptGenerationRecord.model_validate(
            {**record.model_dump(), **changes, "updated_at": datetime.now(UTC)}
        )
        await self._store.save_async(updated)
        return updated

    async def _get_taken_dataset_names_async(self, *, exclude_id: str | None = None) -> set[str]:
        taken = set(await self._memory.get_seed_dataset_names_async())
        taken.update(
            record.dataset_name
            for record in await self._store.list_async()
            if record.id != exclude_id and record.status not in _NAME_RELEASING_STATUSES
        )
        return taken

    def _validate_targets(
        self, *, adapter: PromptGenerationAdapter, request: Mapping[str, Any], targets: Mapping[str, str]
    ) -> None:
        adapter.validate_targets(request=request, targets=self._resolve_targets(adapter=adapter, targets=targets))

    def _resolve_targets(
        self, *, adapter: PromptGenerationAdapter, targets: Mapping[str, str]
    ) -> dict[str, PromptTarget]:
        self._check_target_roles(adapter=adapter, targets=targets)
        resolved: dict[str, PromptTarget] = {}
        for role, name in targets.items():
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
    return PromptGenerationSummary.model_validate(record.model_dump(include=set(PromptGenerationSummary.model_fields)))


def _require_names_available(*, names: list[str], taken: set[str]) -> None:
    conflicts = [name for name in names if name in taken]
    if conflicts:
        raise PromptGenerationConflictError("; ".join(_name_taken_message(name) for name in conflicts))


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
