# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import random
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True)
class RandomContext:
    """An immutable path below a root seed used to derive independent random streams."""

    seed: int
    path: tuple[str, ...] = ()

    def child(self, name: str) -> RandomContext:
        """
        Derive a named child context.

        Args:
            name (str): Stable name for the child stream.

        Returns:
            RandomContext: A child context independent from sibling streams.

        Raises:
            ValueError: If name is empty.
        """
        if not name:
            raise ValueError("Random context child name cannot be empty")
        return RandomContext(seed=self.seed, path=(*self.path, name))

    def derived_seed(self, *, stream: str) -> int:
        """
        Derive a stable integer seed for a named stream.

        Args:
            stream (str): Stable stream name within this context.

        Returns:
            int: A deterministic seed derived from the root seed and full path.

        Raises:
            ValueError: If stream is empty.
        """
        if not stream:
            raise ValueError("Random stream name cannot be empty")
        payload = "\x1f".join((str(self.seed), *self.path, stream)).encode("utf-8")
        return int.from_bytes(hashlib.sha256(payload).digest()[:16], byteorder="big")


@dataclass
class _RandomExecution:
    """Mutable random streams scoped to one converter invocation."""

    context: RandomContext | None
    namespace: str
    owner: object | None
    generators: dict[
        tuple[int | None, tuple[str, ...], str, int | None, int, object | None],
        random.Random,
    ] = field(default_factory=dict)


_configured_context: RandomContext | None = None
_active_execution: ContextVar[_RandomExecution | None] = ContextVar("pyrit_random_execution", default=None)


def configure_random_seed(*, seed: int | None) -> None:
    """
    Configure the process-wide root seed used by subsequent PyRIT operations.

    Args:
        seed (int | None): Root seed, or None to restore non-deterministic behavior.

    Raises:
        TypeError: If seed is not an int or None.
    """
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise TypeError("seed must be an int or None")

    global _configured_context
    _configured_context = RandomContext(seed=seed) if seed is not None else None


def get_configured_random_seed() -> int | None:
    """
    Return the configured root seed.

    Returns:
        int | None: The configured seed, or None when local randomness is unseeded.
    """
    return _configured_context.seed if _configured_context else None


@contextlib.contextmanager
def random_execution(
    *,
    namespace: str,
    seed: int | None = None,
    owner: object | None = None,
    operation_key: str | None = None,
) -> Iterator[None]:
    """
    Establish an operation-local random context for a converter invocation.

    Args:
        namespace (str): Stable converter namespace.
        seed (int | None): Explicit converter seed. Overrides the inherited root.
        owner (object | None): Operation owner used to distinguish nested instances
            of the same component type.
        operation_key (str | None): Stable input identity for deterministic diversity
            across distinct operations.
    """
    active = _active_execution.get()
    if active and active.namespace == namespace and (owner is None or active.owner is owner):
        yield
        return

    parent = active.context if active else _configured_context
    if seed is not None:
        context = RandomContext(seed=seed, path=parent.path if parent else ()).child(namespace)
    else:
        context = parent.child(namespace) if parent else None

    if context and operation_key is not None:
        operation_digest = hashlib.sha256(operation_key.encode("utf-8")).hexdigest()
        context = context.child(f"operation:{operation_digest}")

    token = _active_execution.set(_RandomExecution(context=context, namespace=namespace, owner=owner))
    try:
        yield
    finally:
        _active_execution.reset(token)


def get_random_generator(
    *,
    stream: str,
    namespace: str | None = None,
    seed: int | None = None,
    owner: object | None = None,
) -> random.Random:
    """
    Return a random generator for a stable named stream.

    The generator is cached within the current converter invocation. Without an
    active invocation, a fresh generator is returned for each call.

    Args:
        stream (str): Stable stream name.
        namespace (str | None): Optional nested component namespace.
        seed (int | None): Explicit component seed. Overrides inherited context.
        owner (object | None): Nested component instance used to isolate its
            mutable generator from sibling instances of the same type.

    Returns:
        random.Random: A generator isolated from sibling streams.
    """
    active = _active_execution.get()

    inherited_context = active.context if active else _configured_context
    context = (
        RandomContext(seed=seed, path=inherited_context.path if inherited_context else ())
        if seed is not None
        else inherited_context
    )

    if context and namespace:
        context = context.child(namespace)

    path = context.path if context else ()
    try:
        task: object | None = asyncio.current_task()
    except RuntimeError:
        task = None
    key = (
        context.seed if context else None,
        path,
        stream,
        id(owner) if owner is not None else None,
        threading.get_ident(),
        task,
    )
    if active:
        generator = active.generators.get(key)
        if generator is None:
            generator = random.Random(context.derived_seed(stream=stream) if context else None)
            active.generators[key] = generator
        return generator

    return random.Random(context.derived_seed(stream=stream) if context else None)


def get_random_seed(
    *,
    stream: str,
    namespace: str | None = None,
    seed: int | None = None,
) -> int | None:
    """
    Return a derived seed for libraries that manage their own generators.

    Args:
        stream (str): Stable stream name.
        namespace (str | None): Optional nested component namespace.
        seed (int | None): Explicit component seed. Overrides inherited context.

    Returns:
        int | None: Derived seed, or None when no root seed is configured.
    """
    active = _active_execution.get()
    inherited_context = active.context if active else _configured_context
    context = (
        RandomContext(seed=seed, path=inherited_context.path if inherited_context else ())
        if seed is not None
        else inherited_context
    )

    if context and namespace:
        context = context.child(namespace)
    return context.derived_seed(stream=stream) if context else None
