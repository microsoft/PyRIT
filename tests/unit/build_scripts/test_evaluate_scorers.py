# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

from build_scripts import evaluate_scorers
from pyrit.registry import ScorerRegistry
from pyrit.setup import IN_MEMORY
from pyrit.setup.initializers import ScorerInitializer, TargetInitializer
from pyrit.setup.initializers.targets import TargetInitializerTags


async def test_evaluate_scorers_preserves_initializer_defaults_async() -> None:
    with (
        patch.object(evaluate_scorers, "initialize_pyrit_async", new_callable=AsyncMock) as initialize_mock,
        patch.object(ScorerRegistry, "get_registry_singleton", return_value=ScorerRegistry()),
    ):
        await evaluate_scorers.evaluate_scorers()

    initialize_mock.assert_awaited_once()
    assert initialize_mock.await_args is not None
    assert initialize_mock.await_args.kwargs["memory_db_type"] == IN_MEMORY
    target_init, scorer_init = initialize_mock.await_args.kwargs["initializers"]
    assert isinstance(target_init, TargetInitializer)
    target_init.validate_params()
    assert target_init.params["tags"] == [TargetInitializerTags.DEFAULT, TargetInitializerTags.SCORER]
    assert target_init.params["auto_group"] is True
    assert isinstance(scorer_init, ScorerInitializer)
    scorer_init.validate_params()
