# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for :mod:`pyrit.models.helpers.message_piece`."""

import uuid

from pyrit.models import MessagePiece
from pyrit.models.helpers import copy_lineage_to, mark_not_persisted


def _make_piece(**overrides) -> MessagePiece:
    defaults = {
        "role": "user",
        "original_value": "hello",
        "conversation_id": "conv-source",
    }
    defaults.update(overrides)
    return MessagePiece(**defaults)


class TestCopyLineageTo:
    def test_copies_lineage_fields_from_source_to_target(self) -> None:
        source = _make_piece(
            conversation_id="conv-A",
            attack_identifier={"__type__": "Attack", "__module__": "x", "id": "atk-1"},
            prompt_target_identifier={"__type__": "Target", "__module__": "x", "id": "tgt-1"},
        )
        source.prompt_metadata = {"k": "v"}

        target = _make_piece(conversation_id="conv-B", role="assistant", original_value="hi")

        copy_lineage_to(target=target, source=source)

        assert target.conversation_id == "conv-A"
        assert target.attack_identifier == source.attack_identifier
        assert target.prompt_target_identifier == source.prompt_target_identifier
        assert target.prompt_metadata == {"k": "v"}

    def test_labels_and_metadata_are_shallow_copied(self) -> None:
        source = _make_piece()
        source.prompt_metadata = {"meta": "1"}

        target = _make_piece(role="assistant")

        copy_lineage_to(target=target, source=source)

        # Mutating the target containers should not affect the source.
        target.prompt_metadata["meta"] = "2"
        assert source.prompt_metadata == {"meta": "1"}

    def test_non_lineage_fields_are_preserved(self) -> None:
        source = _make_piece(conversation_id="conv-A")
        target = _make_piece(
            role="assistant",
            original_value="target-value",
            conversation_id="conv-B",
        )
        original_value_before = target.original_value
        role_before = target.role
        id_before = target.id

        copy_lineage_to(target=target, source=source)

        assert target.original_value == original_value_before
        assert target.role == role_before
        assert target.id == id_before


class TestMarkNotPersisted:
    def test_sets_not_in_database_flag(self) -> None:
        piece = _make_piece()
        assert piece.not_in_database is False
        original_id = piece.id

        mark_not_persisted(piece)

        assert piece.not_in_database is True
        # The id is intentionally preserved so scorers can still reference the piece
        # within the in-memory call; only the persistence policy changes.
        assert piece.id == original_id

    def test_idempotent_when_already_flagged(self) -> None:
        piece = _make_piece()
        mark_not_persisted(piece)
        original_id = piece.id

        mark_not_persisted(piece)

        assert piece.not_in_database is True
        assert piece.id == original_id

    def test_does_not_alter_other_fields(self) -> None:
        piece = _make_piece(conversation_id="conv-X")
        original_conv = piece.conversation_id
        original_role = piece.role
        original_value = piece.original_value
        original_id = piece.id

        mark_not_persisted(piece)

        assert piece.conversation_id == original_conv
        assert piece.role == original_role
        assert piece.original_value == original_value
        assert piece.id == original_id
        assert isinstance(piece.original_prompt_id, uuid.UUID) or piece.original_prompt_id is None
