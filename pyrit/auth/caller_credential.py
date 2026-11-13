# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Credentials that callers supply for a shared component and that resolve per call.

A shared component (such as a registered prompt target) holds a provider instead of a
secret. Each time the provider runs, it reads the caller principal from an explicitly
scoped context variable and returns only that caller's credential. A missing caller or
credential raises ``CallerCredentialRequiredError``; the provider never returns an empty
value that could make a client fall back to environment or ambient credentials.

Credentials live only in process memory. Hosts that run work on behalf of a caller set
the principal with ``caller_principal_scope``; work started from another caller's
context must be given its own principal with ``caller_principal_context``.
"""

import contextvars
import threading
import weakref
from collections.abc import Awaitable, Callable, Generator, Mapping
from contextlib import contextmanager
from typing import NoReturn

_caller_principal: contextvars.ContextVar[str | None] = contextvars.ContextVar("pyrit_caller_principal", default=None)

# Providers made by ``create_caller_credential_provider``. They stay plain coroutine functions, so
# ``ensure_async_token_provider`` returns them unchanged and this membership survives.
_caller_credential_providers: "weakref.WeakSet[Callable[[], Awaitable[str]]]" = weakref.WeakSet()


def validate_token_provider_headers(headers: Mapping[str, object] | None) -> None:
    """
    Reject static authentication that could replace a token provider's credential.

    Raises:
        ValueError: If headers are invalid or contain static authentication.
    """
    if headers is None:
        return
    if not isinstance(headers, Mapping):
        raise ValueError("Headers used with a token provider must be a mapping.")
    if any(
        name.lower() in {"authorization", "proxy-authorization", "api-key", "x-api-key", "x-goog-api-key"}
        for name in headers
    ):
        raise ValueError("Static authentication headers cannot be combined with a token provider.")


class CallerCredentialRequiredError(RuntimeError):
    """A component needs the current caller's own credential, and none is available."""


class CallerCredentialStore:
    """
    Process-memory credentials keyed by caller principal and credential ID.

    The store is a shared live dependency: copying returns the same store, and it cannot
    be pickled, so credentials are never duplicated into serialized state.
    """

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._lock = threading.Lock()
        self._credentials: dict[tuple[str, str], str] = {}

    def set_credential(self, *, principal: str, credential_id: str, credential: str) -> None:
        """
        Set or replace one caller's credential.

        Args:
            principal (str): Stable key of the caller that owns the credential.
            credential_id (str): ID of the credential slot, such as one registered target.
            credential (str): The credential value.

        Raises:
            ValueError: If the principal, credential ID, or credential is empty.
        """
        if not principal or not credential_id:
            raise ValueError("A caller principal and credential ID are required.")
        if not isinstance(credential, str) or not credential.strip():
            raise ValueError("A credential must be a nonempty string.")
        with self._lock:
            self._credentials[(principal, credential_id)] = credential

    def get_credential(self, *, principal: str, credential_id: str) -> str | None:
        """
        Read one caller's credential.

        Args:
            principal (str): Stable key of the caller.
            credential_id (str): ID of the credential slot.

        Returns:
            str | None: The credential, or None when the caller has not set one.
        """
        with self._lock:
            return self._credentials.get((principal, credential_id))

    def has_credential(self, *, principal: str, credential_id: str) -> bool:
        """
        Check whether a caller has set a credential.

        Args:
            principal (str): Stable key of the caller.
            credential_id (str): ID of the credential slot.

        Returns:
            bool: True if the caller has a credential for the slot.
        """
        return self.get_credential(principal=principal, credential_id=credential_id) is not None

    def remove_credential(self, *, principal: str, credential_id: str) -> bool:
        """
        Remove one caller's credential.

        Args:
            principal (str): Stable key of the caller.
            credential_id (str): ID of the credential slot.

        Returns:
            bool: True if a credential was removed.
        """
        with self._lock:
            return self._credentials.pop((principal, credential_id), None) is not None

    def remove_credential_id(self, credential_id: str) -> None:
        """Remove every caller's credential for one slot."""
        with self._lock:
            for key in [key for key in self._credentials if key[1] == credential_id]:
                del self._credentials[key]

    def clear(self) -> None:
        """Remove all credentials."""
        with self._lock:
            self._credentials.clear()

    def __len__(self) -> int:
        """
        Count stored credentials.

        Returns:
            int: The number of stored credentials.
        """
        with self._lock:
            return len(self._credentials)

    def __repr__(self) -> str:
        """
        Describe the store without revealing credentials.

        Returns:
            str: A description containing only the credential count.
        """
        return f"{type(self).__name__}(credentials={len(self)})"

    def __copy__(self) -> "CallerCredentialStore":
        """
        Share the store rather than copying credentials.

        Returns:
            CallerCredentialStore: This store.
        """
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> "CallerCredentialStore":
        """
        Share the store rather than copying credentials.

        Returns:
            CallerCredentialStore: This store.
        """
        return self

    def __reduce__(self) -> NoReturn:
        """
        Refuse serialization so credentials never leave process memory.

        Raises:
            TypeError: Always.
        """
        raise TypeError(f"{type(self).__name__} cannot be serialized.")


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
        None: The scope in which providers resolve this caller's credentials.
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


def create_caller_credential_provider(
    *, store: CallerCredentialStore, credential_id: str, component_name: str
) -> Callable[[], Awaitable[str]]:
    """
    Create a token provider that returns the current caller's credential on every call.

    The provider completes without suspending. Clients that store the resolved value and then
    build the request without an intervening await, such as the OpenAI SDK, therefore cannot
    interleave two callers' credentials on a shared client.

    Args:
        store (CallerCredentialStore): The store that holds callers' credentials.
        credential_id (str): ID of the credential slot the provider reads.
        component_name (str): Name shown in errors, such as the registered target name.

    Returns:
        Callable[[], Awaitable[str]]: An async token provider for targets that accept one.
    """

    async def resolve_caller_credential_async() -> str:
        principal = _caller_principal.get()
        if principal is None:
            raise CallerCredentialRequiredError(
                f"'{component_name}' uses caller-supplied credentials, but this operation has no caller "
                "identity. Run it from an authenticated request."
            )
        credential = store.get_credential(principal=principal, credential_id=credential_id)
        if credential is None:
            raise CallerCredentialRequiredError(
                f"You have not set a credential for '{component_name}'. Set your own credential for it before use."
            )
        return credential

    _caller_credential_providers.add(resolve_caller_credential_async)
    return resolve_caller_credential_async


def is_caller_credential_provider(value: object) -> bool:
    """
    Check whether a value is a provider made by ``create_caller_credential_provider``.

    Components apply the caller-credential contract, such as refusing settings that could replace
    the caller's credential, only to these providers. A token provider a user passes directly keeps
    the component's ordinary token-provider behavior.

    Args:
        value (object): A credential parameter value, such as a target's ``api_key``.

    Returns:
        bool: True if the value resolves each caller's own credential.
    """
    return value in _caller_credential_providers
