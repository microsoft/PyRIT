# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Backend mappers module.

Pure mapping functions that translate between PyRIT domain models and backend API DTOs.
Centralizes all translation logic so domain models can evolve independently of the API contract.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.backend.mappers._preview import format_last_message_preview
    from pyrit.backend.mappers.attack_mappers import (
        attack_result_to_summary_async,
        pyrit_messages_to_dto_async,
        request_piece_to_pyrit_message_piece,
        request_to_pyrit_message,
    )
    from pyrit.backend.mappers.converter_mappers import converter_object_to_instance
    from pyrit.backend.mappers.target_mappers import target_object_to_instance

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "attack_result_to_summary_async": "pyrit.backend.mappers.attack_mappers",
    "converter_object_to_instance": "pyrit.backend.mappers.converter_mappers",
    "format_last_message_preview": "pyrit.backend.mappers._preview",
    "pyrit_messages_to_dto_async": "pyrit.backend.mappers.attack_mappers",
    "request_piece_to_pyrit_message_piece": "pyrit.backend.mappers.attack_mappers",
    "request_to_pyrit_message": "pyrit.backend.mappers.attack_mappers",
    "target_object_to_instance": "pyrit.backend.mappers.target_mappers",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
