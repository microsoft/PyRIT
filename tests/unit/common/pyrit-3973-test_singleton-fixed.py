# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from unit.async_utils import TEST_WATCHDOG_SECONDS

from pyrit.common.singleton import Singleton


@pytest.fixture(autouse=True)
def _cleanup_singleton_instances() -> Iterator[None]:
    """Reset Singleton registry after each test to prevent cross-test pollution."""
    yield
    Singleton._instances.clear()


def test_singleton_returns_same_instance() -> None:
    class _MySingleton(abc.ABC, metaclass=Singleton):
        pass

    a = _MySingleton()
    b = _MySingleton()
    assert a is b


def test_singleton_different_classes_have_different_instances() -> None:
    class _A(abc.ABC, metaclass=Singleton):
        pass

    class _B(abc.ABC, metaclass=Singleton):
        pass

    a = _A()
    b = _B()
    assert a is not b


def test_singleton_preserves_init_args() -> None:
    class _Configured(abc.ABC, metaclass=Singleton):
        def __init__(self, value: int = 0) -> None:
            self.value = value

    first = _Configured(value=42)
    second = _Configured(value=99)
    assert first is second
    assert second.value == 42


def test_singleton_concurrent_first_calls_construct_once() -> None:
    """Concurrent first calls construct exactly one instance."""
    first_entered = threading.Event()
    second_entered = threading.Event()
    acquire_attempted = threading.Event()
    release = threading.Event()
    count = 0
    count_lock = threading.Lock()

    class _TrackingLock:
        """Lock proxy that signals when a caller attempts to acquire it."""

        def __init__(self) -> None:
            self._lock = threading.RLock()

        def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
            acquire_attempted.set()
            return self._lock.acquire(blocking, timeout)

        def release(self) -> None:
            self._lock.release()

        def __enter__(self) -> "_TrackingLock":
            self.acquire()
            return self

        def __exit__(self, *exc: object) -> bool:
            self.release()
            return False

    class _Gated(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            nonlocal count
            with count_lock:
                count += 1
                current = count
            (first_entered if current == 1 else second_entered).set()
            assert release.wait(timeout=TEST_WATCHDOG_SECONDS), "Gated construction was not released"

    # Instrument the lock so the test can observe the second caller reaching
    # lock acquisition. The first caller also acquires this lock, so clear
    # the event once the first construction has started.
    _Gated._singleton_lock = _TrackingLock()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_Gated)
        try:
            assert first_entered.wait(timeout=TEST_WATCHDOG_SECONDS), "First construction did not start"
            acquire_attempted.clear()
            second = pool.submit(_Gated)
            # Rendezvous: only release the first construction once the second
            # caller has provably reached lock acquisition. Waiting on the
            # attempt (rather than a fixed sleep) also covers delayed worker
            # startup: the test proceeds only once the second call is
            # actually in flight and blocked on the lock.
            assert acquire_attempted.wait(timeout=TEST_WATCHDOG_SECONDS), "Second caller did not reach lock acquisition"
            assert not second_entered.is_set(), "Second construction entered while the first was gated"
        finally:
            release.set()
        a = first.result(timeout=TEST_WATCHDOG_SECONDS)
        b = second.result(timeout=TEST_WATCHDOG_SECONDS)

    assert count == 1
    assert a is b


def test_singleton_construction_from_init_subclass() -> None:
    """A base class __init_subclass__ hook can construct the singleton at class creation time."""
    # The hook runs inside Singleton.__new__ (via super().__new__), before
    # the class object is returned, so the per-class lock and flag must
    # already exist when the hook constructs the instance.

    class _AutoRegister:
        def __init_subclass__(cls, **kwargs: object) -> None:
            super().__init_subclass__(**kwargs)
            cls.instance = cls()

    class _Service(_AutoRegister, metaclass=Singleton):
        pass

    assert _Service.instance is _Service()


def test_singleton_failed_construction_is_not_cached() -> None:
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


def test_singleton_subclass_has_own_instance() -> None:
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


def test_singleton_nested_different_class_construction() -> None:
    """Constructing one singleton inside another singleton's __init__ works."""

    class _Inner(abc.ABC, metaclass=Singleton):
        pass

    class _Outer(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            self.inner = _Inner()

    outer = _Outer()
    assert outer is _Outer()
    assert outer.inner is _Inner()


def test_singleton_recursive_same_class_construction_raises() -> None:
    """Recursive construction of the same class raises instead of deadlocking."""

    class _Recursive(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            _Recursive()

    with pytest.raises(RuntimeError, match="Recursive construction"):
        _Recursive()
    assert _Recursive not in Singleton._instances
