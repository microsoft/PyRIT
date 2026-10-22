# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Declare what a scorer accepts, so a mismatch is reported before evidence is acquired."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrit.models import ChatMessageRole, Condition, PromptDataType


class LegacyScorerHookError(TypeError):
    """
    A scorer's protected hook cannot receive the typed criterion the scorer declares.

    Legacy hooks receive only the objective. They keep working for objective-based and
    constructor-configured scoring until 2.0, but a scorer that declares a typed condition
    must implement an expectation-aware hook so the criterion reaches the code that matches it.
    """


@dataclass(frozen=True, kw_only=True)
class ScorerCapabilities:
    """
    The evidence and criterion a scorer accepts.

    Capabilities decide whether a scorer applies, never how an applicable input fails: piece
    limits and error policy stay on the message validator. A scorer that does not apply to
    some evidence returns no score for it, so configuration and preflight checks can compare
    these fields with the evidence and criteria they intend to supply.

    Attributes:
        condition_type (type[Condition] | None): The one condition a leaf matches, or None for
            a wrapper or a criterion fixed at construction. A wrapper's coverage is
            ``Scorer.get_condition_types()``.
        supported_data_types (frozenset[PromptDataType] | None): Message piece data types the
            scorer reads, or None when it does not score message pieces.
        supported_roles (frozenset[ChatMessageRole] | None): Message roles the scorer reads, or
            None when it does not score message pieces.
        required_metadata (frozenset[str]): Metadata keys a message piece must carry.
    """

    condition_type: type[Condition] | None = None
    supported_data_types: frozenset[PromptDataType] | None = None
    supported_roles: frozenset[ChatMessageRole] | None = None
    required_metadata: frozenset[str] = frozenset()
