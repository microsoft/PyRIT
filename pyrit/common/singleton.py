# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import threading


class Singleton(abc.ABCMeta):
    """
    A metaclass for creating singleton classes. A singleton class can only have one instance.
    If an instance of the class exists, it returns that instance; if not, it creates and returns a new one.
    """

    _instances: dict[type, object] = {}

    def __new__(
        mcs, name: str, bases: tuple[type, ...], namespace: dict[str, object], **kwargs: object
    ) -> type:
        """
        Create the class and give it a reentrant lock for synchronizing construction.

        The lock is created at class definition time so there is no race in lazily
        creating it, and each class gets its own lock so unrelated singletons do
        not serialize each other. The per-class state is placed in the namespace
        BEFORE the class is created: ``super().__new__`` runs the base classes'
        ``__init_subclass__`` hooks, and a hook may construct the singleton
        (e.g. to auto-register the instance), which needs the lock and flag
        to already exist on the class.

        Args:
            mcs (type): The metaclass.
            name (str): The name of the class being created.
            bases (tuple[type, ...]): The base classes of the class being created.
            namespace (dict[str, object]): The namespace of the class being created.

        Returns:
            type: The newly created class.
        """
        namespace.setdefault("_singleton_lock", threading.RLock())
        namespace.setdefault("_singleton_constructing", False)
        return super().__new__(mcs, name, bases, namespace, **kwargs)

    def __call__(cls, *args: object, **kwargs: object) -> object:
        """
        Override the default __call__ behavior to ensure only one instance of the singleton class is created.

        The cache check, construction, and publication are synchronized together under
        the class lock, so concurrent first calls construct exactly one instance.
        A failed construction is not cached, so the next call retries. A recursive
        construction of the same class raises ``RuntimeError`` instead of deadlocking.

        Returns:
            The singleton instance if it exists, otherwise creates a new one and returns it.

        Raises:
            RuntimeError: If the class is constructed recursively from its own ``__init__``.
        """
        with cls._singleton_lock:
            if cls in cls._instances:
                return cls._instances[cls]
            if cls._singleton_constructing:
                raise RuntimeError(f"Recursive construction of singleton {cls.__qualname__!r} is not supported.")
            cls._singleton_constructing = True
            try:
                instance = super().__call__(*args, **kwargs)
            finally:
                cls._singleton_constructing = False
            cls._instances[cls] = instance
            return instance
