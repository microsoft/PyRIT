# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import requests
from typing_extensions import override

from pyrit.common.path import DB_DATA_PATH
from pyrit.datasets.seed_datasets.remote.remote_dataset_loader import (
    _RemoteDatasetLoader,
)
from pyrit.models import SeedDataset, SeedPrompt, SeedUnion

logger = logging.getLogger(__name__)


class ODINSeverity(Enum):
    """Severity ratings assigned to 0DIN threat-feed reports."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    SEVERE = "severe"


class ODINSecurityBoundary(Enum):
    """Security boundary categories for 0DIN threat-feed reports."""

    GUARDRAIL_JAILBREAK = "guardrail_jailbreak"
    PROMPT_INJECTION = "prompt_injection"
    PROMPT_EXTRACTION = "prompt_extraction"
    CONTENT_MANIPULATION = "content_manipulation"
    INTERPRETER_JAILBREAK = "interpreter_jailbreak"
    OTHER = "other"


class ODINTaxonomyCategory(Enum):
    """Top-level categories from the 0DIN jailbreak taxonomy."""

    STRATAGEMS = "stratagems"
    FICTIONALIZING = "fictionalizing"
    LANGUAGE = "language"
    RHETORIC = "rhetoric"
    POSSIBLE_WORLDS = "possible_worlds"


class _ODINDataset(_RemoteDatasetLoader):
    """
    Loader for the 0DIN (0din.ai) Jailbreak / Threat Feed dataset.

    0DIN is Mozilla's GenAI bug-bounty and threat-intelligence program. The Threat Feed
    publishes verified jailbreak disclosures against production models, each annotated with
    a taxonomy (category/strategy/technique), severity, affected models, reproducibility test
    results, and impact scores. The taxonomy axis is drawn from 0DIN's published taxonomy,
    which is grounded in the "Summon a Demon and Bind it" grounded theory of LLM red teaming
    [@inie2025summon] (public taxonomy: https://0din.ai/research/taxonomy). Note this taxonomy
    describes *how* an attack is structured, not the *harm* it targets.

    Each report exposes one or more sample exploit prompts (``messages``), and—optionally—a
    large set of industry-specific ``variant_prompts``. Every prompt is mapped to a SeedPrompt
    containing the literal attack text; the report title is stored in the SeedPrompt's ``name``
    field. Sample prompts that repeat across multiple tested models are de-duplicated.

    Note: 0DIN does not expose separate objective data, so no SeedObjective objects are created.

    The 0DIN feed is live and grows over time. The raw feed is cached on disk (under
    ``DB_DATA_PATH``); because reports are returned newest-first, subsequent fetches sync
    incrementally — fetching only newly disclosed reports and merging them onto the cache.
    Pass ``cache=False`` to ``fetch_dataset_async`` to force a full refresh.

    Reference: [@odin2024], [@inie2025summon]
    API Docs: https://0din.ai/docs/jailbreak-feed/api

    This dataset is gated: programmatic access requires a 0DIN Team or Enterprise subscription
    and an API key. Provide the key via the ``api_key`` parameter or the ``0DIN_API_KEY``
    environment variable. See https://0din.ai/products for subscription details.

    Warning: This dataset contains adversarial prompts designed to exploit LLMs. Use responsibly
    and consult your legal department before using for testing.
    """

    # Metadata
    modalities: list[str] = ["text"]
    size: str = "large"  # ~1,346 unique sample prompts; far larger with variant prompts enabled
    tags: set[str] = {"safety", "jailbreak", "cybersecurity"}
    harm_categories: list[str] = sorted(c.value for c in ODINTaxonomyCategory)

    API_BASE_URL = "https://0din.ai/api/v1/threatfeed/"
    REPORT_WEB_URL = "https://0din.ai/threatfeed"
    PAGE_SIZE = 100
    # On-disk cache of the raw (unfiltered) feed, shared across filter configurations.
    CACHE_FILENAME = "0din_threatfeed.json"
    # 0DIN enforces a 25 req/min rate limit and returns transient 5xx (or 429/406 from its
    # anti-abuse layer) under load; retry those with backoff.
    MAX_RETRIES = 4
    RETRY_BACKOFF_SECONDS = 5.0
    _RETRYABLE_STATUS_CODES = frozenset({406, 429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        api_key: str | None = None,
        severity: ODINSeverity | None = None,
        security_boundaries: list[ODINSecurityBoundary] | None = None,
        categories: list[ODINTaxonomyCategory] | None = None,
        include_variant_prompts: bool = False,
    ) -> None:
        """
        Initialize the 0DIN dataset loader.

        The 0DIN API does not support server-side filtering, so all filters are applied
        client-side after the full feed is fetched.

        Args:
            api_key: 0DIN API key. Falls back to the ``0DIN_API_KEY`` environment variable
                if not provided.
            severity: Keep only reports with this severity. Defaults to None (all severities).
            security_boundaries: Keep only reports whose security boundary is in this list.
                Defaults to None (all boundaries).
            categories: Keep only reports tagged with at least one of these taxonomy categories.
                Defaults to None (all categories).
            include_variant_prompts: Whether to additionally emit the industry-specific variant
                prompts attached to each report. Defaults to False (sample prompts only), since
                variants greatly increase the dataset size.

        Raises:
            ValueError: If an invalid severity, security boundary, or category is provided, or
                if a filter list is provided but empty (pass None to include all).
        """
        self._api_key = api_key

        if severity is not None:
            self._validate_enum(severity, ODINSeverity, "severity")

        if security_boundaries is not None:
            if not security_boundaries:
                raise ValueError(
                    "`security_boundaries` must be a non-empty list (pass None to include all security boundaries)"
                )
            self._validate_enums(security_boundaries, ODINSecurityBoundary, "security_boundary")

        if categories is not None:
            if not categories:
                raise ValueError("`categories` must be a non-empty list (pass None to include all categories)")
            self._validate_enums(categories, ODINTaxonomyCategory, "category")

        self._severity = severity
        self._security_boundaries = security_boundaries
        self._categories = categories
        self._include_variant_prompts = include_variant_prompts
        self.source = "https://0din.ai"

    @property
    @override
    def dataset_name(self) -> str:
        """The dataset name."""
        return "0din_threatfeed"

    def _resolve_api_key(self) -> str:
        """
        Resolve the 0DIN API key from the constructor argument or environment.

        Returns:
            str: The resolved API key.

        Raises:
            ValueError: If no API key is provided and ``0DIN_API_KEY`` is not set.
        """
        api_key = self._api_key or os.environ.get("0DIN_API_KEY")
        if not api_key:
            raise ValueError(
                "0DIN API key is required. Provide it via the 'api_key' parameter "
                "or set the 0DIN_API_KEY environment variable."
            )
        return api_key

    def _fetch_page(self, *, page: int, headers: dict[str, str]) -> dict[str, Any]:
        """
        Fetch a single page of the threat feed, retrying transient errors with backoff.

        Args:
            page: The 1-based page number to fetch.
            headers: Request headers including the Authorization key.

        Returns:
            dict[str, Any]: The parsed JSON body for the page.

        Raises:
            ConnectionError: If the request fails with a non-retryable status, or if all
                retries are exhausted on transient errors.
        """
        last_status: int | None = None
        last_text = ""
        for attempt in range(self.MAX_RETRIES):
            response = requests.get(
                self.API_BASE_URL,
                headers=headers,
                params={"page": page, "per_page": self.PAGE_SIZE},
                timeout=60,
            )

            if response.status_code == 200:
                return response.json()

            last_status = response.status_code
            last_text = response.text
            if response.status_code not in self._RETRYABLE_STATUS_CODES:
                break

            if attempt < self.MAX_RETRIES - 1:
                backoff = self.RETRY_BACKOFF_SECONDS * (attempt + 1)
                logger.warning(
                    f"0DIN API page {page} returned status {response.status_code}; "
                    f"retrying in {backoff:.0f}s (attempt {attempt + 1}/{self.MAX_RETRIES})."
                )
                time.sleep(backoff)

        raise ConnectionError(f"0DIN API request failed with status {last_status}: {last_text}")

    def _cache_path(self) -> Path:
        """
        Return the on-disk path of the cached raw threat feed.

        Returns:
            Path: The JSON cache file path under ``DB_DATA_PATH``.
        """
        return DB_DATA_PATH / "seed-prompt-entries" / self.CACHE_FILENAME

    def _load_cached_reports(self) -> list[dict[str, Any]]:
        """
        Load previously cached threat-feed reports from disk.

        Returns:
            list[dict[str, Any]]: The cached reports, or an empty list if no usable cache exists.
        """
        path = self._cache_path()
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"Ignoring unreadable 0DIN cache at {path}: {exc}")
            return []
        return data if isinstance(data, list) else []

    def _write_cached_reports(self, reports: list[dict[str, Any]]) -> None:
        """
        Persist the full set of threat-feed reports to disk.

        Args:
            reports: The complete (unfiltered) list of reports to cache.
        """
        path = self._cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as file:
                json.dump(reports, file, ensure_ascii=False)
        except OSError as exc:
            logger.warning(f"Failed to write 0DIN cache at {path}: {exc}")

    def _fetch_all_reports(self, *, cache: bool = True) -> list[dict[str, Any]]:
        """
        Fetch all threat-feed reports, incrementally syncing against the on-disk cache.

        The feed is returned newest-first, so when a cache exists this paginates from the
        first page and stops as soon as it encounters a report UUID already present in the
        cache — fetching only newly disclosed reports and merging them on top. When no cache
        exists (or ``cache`` is False) the full feed is fetched.

        Note: edits to already-cached reports (``updated_at`` changes) are not picked up by
        the incremental sync; pass ``cache=False`` to force a full refresh.

        Args:
            cache: Whether to read from and write to the on-disk cache. Defaults to True.

        Returns:
            list[dict[str, Any]]: All report records (newest-first).

        Raises:
            ValueError: If no API key is provided and ``0DIN_API_KEY`` is not set.
            ConnectionError: If an API request fails.
        """
        api_key = self._resolve_api_key()
        headers = {"Authorization": api_key}

        cached_reports = self._load_cached_reports() if cache else []
        cached_uuids = {r.get("uuid") for r in cached_reports if r.get("uuid")}

        new_reports: list[dict[str, Any]] = []
        reached_cache = False
        page = 1

        while not reached_cache:
            body = self._fetch_page(page=page, headers=headers)
            for report in body.get("threat_feeds", []):
                if report.get("uuid") in cached_uuids:
                    reached_cache = True
                    break
                new_reports.append(report)

            total_pages = body.get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1

        if not cache:
            return new_reports

        if not new_reports:
            return cached_reports

        # Merge newest-first, de-duplicating by UUID (newly fetched reports win).
        merged: list[dict[str, Any]] = []
        seen: set[Any] = set()
        for report in (*new_reports, *cached_reports):
            uuid = report.get("uuid")
            if uuid and uuid in seen:
                continue
            if uuid:
                seen.add(uuid)
            merged.append(report)

        self._write_cached_reports(merged)
        return merged

    def _matches_filters(self, report: dict[str, Any]) -> bool:
        """
        Determine whether a report satisfies the configured client-side filters.

        Args:
            report: A single threat-feed report record.

        Returns:
            bool: True if the report should be included.
        """
        if self._severity is not None and report.get("severity") != self._severity.value:
            return False

        if self._security_boundaries is not None:
            allowed = {b.value for b in self._security_boundaries}
            if report.get("security_boundary") not in allowed:
                return False

        if self._categories is not None:
            allowed_categories = {c.value for c in self._categories}
            report_categories = {t.get("category") for t in report.get("taxonomies") or []}
            if not (report_categories & allowed_categories):
                return False

        return True

    def _parse_datetime(self, date_str: str | None) -> datetime | None:
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

    def _build_metadata(
        self, report: dict[str, Any], *, extra: dict[str, str | int] | None = None
    ) -> dict[str, str | int]:
        """
        Build the metadata dict from a 0DIN report.

        Args:
            report: A single threat-feed report record.
            extra: Optional additional key/value pairs to merge in (e.g. variant info).

        Returns:
            dict[str, str | int]: Metadata dictionary with string or integer values.
        """
        metadata: dict[str, str | int] = {}

        if report.get("uuid"):
            metadata["uuid"] = report["uuid"]
        if report.get("severity"):
            metadata["severity"] = report["severity"]
        if report.get("security_boundary"):
            metadata["security_boundary"] = report["security_boundary"]
        if report.get("source"):
            metadata["report_source"] = report["source"]

        taxonomies = report.get("taxonomies") or []
        categories = sorted({t["category"] for t in taxonomies if t.get("category")})
        strategies = sorted({t["strategy"] for t in taxonomies if t.get("strategy")})
        techniques = sorted({t["technique"] for t in taxonomies if t.get("technique")})
        if categories:
            metadata["taxonomy_categories"] = ", ".join(categories)
        if strategies:
            metadata["taxonomy_strategies"] = ", ".join(strategies)
        if techniques:
            metadata["taxonomy_techniques"] = ", ".join(techniques)

        model_names = []
        for model in report.get("models") or []:
            name = model.get("name")
            if not name:
                continue
            vendor = (model.get("vendor") or {}).get("name")
            model_names.append(f"{vendor}: {name}" if vendor else name)
        if model_names:
            metadata["affected_models"] = ", ".join(model_names)

        for entry in report.get("metadata") or []:
            if entry.get("type") == "SocialImpact" and entry.get("result") is not None:
                metadata["social_impact"] = int(entry["result"])

        signatures = report.get("detection_signatures") or []
        if signatures and signatures[0].get("signature"):
            metadata["detection_signature"] = signatures[0]["signature"]

        if report.get("disclosed_at"):
            metadata["disclosed_at"] = report["disclosed_at"]

        if extra:
            metadata.update(extra)

        return metadata

    def _convert_report_to_seed_prompts(self, report: dict[str, Any]) -> list[SeedPrompt]:
        """
        Convert a single 0DIN report into one or more SeedPrompts.

        Sample prompts from ``messages`` are emitted first (de-duplicated by text). When
        ``include_variant_prompts`` is set, industry-specific variant prompts are appended.

        Args:
            report: A single threat-feed report record.

        Returns:
            list[SeedPrompt]: The seed prompts derived from this report.
        """
        title = report.get("title") or None
        uuid = report.get("uuid", "")
        summary = report.get("summary") or None
        taxonomies = report.get("taxonomies") or []
        harm_categories = sorted({t["category"] for t in taxonomies if t.get("category")}) or None
        date_added = self._parse_datetime(report.get("disclosed_at"))
        source_url = f"{self.REPORT_WEB_URL}/{uuid}" if uuid else self.source

        seeds: list[SeedPrompt] = []
        seen_prompts: set[str] = set()

        def _add_prompt(text: str, *, extra: dict[str, str | int] | None = None) -> None:
            if not text or text in seen_prompts:
                return
            seen_prompts.add(text)
            seeds.append(
                SeedPrompt(
                    value=text,
                    data_type="text",
                    name=title,
                    dataset_name=self.dataset_name,
                    harm_categories=harm_categories,
                    description=summary,
                    groups=["0DIN", "Mozilla"],
                    source=source_url,
                    date_added=date_added,
                    metadata=self._build_metadata(report, extra=extra),
                )
            )

        for message in report.get("messages") or []:
            _add_prompt(message.get("prompt", ""))

        if self._include_variant_prompts:
            for variant in report.get("variant_prompts") or []:
                industry = variant.get("industry")
                for subindustry in variant.get("subindustries") or []:
                    sub_name = subindustry.get("subindustry")
                    for prompt in subindustry.get("prompts") or []:
                        extra: dict[str, str | int] = {}
                        if industry:
                            extra["variant_industry"] = industry
                        if sub_name:
                            extra["variant_subindustry"] = sub_name
                        _add_prompt(prompt.get("prompt", ""), extra=extra)

        return seeds

    @override
    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Fetch reports from the 0DIN API and return them as a SeedDataset.

        Args:
            cache: Whether to use the on-disk cache. Defaults to True. When True, the raw feed
                is cached and subsequent calls only fetch newly disclosed reports (see
                ``_fetch_all_reports``). When False, the full feed is fetched fresh and the
                cache is neither read nor written.

        Returns:
            SeedDataset: A SeedDataset containing the fetched prompts.

        Raises:
            ValueError: If no API key is available or if the filters produce no seeds.
            ConnectionError: If an API request fails.
        """
        logger.info("Fetching reports from 0DIN threat feed API")

        reports = await asyncio.to_thread(self._fetch_all_reports, cache=cache)

        all_seeds: list[SeedUnion] = []
        for report in reports:
            if not self._matches_filters(report):
                continue
            all_seeds.extend(self._convert_report_to_seed_prompts(report))

        if not all_seeds:
            raise ValueError("SeedDataset cannot be empty. Check your filter criteria.")

        logger.info(f"Successfully loaded {len(all_seeds)} prompts from 0DIN")

        return SeedDataset(seeds=all_seeds, dataset_name=self.dataset_name)
