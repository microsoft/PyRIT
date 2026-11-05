# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import abc
import threading

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


def test_singleton_concurrent_first_calls_create_single_instance():
    class _SlowInit(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            # Release the GIL so racing threads interleave inside __call__.
            threading.Event().wait(0.01)

    barrier = threading.Barrier(16)
    results: list = []

    def _make() -> None:
        barrier.wait()
        results.append(_SlowInit())

    threads = [threading.Thread(target=_make) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 16
    assert all(result is results[0] for result in results)


def test_singleton_nested_construction_does_not_deadlock():
    """A singleton whose __init__ builds another uncached singleton must not hang.

    Regression test for the Copilot review finding on PR #3970: the metaclass lock
    is shared across all Singleton classes, so a non-reentrant lock deadlocks when
    construction nests within the same thread.
    """

    class _Inner(abc.ABC, metaclass=Singleton):
        pass

    class _Outer(abc.ABC, metaclass=Singleton):
        def __init__(self) -> None:
            self.inner = _Inner()

    results: list = []
    worker = threading.Thread(target=lambda: results.append(_Outer()))
    worker.start()
    worker.join(timeout=10)

    assert not worker.is_alive(), "deadlock: nested singleton construction hung"
    assert len(results) == 1
    assert results[0].inner is _Inner()
