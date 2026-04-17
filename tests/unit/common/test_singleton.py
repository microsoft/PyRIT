# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc

import pytest

from pyrit.common.singleton import Singleton


@pytest.fixture(autouse=True)
def _cleanup_singleton_instances():
    """Reset Singleton registry after each test to prevent cross-test pollution."""
    yield
    Singleton._instances.clear()


def test_singleton_returns_same_instance():
    class _MySingleton(abc.ABC, metaclass=Singleton):
        pass

    a = _MySingleton()
    b = _MySingleton()
    assert a is b


def test_singleton_different_classes_have_different_instances():
    class _A(abc.ABC, metaclass=Singleton):
        pass

    class _B(abc.ABC, metaclass=Singleton):
        pass

    a = _A()
    b = _B()
    assert a is not b


def test_singleton_preserves_init_args():
    class _Configured(abc.ABC, metaclass=Singleton):
        def __init__(self, value: int = 0) -> None:
            self.value = value

    first = _Configured(value=42)
    second = _Configured(value=99)
    assert first is second
    assert second.value == 42
