# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import requests

from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt

logger = logging.getLogger(__name__)

# Maps PromptIntel short category IDs to their full taxonomy names
_CATEGORY_DISPLAY_NAMES: dict[str, str] = {
    "manipulation": "Prompt Manipulation",
    "abuse": "Abusing Legitimate Functions",
    "patterns": "Suspicious Prompt Patterns",
    "outputs": "Abnormal Outputs",
}


class PromptIntelSeverity(Enum):
    """Severity levels for PromptIntel prompts."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PromptIntelCategory(Enum):
    """Threat categories in the PromptIntel dataset."""

    MANIPULATION = "manipulation"
    ABUSE = "abuse"
    PATTERNS = "patterns"
    OUTPUTS = "outputs"


class _PromptIntelDataset(_RemoteDatasetLoader):
    """
    Loader for the PromptIntel Indicators of Prompt Compromise (IoPC) dataset.

    PromptIntel provides a curated registry of real-world prompt injection attacks,
    jailbreaks, and other LLM exploitation techniques annotated with threat categories,
    severity levels, NOVA detection rules, and impact descriptions.

    Reference: https://promptintel.novahunting.ai
    API Docs: https://promptintel.novahunting.ai/api

    Each prompt is mapped to a SeedPrompt with the attack text and metadata.
    The attack title is stored in the SeedPrompt's `name` field.

    Note: PromptIntel does not provide separate objective data, so no SeedObjective
    objects are created.

    Warning: This dataset contains adversarial prompts designed to exploit LLMs.
    Use responsibly and consult your legal department before using for testing.
    """

    API_BASE_URL = "https://api.promptintel.novahunting.ai/api/v1"
    PROMPT_WEB_URL = "https://promptintel.novahunting.ai/prompt"
    MAX_PAGE_LIMIT = 100

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        severity: Optional[PromptIntelSeverity] = None,
        categories: Optional[list[PromptIntelCategory]] = None,
        search: Optional[str] = None,
        max_prompts: Optional[int] = None,
    ) -> None:
        """
        Initialize the PromptIntel dataset loader.

        Args:
            api_key: PromptIntel API key. Falls back to PROMPTINTEL_API_KEY env var if not provided.
            severity: Filter prompts by severity level. Defaults to None (all severities).
            categories: Filter prompts by threat categories. Defaults to None (all categories).
                When multiple categories are specified, separate API requests are made for each
                category and results are merged with deduplication.
            search: Search term to filter prompts by title and content. Defaults to None.
            max_prompts: Maximum number of prompts to fetch. Defaults to None (all available).

        Raises:
            ValueError: If an invalid severity or category is provided.
        """
        self._api_key = api_key

        if severity is not None:
            valid_severities = {s.value for s in PromptIntelSeverity}
            sev_value = severity.value if isinstance(severity, PromptIntelSeverity) else severity
            if sev_value not in valid_severities:
                raise ValueError(
                    f"Invalid severity: {sev_value}. Valid values: {[s.value for s in PromptIntelSeverity]}"
                )

        if categories is not None:
            valid_categories = {c.value for c in PromptIntelCategory}
            invalid_categories = {
                cat.value if isinstance(cat, PromptIntelCategory) else cat for cat in categories
            } - valid_categories
            if invalid_categories:
                raise ValueError(
                    f"Invalid categories: {', '.join(str(c) for c in invalid_categories)}. "
                    f"Valid values: {[c.value for c in PromptIntelCategory]}"
                )

        self._severity = severity
        self._categories = categories
        self._search = search
        self._max_prompts = max_prompts
        self.source = "https://promptintel.novahunting.ai"

    @property
    def dataset_name(self) -> str:
        """Return the dataset name."""
        return "promptintel"

    def _fetch_all_prompts(self) -> list[dict[str, Any]]:
        """
        Fetch all prompts from the PromptIntel API, handling pagination.

        When multiple categories are specified, separate API requests are made for each
        category and results are merged with deduplication by prompt ID.

        Returns:
            List[Dict[str, Any]]: All fetched prompt records.

        Raises:
            ValueError: If no API key is provided and PROMPTINTEL_API_KEY is not set.
            ConnectionError: If the API request fails.
        """
        api_key = self._api_key or os.environ.get("PROMPTINTEL_API_KEY")
        if not api_key:
            raise ValueError(
                "PromptIntel API key is required. Provide it via the 'api_key' parameter "
                "or set the PROMPTINTEL_API_KEY environment variable."
            )
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Build list of category values to fetch; [None] means fetch all categories
        categories_to_fetch: list[Optional[str]] = [c.value for c in self._categories] if self._categories else [None]

        all_prompts: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        limit = self.MAX_PAGE_LIMIT

        for category in categories_to_fetch:
            page = 1

            while True:
                params: dict[str, Any] = {"page": page, "limit": limit}
                if self._severity:
                    params["severity"] = self._severity.value
                if category:
                    params["category"] = category
                if self._search:
                    params["search"] = self._search

                response = requests.get(
                    f"{self.API_BASE_URL}/prompts",
                    headers=headers,
                    params=params,
                    timeout=30,
                )

                if response.status_code != 200:
                    raise ConnectionError(
                        f"PromptIntel API request failed with status {response.status_code}: {response.text}"
                    )

                body = response.json()
                data = body.get("data", [])
                pagination = body.get("pagination", {})

                for record in data:
                    record_id = record.get("id")
                    if record_id not in seen_ids:
                        seen_ids.add(record_id)
                        all_prompts.append(record)

                # Check if we've reached the max_prompts limit
                if self._max_prompts and len(all_prompts) >= self._max_prompts:
                    all_prompts = all_prompts[: self._max_prompts]
                    break

                # Check if there are more pages
                total_pages = pagination.get("pages", 1)
                if page >= total_pages:
                    break
                page += 1

            # Also break the outer loop if max_prompts reached
            if self._max_prompts and len(all_prompts) >= self._max_prompts:
                break

        return all_prompts

    def _parse_datetime(self, date_str: Optional[str]) -> Optional[datetime]:
        """
        Parse an ISO 8601 datetime string from the API.

        Args:
            date_str: ISO format datetime string, or None.

        Returns:
            datetime or None if parsing fails.
        """
        if not date_str:
            return None
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    def _build_metadata(self, record: dict[str, Any]) -> dict[str, str | int]:
        """
        Build the metadata dict from a PromptIntel record.

        Args:
            record: A single prompt record from the API.

        Returns:
            Dict[str, str | int]: Metadata dictionary with string or integer values.
        """
        metadata: dict[str, str | int] = {}

        if record.get("severity"):
            metadata["severity"] = record["severity"]

        categories = record.get("categories", [])
        if categories:
            display_names = [_CATEGORY_DISPLAY_NAMES.get(c, c) for c in categories if isinstance(c, str)]
            metadata["categories"] = ", ".join(display_names)

        tags = record.get("tags", [])
        if tags:
            metadata["tags"] = ", ".join(tags)

        model_labels = record.get("model_labels", [])
        if model_labels:
            metadata["model_labels"] = ", ".join(model_labels)

        reference_urls = record.get("reference_urls", [])
        if reference_urls:
            metadata["reference_urls"] = ", ".join(reference_urls)

        if record.get("nova_rule"):
            metadata["nova_rule"] = record["nova_rule"]

        if record.get("mitigation_suggestions"):
            metadata["mitigation_suggestions"] = record["mitigation_suggestions"]

        threat_actors = record.get("threat_actors", [])
        if threat_actors:
            metadata["threat_actors"] = ", ".join(threat_actors)

        malware_hashes = record.get("malware_hashes", [])
        if malware_hashes:
            metadata["malware_hashes"] = ", ".join(malware_hashes)

        return metadata

    def _convert_record_to_seed_prompt(self, record: dict[str, Any]) -> Optional[SeedPrompt]:
        """
        Convert a single PromptIntel record into a SeedPrompt.

        Args:
            record: A single prompt record from the API.

        Returns:
            A SeedPrompt, or None if the record is skipped.
        """
        prompt_value = record.get("prompt", "")
        if not prompt_value:
            return None

        title = record.get("title", "")
        if not title:
            return None

        record_id = record.get("id", "")

        # Build common fields
        threats = record.get("threats", [])
        harm_categories = threats if threats else None
        author = record.get("author", "")
        authors = [author] if author else None
        date_added = self._parse_datetime(record.get("created_at"))
        source_url = f"{self.PROMPT_WEB_URL}/{record_id}"
        impact_description = record.get("impact_description", "")
        metadata = self._build_metadata(record)

        # Escape Jinja2 template syntax in the prompt text
        escaped_prompt = f"{{% raw %}}{prompt_value}{{% endraw %}}"

        return SeedPrompt(
            value=escaped_prompt,
            data_type="text",
            name=title,
            dataset_name=self.dataset_name,
            harm_categories=harm_categories,
            description=impact_description if impact_description else None,
            authors=authors,
            source=source_url,
            date_added=date_added,
            metadata=metadata,
        )

    async def fetch_dataset(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch prompts from the PromptIntel API and return as a SeedDataset.

        Each prompt is converted into a SeedPrompt containing the attack text and metadata.

        Args:
            cache: Whether to cache the fetched dataset. Defaults to True. (Currently unused;
                reserved for future caching support.)

        Returns:
            SeedDataset: A SeedDataset containing all fetched prompts.
        """
        logger.info("Fetching prompts from PromptIntel API")

        records = self._fetch_all_prompts()

        all_seeds = []
        for record in records:
            seed = self._convert_record_to_seed_prompt(record)
            if seed:
                all_seeds.append(seed)

        logger.info(f"Successfully loaded {len(all_seeds)} prompts from PromptIntel")

        return SeedDataset(seeds=all_seeds, dataset_name=self.dataset_name)
