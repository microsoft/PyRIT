# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Backward-compatibility shim.

``MessagePiece`` now lives in ``pyrit.models.messages``. Import from there (or
from ``pyrit.models``) instead. This module re-exports the public names so
existing ``from pyrit.models.message_piece import ...`` imports keep working.
"""

from typing import Any

from pyrit.models.messages import message_piece as _message_piece
from pyrit.models.messages.message_piece import MessagePiece, sort_message_pieces


def __getattr__(name: str) -> Any:
    return getattr(_message_piece, name)


__all__ = ["MessagePiece", "sort_message_pieces"]
