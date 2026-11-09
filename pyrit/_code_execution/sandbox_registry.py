# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Name the sandboxes a process uses, so every caller shares one ``Sandbox`` object per sandbox."""

from __future__ import annotations

import asyncio
import re
import threading
from typing import ClassVar

from pyrit._code_execution.sandbox import Sandbox
from pyrit.registry import DefaultInstanceRegistry


class SandboxRegistry:
    """
    The sandboxes this process uses, by name.

    Targets, scorers and operators refer to a sandbox by its name, so they share one ``Sandbox`` object,
    and with it one session and one lock. A second object with the same identifier would let two locks
    govern one sandbox, so it is refused. Names are letters, digits, dots, hyphens and underscores.
    """

    _NAME_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    _singleton: ClassVar[SandboxRegistry | None] = None
    _singleton_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._entries: DefaultInstanceRegistry[Sandbox] = DefaultInstanceRegistry(instance_type=Sandbox)
        self._lock = threading.RLock()

    @classmethod
    def get_registry_singleton(cls) -> SandboxRegistry:
        """
        Return the process-wide registry, creating it on first use.

        Returns:
            SandboxRegistry: The shared registry.
        """
        with cls._singleton_lock:
            if cls._singleton is None:
                cls._singleton = cls()
            return cls._singleton

    @classmethod
    def reset_registry_singleton(cls) -> None:
        """Forget the process-wide registry, without closing its sandboxes."""
        with cls._singleton_lock:
            cls._singleton = None

    def register(self, sandbox: Sandbox, *, name: str, tags: dict[str, str] | list[str] | None = None) -> None:
        """
        Register a sandbox under a name.

        Args:
            sandbox (Sandbox): The sandbox. Its capabilities are checked now.
            name (str): The name callers use.
            tags (dict[str, str] | list[str] | None): Optional tags for filtering.

        Raises:
            ValueError: If the name is invalid or taken, or the sandbox is already registered.
        """
        if not self._NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Sandbox name {name!r} must be 1-128 letters, digits, dots, hyphens or underscores.")
        sandbox.get_capabilities()
        identity = sandbox.get_identifier().hash
        with self._lock:
            if name in self._entries:
                raise ValueError(f"Another sandbox is already registered as {name!r}.")
            for entry in self._entries.get_all_instances():
                if entry.instance is sandbox or entry.instance.get_identifier().hash == identity:
                    raise ValueError(f"This sandbox is already registered as {entry.name!r}.")
            self._entries.register(sandbox, name=name, tags=tags)

    def get(self, name: str) -> Sandbox | None:
        """
        Return the sandbox registered under a name.

        Args:
            name (str): The registry name.

        Returns:
            Sandbox | None: The sandbox, or None if no sandbox has this name.
        """
        return self._entries.get(name)

    def get_names(self) -> list[str]:
        """
        Return the registered names.

        Returns:
            list[str]: The names, sorted.
        """
        return self._entries.get_names()

    def unregister(self, name: str) -> Sandbox | None:
        """
        Remove a sandbox from the registry, without closing it.

        Args:
            name (str): The registry name.

        Returns:
            Sandbox | None: The removed sandbox, or None if no sandbox had this name.
        """
        with self._lock:
            return self._entries.unregister(name)

    async def close_all_async(self) -> None:
        """Close every registered sandbox's session once its running call finishes. This never raises."""
        sandboxes = [entry.instance for entry in self._entries.get_all_instances()]
        await asyncio.gather(*(sandbox.close_async() for sandbox in sandboxes))


def resolve_sandbox(sandbox: Sandbox | str) -> Sandbox:
    """
    Return a sandbox, looking a name up in the process-wide registry.

    Args:
        sandbox (Sandbox | str): A sandbox, or the name it is registered under.

    Returns:
        Sandbox: The sandbox.

    Raises:
        ValueError: If no sandbox is registered under the name.
    """
    if not isinstance(sandbox, str):
        return sandbox
    registry = SandboxRegistry.get_registry_singleton()
    found = registry.get(sandbox)
    if found is None:
        raise ValueError(f"No sandbox is registered as {sandbox!r}; registered: {registry.get_names()}.")
    return found
