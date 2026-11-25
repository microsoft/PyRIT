# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
The caller that the current work acts for.

Hosts identify each caller and scope the work a request starts to it with
``caller_principal_scope``. Work started from another caller's context, such as a queued run,
is given its own caller with ``caller_principal_context``. The registry uses the caller in scope
to show an entry that has an owner only to that owner.
"""

import contextvars
from collections.abc import Generator
from contextlib import contextmanager

_caller_principal: contextvars.ContextVar[str | None] = contextvars.ContextVar("pyrit_caller_principal", default=None)


def get_caller_principal() -> str | None:
    """
    Return the caller principal of the current context.

    Returns:
        str | None: The caller principal, or None when no caller is in scope.
    """
    return _caller_principal.get()


@contextmanager
def caller_principal_scope(principal: str | None) -> Generator[None, None, None]:
    """
    Run work on behalf of one caller, or of no caller when ``principal`` is None.

    Tasks created inside the scope inherit the principal.

    Args:
        principal (str | None): Stable key of the caller.

    Yields:
        None: The scope in which work acts for this caller.
    """
    token = _caller_principal.set(principal)
    try:
        yield
    finally:
        _caller_principal.reset(token)


def caller_principal_context(principal: str | None) -> contextvars.Context:
    """
    Copy the current context with an explicit caller principal.

    Pass the result as ``context`` to ``asyncio.create_task`` when work captured for one
    caller is started from another caller's context, such as a queued run.

    Args:
        principal (str | None): Stable key of the caller the work belongs to.

    Returns:
        contextvars.Context: A copy of the current context with the principal replaced.
    """
    context = contextvars.copy_context()
    context.run(_caller_principal.set, principal)
    return context
