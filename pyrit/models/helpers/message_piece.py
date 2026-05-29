# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Free-function helpers that operate on :class:`MessagePiece` instances."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.models.message_piece import MessagePiece


def copy_lineage_to(*, target: MessagePiece, source: MessagePiece) -> None:
    """
    Copy lineage metadata from ``source`` onto ``target``.

    Lineage fields are the metadata that tie a piece back to its originating
    conversation, attack, and target. Mutable containers (``labels``,
    ``prompt_metadata``) are shallow-copied so that mutations on one piece
    do not affect others.

    Args:
        target: The piece whose lineage will be overwritten.
        source: The piece whose lineage metadata is authoritative.
    """
    target.conversation_id = source.conversation_id
    target.labels = dict(source.labels)
    target.attack_identifier = source.attack_identifier
    target.prompt_target_identifier = source.prompt_target_identifier
    target.prompt_metadata = dict(source.prompt_metadata)


def mark_not_persisted(piece: MessagePiece) -> None:
    """
    Flag ``piece`` so memory operations skip persisting it.

    Delegates to :meth:`MessagePiece.set_piece_not_in_database`, which sets a
    private ``not_in_database`` flag without touching the piece's ``id``. The
    memory layer filters flagged pieces out of inserts and gracefully drops the
    missing foreign key on any score that references one, so the score itself
    is still persisted.

    Args:
        piece: The piece to mark as not persisted.
    """
    piece.set_piece_not_in_database()
