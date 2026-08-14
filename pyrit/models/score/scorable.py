# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import uuid

    from pyrit.models.literals import ChatMessageRole, PromptDataType
    from pyrit.models.messages import Message
    from pyrit.models.score.scoring_scope import ScoringScope


class Volatility(Enum):
    """Whether two reads of the same scorable can disagree."""

    STABLE = "stable"
    APPEND_ONLY = "append_only"
    MUTABLE = "mutable"


@dataclass(frozen=True)
class ConversationScorable:
    """
    A stored conversation, resolved from memory.

    A conversation is append-only rather than stable because it grows while the attack
    runs; reading it after turn one and after turn five gives different evidence. Leave
    ``through_sequence`` unset to mean "as far as it goes right now"; set it to name a
    reproducible prefix.
    """

    volatility: ClassVar[Volatility] = Volatility.APPEND_ONLY

    conversation_id: str
    through_sequence: int | None = None
    role_filter: ChatMessageRole | None = None


@dataclass(frozen=True)
class MessageScorable:
    """
    A message the caller already holds.

    Use this when the message is in hand — an attack scoring the response it just
    received, or a caller scoring a message whose pieces were never persisted. Use
    ``MessageReferenceScorable`` when only the piece ids are known.
    """

    volatility: ClassVar[Volatility] = Volatility.STABLE

    message: Message
    role_filter: ChatMessageRole | None = None
    skip_on_error_result: bool = False


@dataclass(frozen=True)
class MessageReferenceScorable:
    """
    Specific message pieces, resolved from memory by id.

    Use this when the pieces are persisted and only their ids are known. Use
    ``MessageScorable`` when the caller already holds the message.
    """

    volatility: ClassVar[Volatility] = Volatility.STABLE

    message_piece_ids: tuple[uuid.UUID | str, ...]
    role_filter: ChatMessageRole | None = None
    skip_on_error_result: bool = False


@dataclass(frozen=True)
class ContentScorable:
    """Loose content with no conversation behind it."""

    volatility: ClassVar[Volatility] = Volatility.STABLE

    value: str
    data_type: PromptDataType = "text"


@dataclass(frozen=True)
class SurfaceScorable:
    """A location that may or may not have been written."""

    volatility: ClassVar[Volatility] = Volatility.MUTABLE

    uri: str
    surface: str = "file"
    scope: ScoringScope | None = None


@dataclass(frozen=True)
class TraceScorable:
    """
    A recorded agent run, or named steps within it.

    A span is the tracing term for one recorded step — a single tool call, model
    request, or nested operation. A trace is the tree of spans for one run.
    """

    volatility: ClassVar[Volatility] = Volatility.APPEND_ONLY

    trace_ids: tuple[str, ...] = ()
    span_name: str | None = None
    scope: ScoringScope | None = None


Scorable = (
    ConversationScorable
    | MessageScorable
    | MessageReferenceScorable
    | ContentScorable
    | SurfaceScorable
    | TraceScorable
)
