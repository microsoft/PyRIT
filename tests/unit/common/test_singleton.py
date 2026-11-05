# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from pyrit.common.singleton import Singleton
from unit.async_utils import TEST_WATCHDOG_SECONDS


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


def test_singleton_concurrent_first_calls_construct_once():
    """Concurrent first calls construct exactly one instance."""
    first_entered = threading.Event()
    second_entered = threading.Event()
    release = threading.Event()
    count = 0
    count_lock = threading.Lock()

    class _Gated(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            nonlocal count
            with count_lock:
                count += 1
                current = count
            (first_entered if current == 1 else second_entered).set()
            assert release.wait(timeout=TEST_WATCHDOG_SECONDS), "Gated construction was not released"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_Gated)
        try:
            assert first_entered.wait(timeout=TEST_WATCHDOG_SECONDS), "First construction did not start"
            second = pool.submit(_Gated)
            assert not second_entered.wait(timeout=1), "Second construction entered while the first was gated"
        finally:
            release.set()
        a = first.result(timeout=TEST_WATCHDOG_SECONDS)
        b = second.result(timeout=TEST_WATCHDOG_SECONDS)

    assert count == 1
    assert a is b


def test_singleton_failed_construction_is_not_cached():
    """A failed construction is not cached, so the next call retries construction."""
    attempts = 0

    class _Flaky(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        _Flaky()

    instance = _Flaky()
    assert attempts == 2
    assert _Flaky() is instance


def test_singleton_subclass_has_own_instance():
    """A subclass of a singleton gets its own instance, isolated from the parent."""
    class _Parent(abc.ABC, metaclass=Singleton):
        pass

    class _Child(_Parent):
        pass

    parent = _Parent()
    child = _Child()
    assert parent is not child
    assert _Parent() is parent
    assert _Child() is child


def test_singleton_nested_different_class_construction():
    """Constructing one singleton inside another singleton's __init__ works."""
    class _Inner(abc.ABC, metaclass=Singleton):
        pass

    class _Outer(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            self.inner = _Inner()

    outer = _Outer()
    assert outer is _Outer()
    assert outer.inner is _Inner()


def test_singleton_recursive_same_class_construction_raises():
    """Recursive construction of the same class raises instead of deadlocking."""
    class _Recursive(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            _Recursive()

    with pytest.raises(RuntimeError, match="Recursive construction"):
        _Recursive()
    assert _Recursive not in Singleton._instances
