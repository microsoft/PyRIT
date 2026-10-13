# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Pre-warm the ScenarioRegistry metadata cache.

Each registered ``Scenario`` is instantiated once so the registry can read the
strategy class, default strategy, and default dataset configuration off the
instance. The results are cached on ``Registry._metadata_cache``; the
first ``--list-scenarios`` / GUI call is then a cache hit. Per-scenario
instantiation failures surface loudly here at startup rather than later.
"""

import logging

from pyrit.common.async_compatibility import run_legacy_sync_async
from pyrit.registry import ScenarioRegistry
from pyrit.setup.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


class PreloadScenarioMetadata(PyRITInitializer):
    """Instantiate every registered scenario once to warm the metadata cache."""

    async def initialize_async(self) -> None:
        """Warm the scenario metadata cache."""
        registry = await run_legacy_sync_async(ScenarioRegistry.get_registry_singleton)
        metadata = await run_legacy_sync_async(registry.get_all_registered_class_metadata)
        logger.info("Preloaded metadata for %d scenarios", len(metadata))
