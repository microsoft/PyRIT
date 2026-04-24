# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Backend mappers module.

Pure mapping functions that translate between PyRIT domain models and backend API DTOs.
Centralizes all translation logic so domain models can evolve independently of the API contract.
"""

from pyrit.backend.mappers.attack_mappers import (
    attack_result_to_summary,
    pyrit_messages_to_dto_async,
    pyrit_scores_to_dto,
    request_piece_to_pyrit_message_piece,
    request_to_pyrit_message,
)
from pyrit.backend.mappers.converter_mappers import (
    converter_object_to_instance,
)
from pyrit.backend.mappers.target_mappers import (
    target_object_to_instance,
)

__all__ = [
    "attack_result_to_summary",
    "converter_object_to_instance",
    "pyrit_messages_to_dto_async",
    "pyrit_scores_to_dto",
    "request_piece_to_pyrit_message_piece",
    "request_to_pyrit_message",
    "target_object_to_instance",
]
