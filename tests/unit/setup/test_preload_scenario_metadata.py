# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the PreloadScenarioMetadata initializer."""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.setup.initializers.scenarios.preload_scenario_metadata import PreloadScenarioMetadata


class TestPreloadScenarioMetadata:
    """Tests for PreloadScenarioMetadata.initialize_async."""

    @pytest.mark.asyncio
    async def test_initialize_async_calls_list_metadata(self) -> None:
        """``initialize_async`` should fetch the registry and call ``list_metadata`` to warm the cache."""
        initializer = PreloadScenarioMetadata()

        mock_registry = MagicMock()
        mock_registry.list_metadata.return_value = [MagicMock(), MagicMock(), MagicMock()]

        with patch(
            "pyrit.setup.initializers.scenarios.preload_scenario_metadata.ScenarioRegistry.get_registry_singleton",
            return_value=mock_registry,
        ):
            await initializer.initialize_async()

        mock_registry.list_metadata.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_initialize_async_propagates_registry_errors(self) -> None:
        """If a scenario fails to instantiate, ``list_metadata`` raises and the initializer surfaces it."""
        initializer = PreloadScenarioMetadata()

        mock_registry = MagicMock()
        mock_registry.list_metadata.side_effect = TypeError("scenario X is not no-arg instantiable")

        with patch(
            "pyrit.setup.initializers.scenarios.preload_scenario_metadata.ScenarioRegistry.get_registry_singleton",
            return_value=mock_registry,
        ):
            with pytest.raises(TypeError, match="not no-arg instantiable"):
                await initializer.initialize_async()
