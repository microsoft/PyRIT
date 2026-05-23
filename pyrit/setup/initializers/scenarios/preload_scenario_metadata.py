# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Pre-warm the ScenarioRegistry metadata cache.

Each registered ``Scenario`` is instantiated once so the registry can read the
strategy class, default strategy, and default dataset configuration off the
instance. The results are cached on ``BaseClassRegistry._metadata_cache``; the
first ``--list-scenarios`` / GUI call is then a cache hit. Per-scenario
instantiation failures are logged and surfaced as degraded metadata by
``ScenarioRegistry._build_metadata`` so the pipeline continues.
"""

import logging
import textwrap

from pyrit.registry import ScenarioRegistry
from pyrit.setup.initializers.pyrit_initializer import PyRITInitializer

logger = logging.getLogger(__name__)


class PreloadScenarioMetadata(PyRITInitializer):
    """Instantiate every registered scenario once to warm the metadata cache."""

    @property
    def name(self) -> str:
        """Return the name of this initializer."""
        return "Preload Scenario Metadata"

    @property
    def execution_order(self) -> int:
        """Runs after target/scorer/attack-technique initializers, before LoadDefaultDatasets."""
        return 5

    @property
    def description(self) -> str:
        """Return a description of this initializer."""
        return textwrap.dedent(
            """
                Instantiate every registered scenario once to populate the
                ScenarioRegistry metadata cache. This surfaces broken scenario
                __init__ implementations loudly at startup (rather than on
                first --list-scenarios call) and makes downstream metadata
                consumers like LoadDefaultDatasets and the GUI cheap.
            """
        ).strip()

    @property
    def required_env_vars(self) -> list[str]:
        """Return the list of required environment variables."""
        return []

    async def initialize_async(self) -> None:
        """Warm the scenario metadata cache."""
        registry = ScenarioRegistry.get_registry_singleton()
        metadata = registry.list_metadata()
        logger.info("Preloaded metadata for %d scenarios", len(metadata))
