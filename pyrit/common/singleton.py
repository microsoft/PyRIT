# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import threading
from typing import Any


class Singleton(abc.ABCMeta):
    """
    A metaclass for creating singleton classes. A singleton class can only have one instance.
    If an instance of the class exists, it returns that instance; if not, it creates and returns a new one.
    """

    _instances: dict[type, object] = {}

    def __init__(
        cls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """
        Initialize the singleton class with its own synchronization lock and construction state.

        Args:
            name: The name of the class being created.
            bases: The base classes of the class.
            namespace: The class namespace dictionary.
            **kwargs: Additional keyword arguments for metaclass initialization.
        """
        super().__init__(name, bases, namespace, **kwargs)
        cls._singleton_lock = threading.RLock()
        cls._is_constructing = False

    def __call__(cls, *args: object, **kwargs: object) -> object:
        """
        Override the default __call__ behavior to ensure only one instance of the singleton class is created.

        Args:
            *args: Positional arguments forwarded to the class constructor.
            **kwargs: Keyword arguments forwarded to the class constructor.

        Returns:
            The singleton instance if it exists, otherwise creates a new one and returns it.

        Raises:
            RuntimeError: If recursive construction of the same singleton class is detected.
        """
        if cls not in cls._instances:
            lock = cls.__dict__.get("_singleton_lock")
            if lock is None:
                lock = threading.RLock()
                cls._singleton_lock = lock
            with lock:
                if cls not in cls._instances:
                    if cls.__dict__.get("_is_constructing", False):
                        raise RuntimeError(f"Recursive construction of singleton class {cls.__name__} detected.")
                    cls._is_constructing = True
                    try:
                        cls._instances[cls] = super().__call__(*args, **kwargs)
                    finally:
                        cls._is_constructing = False
        return cls._instances[cls]
