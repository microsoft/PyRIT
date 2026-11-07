# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Capability checks for history copied into a fresh target conversation."""

from collections.abc import Sequence

from pyrit.models import Message
from pyrit.prompt_target import PromptTarget


class HistoryReplayError(ValueError):
    """The selected target cannot replay the supplied conversation history."""


def validate_history_replay(*, target: PromptTarget, messages: Sequence[Message]) -> None:
    """
    Check replay permission separately from ordinary live continuation.

    Empty history needs no replay capability. Initial system/developer setup is
    also allowed when the target supports system prompts and multiple turns.

    Raises:
        ValueError: The target cannot replay the history or accept its pieces.
    """
    capabilities = target.capabilities
    system_setup = (
        capabilities.supports_multi_turn
        and capabilities.supports_system_prompt
        and all(message.api_role in ("system", "developer") for message in messages)
    )
    if messages and not system_setup:
        try:
            target.validate_history_replay()
        except ValueError as error:
            raise HistoryReplayError(
                "The selected target cannot initialize copies of this conversation because it does not support "
                "editable history. Select a different target or configure a supported history adapter."
            ) from error
    try:
        target.validate_history(messages)
    except ValueError as error:
        raise HistoryReplayError(str(error)) from error
