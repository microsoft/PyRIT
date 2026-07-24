# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
The PUZZLED jailbreak technique (arXiv:2508.01306): a converter that hides a prompt's
sensitive words inside a word puzzle, plus the keyword-masking and puzzle-building blocks
it is assembled from.
"""

from pyrit.converter.puzzled.puzzle_builders import PuzzleType
from pyrit.converter.puzzled.puzzled_converter import PuzzledConverter

__all__ = [
    "PuzzleType",
    "PuzzledConverter",
]
