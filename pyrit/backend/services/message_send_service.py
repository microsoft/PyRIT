# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Shared manual-message execution and bounded, worker-local asynchronous progress."""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache, partial
from typing import Any

from pyrit.backend.mappers import request_piece_to_pyrit_message_piece, request_to_pyrit_message
from pyrit.backend.models.attacks import AddMessageRequest, ConverterConfigurationRequest, MessagePieceRequest
from pyrit.backend.models.message_sends import (
    MessageSendFailureStage,
    MessageSendRequest,
    MessageSendState,
    MessageSendStatus,
)
from pyrit.backend.services.converter_service import get_converter_service
from pyrit.backend.services.manual_send_scheduler import (
    ManualSendConflictError,
    ManualSendQueueFullError,
    ManualSendScheduler,
    get_manual_send_scheduler,
)
from pyrit.backend.services.media_persistence import persist_media_value_async
from pyrit.backend.services.target_service import get_target_service
from pyrit.common.deprecation import print_deprecation_message
from pyrit.memory import CentralMemory, data_serializer_factory
from pyrit.models import (
    MEDIA_PATH_DATA_TYPES,
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackTechniqueIdentifier,
    ComponentIdentifier,
    Conversation,
    ConverterIdentifier,
)
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer
from pyrit.prompt_target import PromptTarget

logger = logging.getLogger(__name__)


class MessageSendNotFoundError(LookupError):
    """A transient handle is missing, expired, or belongs to another attack."""


@dataclass(kw_only=True)
class _ValidatedMessage:
    target: PromptTarget | None
    request_configurations: list[ConverterConfiguration]
    response_configurations: list[ConverterConfiguration]
    applied_identifiers: dict[int, list[ConverterIdentifier]]


@dataclass(kw_only=True)
class _Send:
    status: MessageSendStatus
    submission_id: str
    fingerprint: str
    reservation: ExitStack
    task: asyncio.Task[None] | None = None


def resolve_applied_converter_identifiers(
    pieces: list[MessagePieceRequest],
) -> dict[int, list[ConverterIdentifier]]:
    """
    Resolve client-reported execution order without inferring type transitions.

    Returns:
        Registry-validated converter identifiers by message piece index, preserving order and duplicates.
    """
    return {
        index: [
            ConverterIdentifier.from_component_identifier(converter.get_identifier())
            for converter in get_converter_service().get_converter_objects_for_ids(
                converter_ids=piece.applied_converter_ids
            )
        ]
        for index, piece in enumerate(pieces)
        if piece.applied_converter_ids
    }


class MessageSendService:
    """Prepare manual messages and dispatch through the existing ``PromptNormalizer``."""

    TERMINAL_TTL_SECONDS = 600
    MAX_TERMINAL_SENDS = 128
    FAILURE_MESSAGES = {
        MessageSendFailureStage.PREPARATION: "Message preparation failed before target dispatch. Check server logs.",
        MessageSendFailureStage.SENDING: "Message sending failed. Inspect saved messages before sending again.",
        MessageSendFailureStage.FINALIZATION: (
            "Sending finished, but attack details could not be finalized. Inspect saved messages before sending again."
        ),
        MessageSendFailureStage.INTERRUPTED: (
            "Send interrupted. Provider delivery may be unknown. Inspect saved messages; do not automatically resend."
        ),
    }

    def __init__(
        self,
        *,
        scheduler: ManualSendScheduler | None = None,
    ) -> None:
        """Initialize the manual-message service with the application's memory."""
        self._memory = CentralMemory.get_memory_instance()
        self._scheduler = scheduler if scheduler is not None else get_manual_send_scheduler()
        self._sends: dict[str, _Send] = {}
        self._submissions: dict[tuple[str, str], str] = {}
        self._terminal: OrderedDict[str, float] = OrderedDict()
        self._accept_lock = asyncio.Lock()
        self._closing = False
        self._shutdown_task: asyncio.Task[None] | None = None

    async def submit_async(self, *, attack_result_id: str, request: MessageSendRequest) -> MessageSendStatus:
        """
        Validate and admit one background send, deduplicating only retained worker-local submissions.

        Returns:
            MessageSendStatus: A detached progress snapshot, not confirmation of delivery.
        """
        payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        async with self._accept_lock:
            if self._closing:
                raise ManualSendQueueFullError("Manual message operations are shutting down")
            self._expire_terminal_sends()
            submission_key = (attack_result_id, request.submission_id)
            existing_id = self._submissions.get(submission_key)
            if existing_id is not None:
                existing = self._sends[existing_id]
                if existing.fingerprint != fingerprint:
                    raise ManualSendConflictError("submission_id was already used with a different message request")
                return existing.status.model_copy(deep=True)

            with ExitStack() as reservation:
                reservation.enter_context(self._scheduler.reserve(conversation_id=request.target_conversation_id))
                owned_request = request.model_copy(deep=True)
                validated = await self._validate_message_async(attack_result_id=attack_result_id, request=owned_request)
                if self._closing:
                    raise ManualSendQueueFullError("Manual message operations are shutting down")
                operation = _Send(
                    status=MessageSendStatus(
                        send_id=str(uuid.uuid4()),
                        attack_result_id=attack_result_id,
                        conversation_id=request.target_conversation_id,
                    ),
                    submission_id=request.submission_id,
                    fingerprint=fingerprint,
                    reservation=reservation.pop_all(),
                )
                self._sends[operation.status.send_id] = operation
                self._submissions[submission_key] = operation.status.send_id
                operation.task = asyncio.create_task(
                    self._run_send_async(operation=operation, request=owned_request, validated=validated)
                )
                operation.task.add_done_callback(partial(self._on_send_done, operation=operation))
                return operation.status.model_copy(deep=True)

    async def get_status_async(self, *, attack_result_id: str, send_id: str, wait_ms: int = 0) -> MessageSendStatus:
        """
        Read a detached snapshot, optionally waiting for completion without cancelling execution.

        Returns:
            MessageSendStatus: The current worker-local progress snapshot.
        """
        if not 0 <= wait_ms <= 1000:
            raise ValueError("wait_ms must be between 0 and 1000")
        self._expire_terminal_sends()
        operation = self._sends.get(send_id)
        if operation is None or operation.status.attack_result_id != attack_result_id:
            raise MessageSendNotFoundError(
                "Send status is unavailable or expired. Refresh saved messages; do not automatically resend."
            )
        if operation.task is not None and wait_ms:
            await asyncio.wait({operation.task}, timeout=wait_ms / 1000)
        return operation.status.model_copy(deep=True)

    async def shutdown_async(self) -> None:
        """Stop admission, cancel accepted sends, and join their unavoidable writes before releasing ownership."""
        self._closing = True
        self._scheduler.stop_admission()
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._settle_sends_async())
        cancelled = False
        while not self._shutdown_task.done():
            try:
                await asyncio.shield(self._shutdown_task)
            except asyncio.CancelledError:
                cancelled = True
        self._shutdown_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def add_message_async(self, *, attack_result_id: str, request: AddMessageRequest) -> None:
        """
        Add a message to an attack, optionally sending to target.

        Messages are stored in the database via PromptNormalizer.
        The ``request.target_conversation_id`` field specifies which conversation
        the messages are stored under (main conversation or a related one).
        """
        async with self.add_message_context_async(attack_result_id=attack_result_id, request=request):
            pass

    @asynccontextmanager
    async def add_message_context_async(
        self, *, attack_result_id: str, request: AddMessageRequest
    ) -> AsyncIterator[None]:
        """
        Add a message and keep its conversation reserved through the caller's response reads.

        Yields:
            None: The completed operation's conversation reservation.
        """
        with self._scheduler.reserve(conversation_id=request.target_conversation_id):
            await self._add_message_async(attack_result_id=attack_result_id, request=request)
            yield

    async def _add_message_async(self, *, attack_result_id: str, request: AddMessageRequest) -> None:
        validated = await self._validate_message_async(attack_result_id=attack_result_id, request=request)
        await self._execute_validated_message_async(
            attack_result_id=attack_result_id, request=request, validated=validated
        )

    async def _validate_message_async(self, *, attack_result_id: str, request: AddMessageRequest) -> _ValidatedMessage:
        results = await asyncio.to_thread(self._memory.get_attack_results, attack_result_ids=[attack_result_id])
        if not results:
            raise ValueError(f"Attack '{attack_result_id}' not found")

        ar = results[0]
        target_registry_name = request.target_registry_name
        target = (
            get_target_service().get_target_object(target_registry_name=target_registry_name)
            if request.send and target_registry_name
            else None
        )
        self._validate_target_match(attack_identifier=ar.get_attack_strategy_identifier(), target=target)

        msg_conversation_id = request.target_conversation_id

        # Validate the target conversation belongs to this attack (main + pruned only)
        if msg_conversation_id not in ar.get_active_conversation_ids():
            raise ValueError(f"Conversation '{msg_conversation_id}' is not part of attack '{attack_result_id}'")

        if request.send and not target_registry_name:
            raise ValueError("target_registry_name is required when send=True")

        request_converter_configs = self._resolve_request_converter_configs(request=request)
        response_converter_configs = self._resolve_converter_configs(
            configurations=request.response_converter_configurations
        )
        if request.send and target is None:
            raise ValueError(f"Target object for '{target_registry_name}' not found")

        return _ValidatedMessage(
            target=target,
            request_configurations=request_converter_configs,
            response_configurations=response_converter_configs,
            applied_identifiers=resolve_applied_converter_identifiers(request.pieces),
        )

    async def _execute_validated_message_async(
        self,
        *,
        attack_result_id: str,
        request: AddMessageRequest,
        validated: _ValidatedMessage,
        progress: MessageSendStatus | None = None,
    ) -> None:
        async with self._scheduler.operation_async():
            if progress is not None:
                progress.state = MessageSendState.PREPARING
            await self._execute_message_async(
                attack_result_id=attack_result_id,
                request=request,
                target=validated.target,
                request_converter_configurations=validated.request_configurations,
                response_converter_configurations=validated.response_configurations,
                applied_converter_identifiers=validated.applied_identifiers,
                progress=progress,
            )

    async def _run_send_async(
        self, *, operation: _Send, request: MessageSendRequest, validated: _ValidatedMessage
    ) -> None:
        progress = operation.status
        try:
            await self._execute_validated_message_async(
                attack_result_id=progress.attack_result_id, request=request, validated=validated, progress=progress
            )
        except asyncio.CancelledError:
            self._record_failure(progress=progress, interrupted=True)
        except Exception:
            logger.exception("Message send '%s' failed during %s", progress.send_id, progress.state)
            self._record_failure(progress=progress)

    def _on_send_done(self, task: asyncio.Task[None], *, operation: _Send) -> None:
        # A task cancelled before its first step never enters _run_send_async.
        if task.cancelled():
            self._record_failure(progress=operation.status, interrupted=True)
        operation.reservation.close()
        operation.status.state = (
            MessageSendState.INTERRUPTED
            if operation.status.failure_stage == MessageSendFailureStage.INTERRUPTED
            else MessageSendState.FAILED
            if operation.status.failure_stage
            else MessageSendState.COMPLETED
        )
        operation.task = None
        self._terminal[operation.status.send_id] = time.monotonic()
        self._expire_terminal_sends()

    async def _settle_sends_async(self) -> None:
        async with self._accept_lock:
            tasks = [operation.task for operation in self._sends.values() if operation.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _expire_terminal_sends(self) -> None:
        cutoff = time.monotonic() - self.TERMINAL_TTL_SECONDS
        while self._terminal:
            oldest_id = next(iter(self._terminal))
            if len(self._terminal) <= self.MAX_TERMINAL_SENDS and self._terminal[oldest_id] > cutoff:
                break
            self._terminal.pop(oldest_id)
            operation = self._sends.pop(oldest_id)
            self._submissions.pop((operation.status.attack_result_id, operation.submission_id))

    @classmethod
    def _record_failure(cls, *, progress: MessageSendStatus, interrupted: bool = False) -> None:
        if interrupted:
            stage = MessageSendFailureStage.INTERRUPTED
        elif progress.state in (MessageSendState.QUEUED, MessageSendState.PREPARING):
            stage = MessageSendFailureStage.PREPARATION
        elif progress.state == MessageSendState.FINALIZING:
            stage = MessageSendFailureStage.FINALIZATION
        else:
            stage = MessageSendFailureStage.SENDING
        progress.failure_stage = stage
        progress.error = cls.FAILURE_MESSAGES[stage]

    async def _execute_message_async(
        self,
        *,
        attack_result_id: str,
        request: AddMessageRequest,
        target: PromptTarget | None,
        request_converter_configurations: list[ConverterConfiguration],
        response_converter_configurations: list[ConverterConfiguration],
        applied_converter_identifiers: dict[int, list[ConverterIdentifier]],
        progress: MessageSendStatus | None = None,
    ) -> None:
        msg_conversation_id = request.target_conversation_id
        preconverted_indexes = {
            index for index, piece in enumerate(request.pieces) if piece.converted_value is not None
        }
        last_response_id: str | None = None

        existing = await asyncio.to_thread(self._memory.get_message_pieces, conversation_id=msg_conversation_id)
        sequence = max((p.sequence for p in existing), default=-1) + 1
        if progress is not None:
            progress.request_turn_number = sequence

        if request.send:
            assert target is not None  # validated before acquiring the execution slot
            prior_ids = {p.id for p in existing}
            try:
                await self._send_and_store_message_async(
                    conversation_id=msg_conversation_id,
                    target=target,
                    request=request,
                    sequence=sequence,
                    request_converter_configurations=request_converter_configurations,
                    response_converter_configurations=response_converter_configurations,
                    preconverted_indexes=preconverted_indexes,
                    applied_converter_identifiers=applied_converter_identifiers,
                    on_target_dispatch=(
                        partial(setattr, progress, "state", MessageSendState.SENDING) if progress is not None else None
                    ),
                )
            except Exception:
                if progress is not None:
                    self._record_failure(progress=progress)
                # PromptNormalizer persists a full error piece (response_error +
                # traceback) to memory *before* re-raising. Surface that stored
                # piece inline so the send (POST) response matches the
                # conversation-reload (GET) view instead of collapsing to a
                # generic 500. If no new error piece was stored (the failure
                # happened before the send, e.g. media preparation), re-raise so the
                # route still reports a real error.
                current_pieces = await asyncio.to_thread(
                    self._memory.get_message_pieces, conversation_id=msg_conversation_id
                )
                if not any(p.id not in prior_ids and p.has_error() for p in current_pieces):
                    raise
                logger.exception(
                    "Send failed for attack '%s' conversation '%s'; surfacing stored error piece.",
                    attack_result_id,
                    msg_conversation_id,
                )
            if progress is not None:
                progress.state = MessageSendState.FINALIZING
            current_pieces = await asyncio.to_thread(
                self._memory.get_message_pieces,
                conversation_id=msg_conversation_id,
            )
            last_response = next(
                (piece for piece in current_pieces if piece.id not in prior_ids and piece.role == "assistant"),
                None,
            )
            last_response_id = str(last_response.id) if last_response else None
        else:
            existing_metadata = await asyncio.to_thread(
                self._memory._get_conversation, conversation_id=msg_conversation_id
            )
            await self._store_message_only_async(
                conversation_id=msg_conversation_id,
                request=request,
                sequence=sequence,
                target_identifier=existing_metadata.target_identifier if existing_metadata else None,
                applied_converter_identifiers=applied_converter_identifiers,
            )

        async with self._scheduler.metadata_update_async(attack_result_id=attack_result_id):
            await self._complete_memory_write_async(
                partial(
                    self._update_attack_after_message,
                    attack_result_id=attack_result_id,
                    last_response_id=last_response_id,
                    request_converter_configurations=self._exclude_preconverted_piece_indexes(
                        configurations=request_converter_configurations,
                        preconverted_indexes=preconverted_indexes,
                        piece_count=len(request.pieces),
                    ),
                    response_converter_configurations=response_converter_configurations,
                    applied_converter_identifiers=applied_converter_identifiers,
                )
            )

    def _validate_target_match(
        self, *, attack_identifier: ComponentIdentifier | None, target: PromptTarget | None
    ) -> None:
        """
        Validate that the request target matches the attack's stored target.

        Raises:
            ValueError: If the target in the request doesn't match the attack's target.
        """
        if target is None:
            return

        stored_target_id = attack_identifier.get_child("objective_target") if attack_identifier else None
        if not stored_target_id:
            return

        request_target_id = target.get_identifier()
        if stored_target_id.hash != request_target_id.hash:
            raise ValueError(
                f"Target mismatch: attack was created with {stored_target_id.unique_name} "
                f"but request uses {request_target_id.unique_name}. "
                f"Create a new attack to use a different target."
            )

    def _update_attack_after_message(
        self,
        *,
        attack_result_id: str,
        last_response_id: str | None,
        request_converter_configurations: list[ConverterConfiguration],
        response_converter_configurations: list[ConverterConfiguration],
        applied_converter_identifiers: dict[int, list[ConverterIdentifier]],
    ) -> None:
        """
        Update attack recency and converter tracking after a message is added.

        Bumps the attack's ``timestamp`` column (the single indexed recency key) so the edited
        conversation re-floats to the top of the History view.

        Args:
            attack_result_id: The attack result to update.
            last_response_id: The latest target response piece ID, if one was stored.
            request_converter_configurations: Resolved request converter configurations used for this message.
            response_converter_configurations: Resolved response converter configurations used for this message.
            applied_converter_identifiers: Registered converters already applied to each preconverted piece.

        Raises:
            ValueError: If the attack disappeared before its metadata could be updated.
        """
        results = self._memory.get_attack_results(attack_result_ids=[attack_result_id])
        if not results:
            raise ValueError(f"Attack '{attack_result_id}' not found after message send")
        ar = results[0]
        update_fields: dict[str, Any] = {"timestamp": datetime.now(UTC)}
        if last_response_id:
            update_fields["last_response_id"] = last_response_id

        request_converter_ids = [
            identifier for identifiers in applied_converter_identifiers.values() for identifier in identifiers
        ]
        request_converter_ids.extend(self._get_converter_identifiers(configurations=request_converter_configurations))
        response_converter_ids = self._get_converter_identifiers(configurations=response_converter_configurations)
        if request_converter_ids or response_converter_ids:
            attack_strategy_identifier = ar.get_attack_strategy_identifier()
            if attack_strategy_identifier and ar.atomic_attack_identifier:
                attack_id = AttackIdentifier.from_component_identifier(attack_strategy_identifier)
                merged_request_converters = self._merge_attack_result_converter_identifiers(
                    existing=attack_id.request_converters,
                    additions=request_converter_ids,
                )
                merged_response_converters = self._merge_attack_result_converter_identifiers(
                    existing=attack_id.response_converters,
                    additions=response_converter_ids,
                )
                if (
                    merged_request_converters != attack_id.request_converters
                    or merged_response_converters != attack_id.response_converters
                ):
                    new_attack_id = self._replace_converter_pipelines(
                        attack_id,
                        request_converters=merged_request_converters,
                        response_converters=merged_response_converters,
                    )
                    new_atomic = self._replace_attack_in_atomic(
                        AtomicAttackIdentifier.from_component_identifier(ar.atomic_attack_identifier),
                        attack=new_attack_id,
                    )
                    update_fields["atomic_attack_identifier"] = new_atomic.model_dump()

        self._memory.update_attack_result_by_id(
            attack_result_id=attack_result_id,
            update_fields=update_fields,
        )

    @staticmethod
    def _replace_converter_pipelines(
        attack_id: AttackIdentifier,
        *,
        request_converters: list[ConverterIdentifier],
        response_converters: list[ConverterIdentifier],
    ) -> AttackIdentifier:
        """
        Return a copy of ``attack_id`` with its converter pipelines replaced.

        Reconstructed through the constructor (not ``model_copy``) so the
        after-validator re-mirrors the typed converters into ``children`` and
        recomputes the content hash. All other params/children/attributes are
        preserved, so the identifier hashes identically apart from the converters.

        Returns:
            AttackIdentifier: A new identifier with the given converter pipelines.
        """
        return AttackIdentifier(
            class_name=attack_id.class_name,
            class_module=attack_id.class_module,
            params=dict(attack_id.params),
            children=dict(attack_id.children),
            attributes=dict(attack_id.attributes),
            request_converters=request_converters,
            response_converters=response_converters,
        )

    @staticmethod
    def _merge_attack_result_converter_identifiers(
        *,
        existing: list[ConverterIdentifier],
        additions: list[ConverterIdentifier],
    ) -> list[ConverterIdentifier]:
        """
        Merge converter usage into the aggregate attack result metadata.

        Attack result converter lists record which converters the attack used, not
        the exact converter pipeline for each message. Keep the first occurrence of
        each identifier across messages while preserving first-use order.

        Args:
            existing: Converter identifiers already recorded on the attack result.
            additions: Converter identifiers used by the new message.

        Returns:
            list[ConverterIdentifier]: Aggregate converter identifiers in first-use order.
        """
        merged = list(existing)
        existing_hashes = {converter.hash for converter in existing}
        for converter in additions:
            if converter.hash not in existing_hashes:
                merged.append(converter)
                existing_hashes.add(converter.hash)
        return merged

    @staticmethod
    def _replace_attack_in_atomic(
        atomic: AtomicAttackIdentifier, *, attack: AttackIdentifier
    ) -> AtomicAttackIdentifier:
        """
        Return a copy of ``atomic`` with its nested attack strategy replaced.

        Handles both the current nested shape (``atomic -> attack_technique ->
        attack``) and the legacy flat shape (``atomic -> attack``). Everything
        else is preserved so the composite identifier hashes identically apart
        from the swapped attack node.

        Returns:
            AtomicAttackIdentifier: A new composite identifier wrapping ``attack``.
        """
        technique = atomic.attack_technique
        if technique is not None:
            new_technique = AttackTechniqueIdentifier(
                class_name=technique.class_name,
                class_module=technique.class_module,
                params=dict(technique.params),
                children=dict(technique.children),
                attributes=dict(technique.attributes),
                attack=attack,
            )
            return AtomicAttackIdentifier(
                class_name=atomic.class_name,
                class_module=atomic.class_module,
                params=dict(atomic.params),
                children=dict(atomic.children),
                attributes=dict(atomic.attributes),
                attack_technique=new_technique,
            )
        # Legacy flat shape: the attack strategy lives in children["attack"].
        atomic_children = dict(atomic.children)
        atomic_children["attack"] = attack
        return AtomicAttackIdentifier(
            class_name=atomic.class_name,
            class_module=atomic.class_module,
            params=dict(atomic.params),
            children=atomic_children,
            attributes=dict(atomic.attributes),
        )

    @staticmethod
    async def _persist_base64_pieces_async(request: AddMessageRequest) -> None:
        """
        Resolve original and converted media independently, updating values in-place.

        The frontend sends binary media (images, audio, etc.) as base64 strings
        with a ``*_path`` data_type.  The PyRIT target layer expects ``*_path``
        values to be **file paths**, so we decode the base64 data, write it to
        the results store, and replace the request values with the resulting
        file path before the message is built.

        If the value is already an HTTP(S) URL (e.g. an Azure Blob Storage URL
        from a remixed/copied message), it is kept as-is since the file already
        exists in storage.
        """
        for piece in request.pieces:
            original_value = piece.original_value
            converted_value = piece.converted_value
            converted_type = piece.converted_value_data_type or piece.data_type
            if piece.data_type in MEDIA_PATH_DATA_TYPES:
                result = await persist_media_value_async(
                    value=original_value,
                    data_type=piece.data_type,
                    mime_type=piece.mime_type,
                    serializer_factory=data_serializer_factory,
                )
                if result.resolved:
                    original_value = result.value
                    if converted_value is None or (
                        converted_value == piece.original_value and converted_type == piece.data_type
                    ):
                        converted_value = original_value

            if (
                converted_value is not None
                and converted_type in MEDIA_PATH_DATA_TYPES
                and (converted_value != original_value or converted_type != piece.data_type)
            ):
                result = await persist_media_value_async(
                    value=converted_value,
                    data_type=converted_type,
                    serializer_factory=data_serializer_factory,
                )
                if result.resolved:
                    converted_value = result.value

            piece.original_value = original_value
            piece.converted_value = converted_value

    async def _send_and_store_message_async(
        self,
        *,
        conversation_id: str,
        target: PromptTarget,
        request: AddMessageRequest,
        sequence: int,
        request_converter_configurations: list[ConverterConfiguration],
        response_converter_configurations: list[ConverterConfiguration],
        preconverted_indexes: set[int],
        applied_converter_identifiers: dict[int, list[ConverterIdentifier]],
        on_target_dispatch: Callable[[], None] | None = None,
    ) -> None:
        """Send message to target via normalizer and store response."""
        await self._persist_base64_pieces_async(request)

        await asyncio.to_thread(self._resolve_video_remix_metadata, request)

        pyrit_message = request_to_pyrit_message(
            request=request,
            conversation_id=conversation_id,
            sequence=sequence,
        )
        for index, identifiers in applied_converter_identifiers.items():
            pyrit_message.message_pieces[index].converter_identifiers.extend(identifiers)

        request_converter_configurations = self._exclude_preconverted_piece_indexes(
            configurations=request_converter_configurations,
            preconverted_indexes=preconverted_indexes,
            piece_count=len(request.pieces),
        )

        normalizer = PromptNormalizer(converter_guard=self._scheduler.conversion_async)
        await normalizer.send_prompt_async(
            message=pyrit_message,
            target=target,
            conversation_id=conversation_id,
            request_converter_configurations=request_converter_configurations,
            response_converter_configurations=response_converter_configurations,
            on_target_dispatch=on_target_dispatch,
        )
        # PromptNormalizer stores both request and response in memory automatically

    async def _store_message_only_async(
        self,
        *,
        conversation_id: str,
        request: AddMessageRequest,
        sequence: int,
        applied_converter_identifiers: dict[int, list[ConverterIdentifier]],
        target_identifier: ComponentIdentifier | None = None,
    ) -> None:
        """Store message without sending (send=False)."""
        await self._persist_base64_pieces_async(request)
        await self._complete_memory_write_async(
            partial(
                self._store_message_only,
                conversation_id=conversation_id,
                request=request,
                sequence=sequence,
                target_identifier=target_identifier,
                applied_converter_identifiers=applied_converter_identifiers,
            )
        )

    def _store_message_only(
        self,
        *,
        conversation_id: str,
        request: AddMessageRequest,
        sequence: int,
        target_identifier: ComponentIdentifier | None,
        applied_converter_identifiers: dict[int, list[ConverterIdentifier]],
    ) -> None:
        self._memory.add_conversation_to_memory(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target_identifier)
        )
        for index, p in enumerate(request.pieces):
            piece = request_piece_to_pyrit_message_piece(
                piece=p,
                role=request.role,
                conversation_id=conversation_id,
                sequence=sequence,
            )
            piece.converter_identifiers.extend(applied_converter_identifiers.get(index, []))
            self._memory.add_message_pieces_to_memory(message_pieces=[piece])

    @staticmethod
    async def _complete_memory_write_async(write: Callable[[], None]) -> None:
        """Keep ownership until an offloaded write finishes, even when the caller cancels."""
        worker = asyncio.create_task(asyncio.to_thread(write))
        cancelled = False
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancelled = True
        worker.result()
        if cancelled:
            raise asyncio.CancelledError

    def _resolve_video_remix_metadata(self, request: AddMessageRequest) -> None:
        """
        Auto-resolve video_id metadata for remix mode.

        When a video_path piece is carried over from a previous conversation
        (via original_prompt_id) alongside a text piece, the video target
        requires video_id in the text piece's prompt_metadata. This method
        looks up the original piece's metadata and propagates the video_id.
        """
        video_pieces = [p for p in request.pieces if p.data_type == "video_path"]
        if not video_pieces:
            return

        text_piece = next((p for p in request.pieces if p.data_type == "text"), None)
        if not text_piece:
            return

        # Already has video_id — nothing to resolve
        if text_piece.prompt_metadata and text_piece.prompt_metadata.get("video_id"):
            return

        # Try to resolve video_id from the original prompt piece
        for vp in video_pieces:
            if not vp.original_prompt_id:
                continue
            original_pieces = self._memory.get_message_pieces(prompt_ids=[vp.original_prompt_id])
            if not original_pieces:
                continue
            video_id = (original_pieces[0].prompt_metadata or {}).get("video_id")
            if video_id:
                if text_piece.prompt_metadata is None:
                    text_piece.prompt_metadata = {}
                text_piece.prompt_metadata["video_id"] = video_id
                # Also set video_id on the video piece itself
                if vp.prompt_metadata is None:
                    vp.prompt_metadata = {}
                vp.prompt_metadata["video_id"] = video_id
                return

    def _resolve_request_converter_configs(self, *, request: AddMessageRequest) -> list[ConverterConfiguration]:
        """
        Resolve legacy or structured request converter configurations.

        Returns:
            list[ConverterConfiguration]: Resolved request configurations.
        """
        if request.converter_ids is not None:
            print_deprecation_message(
                old_item="AddMessageRequest.converter_ids",
                new_item="AddMessageRequest.request_converter_configurations",
                removed_in="1.3.0",
            )
        if request.converter_ids:
            converters = get_converter_service().get_converter_objects_for_ids(converter_ids=request.converter_ids)
            return ConverterConfiguration.from_converters(converters=converters)

        return self._resolve_converter_configs(configurations=request.request_converter_configurations)

    def _resolve_converter_configs(
        self,
        *,
        configurations: list[ConverterConfigurationRequest] | None,
    ) -> list[ConverterConfiguration]:
        """
        Resolve registry-backed converter configurations.

        Returns:
            list[ConverterConfiguration]: Resolved configurations in request order.
        """
        converter_service = get_converter_service()
        return [
            ConverterConfiguration(
                converters=converter_service.get_converter_objects_for_ids(converter_ids=configuration.converter_ids),
                indexes_to_apply=configuration.indexes_to_apply,
                prompt_data_types_to_apply=configuration.prompt_data_types_to_apply,
            )
            for configuration in configurations or []
        ]

    @staticmethod
    def _exclude_preconverted_piece_indexes(
        *,
        configurations: list[ConverterConfiguration],
        preconverted_indexes: set[int],
        piece_count: int,
    ) -> list[ConverterConfiguration]:
        """
        Exclude client-preconverted pieces from request converter configurations.

        Returns:
            list[ConverterConfiguration]: Configurations that still apply to at least one piece.
        """
        if not preconverted_indexes:
            return configurations

        filtered_configurations: list[ConverterConfiguration] = []
        for configuration in configurations:
            configured_indexes = configuration.indexes_to_apply
            candidate_indexes = range(piece_count) if configured_indexes is None else configured_indexes
            eligible_indexes = [index for index in candidate_indexes if index not in preconverted_indexes]
            if not eligible_indexes:
                continue
            filtered_configurations.append(
                ConverterConfiguration(
                    converters=configuration.converters,
                    indexes_to_apply=eligible_indexes,
                    prompt_data_types_to_apply=configuration.prompt_data_types_to_apply,
                )
            )
        return filtered_configurations

    @staticmethod
    def _get_converter_identifiers(*, configurations: list[ConverterConfiguration]) -> list[ConverterIdentifier]:
        """
        Flatten resolved converter identifiers in configuration order.

        Returns:
            list[ConverterIdentifier]: The converter identifiers.
        """
        return [
            ConverterIdentifier.from_component_identifier(converter.get_identifier())
            for configuration in configurations
            for converter in configuration.converters
        ]


@lru_cache(maxsize=1)
def get_message_send_service() -> MessageSendService:
    """Return this worker's asynchronous send owner."""
    return MessageSendService()
