# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the PreloadScenarioMetadata initializer."""

from threading import get_ident
from unittest.mock import MagicMock, patch

import pytest

from pyrit.setup.initializers.preload_scenario_metadata import PreloadScenarioMetadata


class TestPreloadScenarioMetadata:
    """Tests for PreloadScenarioMetadata.initialize_async."""

    async def test_initialize_async_warms_metadata_cache(self) -> None:
        """``initialize_async`` should fetch the registry and warm the metadata cache."""
        initializer = PreloadScenarioMetadata()

        mock_registry = MagicMock()
        backend_thread = get_ident()

        def get_metadata() -> list[MagicMock]:
            assert get_ident() != backend_thread
            return [MagicMock(), MagicMock(), MagicMock()]

        mock_registry.get_all_registered_class_metadata.side_effect = get_metadata

        with patch(
            "pyrit.setup.initializers.preload_scenario_metadata.ScenarioRegistry.get_registry_singleton",
            return_value=mock_registry,
        ):
            await initializer.initialize_async()

        mock_registry.get_all_registered_class_metadata.assert_called_once_with()

    async def test_initialize_async_propagates_registry_errors(self) -> None:
        """If a scenario fails to instantiate, metadata building raises and the initializer surfaces it."""
        initializer = PreloadScenarioMetadata()

        mock_registry = MagicMock()
        mock_registry.get_all_registered_class_metadata.side_effect = TypeError("scenario X is not no-arg instantiable")

        with patch(
            "pyrit.setup.initializers.preload_scenario_metadata.ScenarioRegistry.get_registry_singleton",
            return_value=mock_registry,
        ):
            with pytest.raises(TypeError, match="not no-arg instantiable"):
                await initializer.initialize_async()
