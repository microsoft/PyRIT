# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from dataclasses import fields
from pathlib import Path
from typing import cast

from typing_extensions import override

from pyrit.common.path import JAILBREAK_TEMPLATES_PATH
from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider
from pyrit.datasets.seed_datasets.seed_metadata import SeedDatasetMetadata
from pyrit.models import SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


class _JailbreakTemplatesDataset(SeedDatasetProvider):
    """
    Loader that reads every local jailbreak template into a single SeedDataset.

    PyRIT ships a library of jailbreak templates (DAN, AIM, etc.) as individual
    ``SeedPrompt`` YAML files under ``JAILBREAK_TEMPLATES_PATH``. This provider scans
    that directory recursively and loads each template as a ``SeedPrompt`` so the whole
    collection is available in memory as one dataset, discoverable alongside the remote
    dataset providers via ``SeedDatasetProvider``.

    Unlike ``TextJailBreak`` (which selects a single template for rendering), this
    provider returns all templates at once without rendering them, leaving the
    ``{{ prompt }}`` placeholders intact.
    """

    # Metadata used for SeedDatasetFilter discovery (mirrors the remote loaders'
    # class-attribute convention).
    tags: frozenset[str] = frozenset({"jailbreak", "safety"})
    size: str = "medium"  # ~160 templates
    modalities: frozenset[str] = frozenset({"text"})
    source_type: str = "local"

    def __init__(self, *, templates_path: Path = JAILBREAK_TEMPLATES_PATH) -> None:
        """
        Initialize the jailbreak templates loader.

        Args:
            templates_path (Path): Directory to scan recursively for jailbreak template
                YAML files. Defaults to ``JAILBREAK_TEMPLATES_PATH``.
        """
        self._templates_path = templates_path

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "jailbreak_templates"

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Load every local jailbreak template into a single SeedDataset.

        Args:
            cache (bool): Ignored for local datasets (included for interface consistency).

        Returns:
            SeedDataset: A dataset containing one ``SeedPrompt`` per jailbreak template.

        Raises:
            ValueError: If no jailbreak templates are found in ``templates_path``.
        """
        seeds = await asyncio.to_thread(self._load_templates)
        if not seeds:
            raise ValueError(f"No jailbreak templates found in {self._templates_path}")
        logger.info(f"Loaded {len(seeds)} jailbreak templates from {self._templates_path}")
        return SeedDataset(seeds=cast("list[SeedUnion]", seeds), dataset_name=self.dataset_name)

    def _load_templates(self) -> list[SeedPrompt]:
        """
        Read all jailbreak template YAML files from disk as SeedPrompts.

        Invalid template files are logged and skipped so a single malformed file does
        not prevent the rest of the collection from loading.

        Returns:
            list[SeedPrompt]: The loaded templates, ordered by file path.
        """
        seeds: list[SeedPrompt] = []
        for path in sorted(self._templates_path.rglob("*.yaml")):
            try:
                seeds.append(SeedPrompt.from_yaml_file(path))
            except Exception as e:
                logger.warning(f"Skipping invalid jailbreak template {path}: {e}")
        return seeds

    @override
    async def _parse_metadata_async(self) -> SeedDatasetMetadata | None:
        """
        Build dataset metadata from this class's metadata attributes.

        Returns:
            SeedDatasetMetadata | None: Parsed metadata if any attributes are set, otherwise None.
        """
        valid_fields = [f.name for f in fields(SeedDatasetMetadata)]
        provider_class = type(self)
        raw = {}
        for key in valid_fields:
            value = getattr(provider_class, key, None)
            if value is None:
                continue
            raw[key] = value

        if not raw:
            return None

        coerced = SeedDatasetMetadata._coerce_metadata_values(raw_metadata=raw)
        result = SeedDatasetMetadata(**coerced)
        SeedDatasetMetadata._validate_singular_fields(metadata=result)
        return result
