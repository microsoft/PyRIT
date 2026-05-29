# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Stateless helpers that operate on ``pyrit.models`` data classes."""

from pyrit.models.helpers.message_piece import copy_lineage_to, mark_not_persisted

__all__ = [
    "copy_lineage_to",
    "mark_not_persisted",
]
