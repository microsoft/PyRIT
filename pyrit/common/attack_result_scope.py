# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The ID of the attack result that the running attack execution produces."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_current_attack_result_id: ContextVar[str | None] = ContextVar("pyrit_attack_result_id", default=None)


def get_current_attack_result_id() -> str | None:
    """
    Return the ID of the attack result the current attack execution produces.

    The ID is allocated when execution starts, so targets, scorers, converters and
    harnesses can read it before they send anything. Conversation creators pass it
    explicitly to ``Conversation.attack_result_id`` when registering ownership.

    Returns:
        str | None: The ID, or None outside an attack execution.
    """
    return _current_attack_result_id.get()


@contextmanager
def attack_result_id_scope(*, attack_result_id: str) -> Iterator[None]:
    """
    Make ``attack_result_id`` the current attack result ID within this scope.

    An attack opens the scope for one execution. A nested execution, such as a child
    attack, opens its own scope and restores the outer ID when it ends.

    Args:
        attack_result_id (str): The ID allocated for the execution's attack result.

    Yields:
        None: Control returns to the caller with the ID set.
    """
    token = _current_attack_result_id.set(attack_result_id)
    try:
        yield
    finally:
        _current_attack_result_id.reset(token)
