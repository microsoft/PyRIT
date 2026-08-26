# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
The PUZZLED jailbreak technique (arXiv:2508.01306): a converter that hides a prompt's
sensitive words inside a word puzzle, plus the keyword-masking and puzzle-building blocks
it is assembled from.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.converter.puzzled.puzzle_builders import PuzzleType
    from pyrit.converter.puzzled.puzzled_converter import PuzzledConverter

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "PuzzleType": "pyrit.converter.puzzled.puzzle_builders",
    "PuzzledConverter": "pyrit.converter.puzzled.puzzled_converter",
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
