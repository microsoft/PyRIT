# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import threading
from concurrent.futures import ThreadPoolExecutor

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


def test_singleton_concurrent_first_calls_construct_once():
    first_entered = threading.Event()
    second_entered = threading.Event()
    second_attempted_lock = threading.Event()
    release = threading.Event()
    count = 0
    count_lock = threading.Lock()

    class _InstrumentedLock:
        def __init__(self, target_lock: threading.RLock) -> None:
            self._target_lock = target_lock
            self._attempts = 0
            self._attempts_lock = threading.Lock()

        def __enter__(self) -> bool:
            with self._attempts_lock:
                self._attempts += 1
                if self._attempts >= 2:
                    second_attempted_lock.set()
            return self._target_lock.__enter__()

        def __exit__(self, exc_type, exc_val, exc_tb):
            return self._target_lock.__exit__(exc_type, exc_val, exc_tb)

    class _ConcurrentSingleton(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            nonlocal count
            with count_lock:
                count += 1
                current = count
            (first_entered if current == 1 else second_entered).set()
            if not release.wait(timeout=5):
                raise TimeoutError("Constructor timed out waiting for release.")

    _ConcurrentSingleton._singleton_lock = _InstrumentedLock(
        _ConcurrentSingleton._singleton_lock
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_ConcurrentSingleton)
        try:
            assert first_entered.wait(timeout=5)
            second = pool.submit(_ConcurrentSingleton)
            assert second_attempted_lock.wait(timeout=5)
            assert not second_entered.is_set()
        finally:
            release.set()
        a, b = first.result(timeout=5), second.result(timeout=5)

    assert count == 1
    assert a is b


def test_singleton_constructor_failure_and_retry():
    should_fail = True

    class _FlakySingleton(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            nonlocal should_fail
            if should_fail:
                should_fail = False
                raise ValueError("Initialization failed transiently.")

    with pytest.raises(ValueError, match="Initialization failed transiently."):
        _FlakySingleton()

    assert _FlakySingleton not in Singleton._instances

    instance = _FlakySingleton()
    assert instance is not None
    assert _FlakySingleton in Singleton._instances
    assert _FlakySingleton() is instance


def test_singleton_subclass_isolation():
    class _Base(abc.ABC, metaclass=Singleton):
        pass

    class _Sub1(_Base):
        pass

    class _Sub2(_Base):
        pass

    assert _Sub1._singleton_lock is not _Sub2._singleton_lock
    assert _Sub1._singleton_lock is not _Base._singleton_lock

    sub1 = _Sub1()
    sub2 = _Sub2()
    base = _Base()

    assert sub1 is not sub2
    assert sub1 is not base
    assert sub2 is not base
    assert _Sub1() is sub1
    assert _Sub2() is sub2
    assert _Base() is base


def test_singleton_nested_different_class_construction():
    class _Dependency(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            self.tag = "dep"

    class _ParentSingleton(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            self.dependency = _Dependency()

    parent = _ParentSingleton()
    assert parent.dependency is _Dependency()
    assert parent is _ParentSingleton()


def test_singleton_same_class_recursive_construction_raises():
    class _RecursiveSingleton(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            _RecursiveSingleton()

    with pytest.raises(RuntimeError, match=r"Recursive construction of singleton class _RecursiveSingleton detected\."):
        _RecursiveSingleton()

    assert _RecursiveSingleton not in Singleton._instances
