# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Synchronous manual-message preparation, dispatch, and attack metadata updates."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, cast

from pyrit.backend.mappers import request_piece_to_pyrit_message_piece, request_to_pyrit_message
from pyrit.backend.models.attacks import AddMessageRequest, ConverterConfigurationRequest
from pyrit.backend.services.converter_service import get_converter_service
from pyrit.backend.services.media_persistence import persist_media_value_async
from pyrit.backend.services.target_service import get_target_service
from pyrit.common.deprecation import print_deprecation_message
from pyrit.memory import CentralMemory, MemoryInterface, data_serializer_factory
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackResult,
    AttackTechniqueIdentifier,
    ComponentIdentifier,
    Conversation,
    ConverterIdentifier,
    PromptDataType,
)
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer

logger = logging.getLogger(__name__)


class MessageSendService:
    """Prepare manual messages and dispatch through the existing ``PromptNormalizer``."""

    def __init__(self, *, memory: MemoryInterface | None = None) -> None:
        """Initialize the manual-message service with the application's memory."""
        self._memory = memory if memory is not None else CentralMemory.get_memory_instance()

    async def add_message_async(self, *, attack_result_id: str, request: AddMessageRequest) -> None:
        """
        Add a message to an attack, optionally sending to target.

        Messages are stored in the database via PromptNormalizer.
        The ``request.target_conversation_id`` field specifies which conversation
        the messages are stored under (main conversation or a related one).

        """
        results = self._memory.get_attack_results(attack_result_ids=[attack_result_id])
        if not results:
            raise ValueError(f"Attack '{attack_result_id}' not found")

        ar = results[0]
        self._validate_target_match(attack_identifier=ar.get_attack_strategy_identifier(), request=request)

        msg_conversation_id = request.target_conversation_id

        # Validate the target conversation belongs to this attack (main + pruned only)
        if msg_conversation_id not in ar.get_active_conversation_ids():
            raise ValueError(f"Conversation '{msg_conversation_id}' is not part of attack '{attack_result_id}'")

        target_registry_name = request.target_registry_name
        if request.send and not target_registry_name:
            raise ValueError("target_registry_name is required when send=True")

        request_converter_configs = self._resolve_request_converter_configs(request=request)
        response_converter_configs = self._resolve_converter_configs(
            configurations=request.response_converter_configurations
        )
        preconverted_indexes = {
            index for index, piece in enumerate(request.pieces) if piece.converted_value is not None
        }
        last_response_id: str | None = None

        # Get existing messages to determine sequence.
        # NOTE: This read-then-write is not atomic (TOCTOU). Fine for the
        # current single-user UI, but would need a DB-level sequence
        # generator or optimistic locking if concurrent writes are supported.
        existing = self._memory.get_message_pieces(conversation_id=msg_conversation_id)
        sequence = max((p.sequence for p in existing), default=-1) + 1

        if request.send:
            assert target_registry_name is not None  # validated above
            prior_ids = {p.id for p in existing}
            try:
                await self._send_and_store_message_async(
                    conversation_id=msg_conversation_id,
                    target_registry_name=target_registry_name,
                    request=request,
                    sequence=sequence,
                    request_converter_configurations=request_converter_configs,
                    response_converter_configurations=response_converter_configs,
                    preconverted_indexes=preconverted_indexes,
                )
            except Exception:
                # PromptNormalizer persists a full error piece (response_error +
                # traceback) to memory *before* re-raising. Surface that stored
                # piece inline so the send (POST) response matches the
                # conversation-reload (GET) view instead of collapsing to a
                # generic 500. If no new error piece was stored (the failure
                # happened before the send, e.g. target lookup), re-raise so the
                # route still reports a real error.
                current_pieces = self._memory.get_message_pieces(conversation_id=msg_conversation_id)
                if not any(p.id not in prior_ids and p.has_error() for p in current_pieces):
                    raise
                logger.exception(
                    "Send failed for attack '%s' conversation '%s'; surfacing stored error piece.",
                    attack_result_id,
                    msg_conversation_id,
                )
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
            existing_metadata = self._memory._get_conversation(conversation_id=msg_conversation_id)
            await self._store_message_only_async(
                conversation_id=msg_conversation_id,
                request=request,
                sequence=sequence,
                target_identifier=existing_metadata.target_identifier if existing_metadata else None,
            )

        await self._update_attack_after_message_async(
            attack_result_id=attack_result_id,
            ar=ar,
            last_response_id=last_response_id,
            request_converter_configurations=request_converter_configs,
            response_converter_configurations=response_converter_configs,
        )

    def _validate_target_match(
        self, *, attack_identifier: ComponentIdentifier | None, request: AddMessageRequest
    ) -> None:
        """
        Validate that the request target matches the attack's stored target.

        Raises:
            ValueError: If the target in the request doesn't match the attack's target.
        """
        if not request.send or not request.target_registry_name:
            return

        stored_target_id = attack_identifier.get_child("objective_target") if attack_identifier else None
        if not stored_target_id:
            return

        target_service = get_target_service()
        request_target_obj = target_service.get_target_object(target_registry_name=request.target_registry_name)
        if not request_target_obj:
            return

        request_target_id = request_target_obj.get_identifier()
        if stored_target_id.hash != request_target_id.hash:
            raise ValueError(
                f"Target mismatch: attack was created with {stored_target_id.unique_name} "
                f"but request uses {request_target_id.unique_name}. "
                f"Create a new attack to use a different target."
            )

    async def _update_attack_after_message_async(
        self,
        *,
        attack_result_id: str,
        ar: AttackResult,
        last_response_id: str | None,
        request_converter_configurations: list[ConverterConfiguration],
        response_converter_configurations: list[ConverterConfiguration],
    ) -> None:
        """
        Update attack recency and converter tracking after a message is added.

        Bumps the attack's ``timestamp`` column (the single indexed recency key) so the edited
        conversation re-floats to the top of the History view.

        Args:
            attack_result_id: The attack result to update.
            ar: The current attack result.
            last_response_id: The latest target response piece ID, if one was stored.
            request_converter_configurations: Resolved request converter configurations used for this message.
            response_converter_configurations: Resolved response converter configurations used for this message.
        """
        update_fields: dict[str, Any] = {"timestamp": datetime.now(UTC)}
        if last_response_id:
            update_fields["last_response_id"] = last_response_id

        request_converter_ids = self._get_converter_identifiers(configurations=request_converter_configurations)
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
        Persist base64-encoded non-text pieces to disk, updating values in-place.

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
            # Only persist *_path types (image_path, audio_path, video_path, binary_path).
            # Other non-text types (url, reasoning, function_call, tool_call, etc.)
            # are text-like and must not be base64-decoded.
            if not piece.data_type.endswith("_path"):
                continue

            result = await persist_media_value_async(
                value=piece.original_value,
                data_type=cast("PromptDataType", piece.data_type),
                mime_type=piece.mime_type,
                serializer_factory=data_serializer_factory,
            )
            if result.resolved:
                piece.original_value = result.value
                if piece.converted_value is None:
                    piece.converted_value = result.value

    async def _send_and_store_message_async(
        self,
        *,
        conversation_id: str,
        target_registry_name: str,
        request: AddMessageRequest,
        sequence: int,
        request_converter_configurations: list[ConverterConfiguration],
        response_converter_configurations: list[ConverterConfiguration],
        preconverted_indexes: set[int],
    ) -> None:
        """Send message to target via normalizer and store response."""
        target_obj = get_target_service().get_target_object(target_registry_name=target_registry_name)
        if not target_obj:
            raise ValueError(f"Target object for '{target_registry_name}' not found")

        await self._persist_base64_pieces_async(request)

        self._resolve_video_remix_metadata(request)

        pyrit_message = request_to_pyrit_message(
            request=request,
            conversation_id=conversation_id,
            sequence=sequence,
        )

        request_converter_configurations = self._exclude_preconverted_piece_indexes(
            configurations=request_converter_configurations,
            preconverted_indexes=preconverted_indexes,
            piece_count=len(request.pieces),
        )

        normalizer = PromptNormalizer()
        await normalizer.send_prompt_async(
            message=pyrit_message,
            target=target_obj,
            conversation_id=conversation_id,
            request_converter_configurations=request_converter_configurations,
            response_converter_configurations=response_converter_configurations,
        )
        # PromptNormalizer stores both request and response in memory automatically

    async def _store_message_only_async(
        self,
        *,
        conversation_id: str,
        request: AddMessageRequest,
        sequence: int,
        target_identifier: ComponentIdentifier | None = None,
    ) -> None:
        """Store message without sending (send=False)."""
        await self._persist_base64_pieces_async(request)
        self._memory.add_conversation_to_memory(
            conversation=Conversation(conversation_id=conversation_id, target_identifier=target_identifier)
        )
        for p in request.pieces:
            piece = request_piece_to_pyrit_message_piece(
                piece=p,
                role=request.role,
                conversation_id=conversation_id,
                sequence=sequence,
            )
            self._memory.add_message_pieces_to_memory(message_pieces=[piece])

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
