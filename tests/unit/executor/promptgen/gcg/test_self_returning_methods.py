# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""GCG factories and fluent worker methods annotated ``-> Self`` keep the subclass type."""

from typing import Any, TypeVar, assert_type
from unittest.mock import MagicMock, patch

import pytest

attack_manager_mod = pytest.importorskip(
    "pyrit.executor.promptgen.gcg.attack.base.attack_manager",
    reason="attack_manager module not importable",
)
pytest.importorskip("torch", reason="torch not installed")

from pyrit.executor.promptgen.gcg.attack.base.attack_manager import (  # noqa: E402
    ModelWorker,
    ModelWorkerOperation,
    RngBundle,
)


class _DerivedRngBundle(RngBundle):
    pass


class _DerivedModelWorker(ModelWorker):
    pass


_WorkerT = TypeVar("_WorkerT", bound=ModelWorker)


def _bare_worker(worker_cls: type[_WorkerT]) -> _WorkerT:
    """Build a worker without loading a model; only the queue and process are used here."""
    worker = object.__new__(worker_cls)
    worker.model = MagicMock()
    worker.tasks = MagicMock()
    worker.results = MagicMock()
    worker.process = None
    return worker


@pytest.mark.parametrize("bundle_cls", [RngBundle, _DerivedRngBundle])
def test_rng_bundle_from_seed_returns_calling_class(bundle_cls: type[RngBundle]) -> None:
    bundle = bundle_cls.from_seed(base_seed=3, workers=[])

    assert type(bundle) is bundle_cls
    assert bundle.base_seed == 3


def test_rng_bundle_from_seed_preserves_subclass_type() -> None:
    bundle = _DerivedRngBundle.from_seed(base_seed=3, workers=[])

    assert_type(bundle, _DerivedRngBundle)


@pytest.mark.parametrize("worker_cls", [ModelWorker, _DerivedModelWorker])
@pytest.mark.parametrize("method", ["start", "stop", "__call__"])
def test_model_worker_fluent_methods_return_the_same_worker(worker_cls: type[ModelWorker], method: str) -> None:
    worker = _bare_worker(worker_cls)
    calls: dict[str, Any] = {
        "start": worker.start,
        "stop": worker.stop,
        "__call__": lambda: worker(object(), ModelWorkerOperation.LOGITS),
    }

    with (
        patch.object(attack_manager_mod.mp, "Process", return_value=MagicMock(pid=1)),
        patch.object(attack_manager_mod.torch.cuda, "empty_cache"),
    ):
        result = calls[method]()

    assert result is worker


def test_model_worker_fluent_methods_preserve_subclass_type() -> None:
    worker = _bare_worker(_DerivedModelWorker)

    with (
        patch.object(attack_manager_mod.mp, "Process", return_value=MagicMock(pid=1)),
        patch.object(attack_manager_mod.torch.cuda, "empty_cache"),
    ):
        started = worker.start()
        queued = worker(object(), ModelWorkerOperation.LOGITS)
        stopped = worker.stop()

    assert_type(started, _DerivedModelWorker)
    assert_type(queued, _DerivedModelWorker)
    assert_type(stopped, _DerivedModelWorker)
