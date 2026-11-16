# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Keys that each caller sets for a shared target, resolved before every request.

A target created with ``auth_mode="caller"`` holds a ``CallerCredential`` instead of a key. Each
caller sets their own key in it. Before every request, the target asks it for the key of the caller
in scope. A missing caller or key raises ``CallerCredentialRequiredError``; the target never falls
back to an environment key, a managed identity, or another caller's key.

Keys live only in process memory, in the ``CallerCredential`` of the target they were set for. Hosts
set the caller with ``caller_principal_scope``. Work started from another caller's context must be
given its own caller with ``caller_principal_context``.
"""

import contextvars
import threading
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import NoReturn

_caller_principal: contextvars.ContextVar[str | None] = contextvars.ContextVar("pyrit_caller_principal", default=None)

_STATIC_AUTHENTICATION_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "api-key",
        "x-api-key",
        "x-goog-api-key",
        "ocp-apim-subscription-key",
        "cookie",
        "set-cookie",
    }
)


def validate_token_provider_headers(headers: Mapping[str, object] | None) -> None:
    """
    Reject static authentication headers that could replace a caller's own key.

    Args:
        headers (Mapping[str, object] | None): Headers sent with every request.

    Raises:
        ValueError: If headers are invalid or contain static authentication.
    """
    if headers is None:
        return
    if not isinstance(headers, Mapping):
        raise ValueError("Headers used with a token provider must be a mapping.")
    if any(name.lower() in _STATIC_AUTHENTICATION_HEADERS for name in headers):
        raise ValueError("Static authentication headers cannot be combined with a token provider.")


class CallerCredentialRequiredError(RuntimeError):
    """A target needs the current caller's own key, and none is available."""


class CallerCredential:
    """
    The keys that callers set for one target, each used only for that caller's requests.

    A target holds one instance. Copies of the target, such as one a chat makes to change the
    temperature, share it, so a caller's key works for every copy. Copying returns the same
    instance, and it cannot be pickled, so keys are never duplicated into serialized state.
    """

    def __init__(self) -> None:
        """Initialize a credential that no caller has set."""
        self._lock = threading.Lock()
        self._credentials: dict[str, str] = {}

    async def get_credential_async(self) -> str:
        """
        Return the key of the caller in scope.

        Targets pass this method as their token provider. It completes without suspending, so a client
        that stores the resolved key and then builds the request cannot interleave two callers' keys.

        Returns:
            str: The current caller's key.

        Raises:
            CallerCredentialRequiredError: If no caller is in scope, or the caller has not set a key.
        """
        principal = _caller_principal.get()
        if principal is None:
            raise CallerCredentialRequiredError(
                "This target uses each caller's own key, but this operation has no signed-in caller. "
                "Run it from an authenticated request."
            )
        with self._lock:
            credential = self._credentials.get(principal)
        if credential is None:
            raise CallerCredentialRequiredError(
                "This target uses each caller's own key, and you have not set yours. "
                "Set your key for it, then try again."
            )
        return credential

    def set_credential(self, *, principal: str, credential: str) -> None:
        """
        Set or replace one caller's key.

        Args:
            principal (str): Stable key of the caller that owns the key.
            credential (str): The key.

        Raises:
            ValueError: If the principal or key is empty.
        """
        if not principal:
            raise ValueError("A caller principal is required.")
        if not isinstance(credential, str) or not credential.strip():
            raise ValueError("A credential must be a nonempty string.")
        with self._lock:
            self._credentials[principal] = credential

    def has_credential(self, *, principal: str) -> bool:
        """
        Check whether a caller has set a key.

        Args:
            principal (str): Stable key of the caller.

        Returns:
            bool: True if the caller has set a key.
        """
        with self._lock:
            return principal in self._credentials

    def remove_credential(self, *, principal: str) -> bool:
        """
        Remove one caller's key.

        Args:
            principal (str): Stable key of the caller.

        Returns:
            bool: True if a key was removed.
        """
        with self._lock:
            return self._credentials.pop(principal, None) is not None

    def is_missing_for_current_caller(self) -> bool:
        """
        Check whether the caller in scope must set a key before using the target.

        Returns:
            bool: True if a caller is in scope and has not set a key. False if they have, or if no
                caller is in scope, such as shared work that acts for no caller.
        """
        principal = _caller_principal.get()
        return principal is not None and not self.has_credential(principal=principal)

    def __repr__(self) -> str:
        """
        Describe the credential without revealing keys.

        Returns:
            str: A description containing only the number of callers with a key.
        """
        with self._lock:
            count = len(self._credentials)
        return f"{type(self).__name__}(callers={count})"

    def __copy__(self) -> "CallerCredential":
        """
        Share the credential rather than copying keys.

        Returns:
            CallerCredential: This credential.
        """
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> "CallerCredential":
        """
        Share the credential rather than copying keys.

        Returns:
            CallerCredential: This credential.
        """
        return self

    def __reduce__(self) -> NoReturn:
        """
        Refuse serialization so keys never leave process memory.

        Raises:
            TypeError: Always.
        """
        raise TypeError(f"{type(self).__name__} cannot be serialized.")


def resolve_caller_credential(*, auth_mode: str, api_key: object) -> CallerCredential | None:
    """
    Return the credential a target uses for each caller's own key, or None for other auth modes.

    Args:
        auth_mode (str): The target's auth mode.
        api_key (object): The target's ``api_key`` argument. With ``auth_mode="caller"`` it is None
            for a new target, or the ``CallerCredential`` of the target being copied.

    Returns:
        CallerCredential | None: The credential for ``auth_mode="caller"``, otherwise None.

    Raises:
        ValueError: If ``auth_mode="caller"`` is combined with a key, or a ``CallerCredential`` is
            passed with another auth mode.
    """
    if auth_mode != "caller":
        if isinstance(api_key, CallerCredential):
            raise ValueError('A CallerCredential requires auth_mode="caller".')
        return None
    if api_key is None:
        return CallerCredential()
    if isinstance(api_key, CallerCredential):
        return api_key
    raise ValueError(
        'auth_mode="caller" uses each caller\'s own key, so it cannot be combined with an explicit api_key. '
        "Omit api_key; each caller sets their own key."
    )


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
        None: The scope in which targets resolve this caller's keys.
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
