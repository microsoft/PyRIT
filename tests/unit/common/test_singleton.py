# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc

from pyrit.common.singleton import Singleton


def test_singleton_returns_same_instance():
    class _MySingleton(abc.ABC, metaclass=Singleton):
        pass

    a = _MySingleton()
    b = _MySingleton()
    assert a is b

    # Cleanup to avoid polluting other tests
    Singleton._instances.pop(_MySingleton, None)


def test_singleton_different_classes_have_different_instances():
    class _A(abc.ABC, metaclass=Singleton):
        pass

    class _B(abc.ABC, metaclass=Singleton):
        pass

    a = _A()
    b = _B()
    assert a is not b

    Singleton._instances.pop(_A, None)
    Singleton._instances.pop(_B, None)


def test_singleton_preserves_init_args():
    class _Configured(abc.ABC, metaclass=Singleton):
        def __init__(self, value: int = 0) -> None:
            self.value = value

    first = _Configured(value=42)
    second = _Configured(value=99)
    assert first is second
    assert second.value == 42

    Singleton._instances.pop(_Configured, None)
