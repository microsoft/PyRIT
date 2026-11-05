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
    _lock: threading.Lock = threading.Lock()

    def __call__(cls, *args: object, **kwargs: object) -> object:
        """
        Override the default __call__ behavior to ensure only one instance of the singleton class is created.

        Returns:
            The singleton instance if it exists, otherwise creates a new one and returns it.
        """
        if cls not in cls._instances:
            # Double-checked locking: concurrent first calls could otherwise
            # each pass the check above and construct their own instance.
            with cls._lock:
                if cls not in cls._instances:
                    cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]
