# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from unittest.mock import MagicMock, patch

import pytest

from pyrit.datasets.seed_datasets.remote.odin_dataset import (
    ODINSecurityBoundary,
    ODINSeverity,
    ODINTaxonomyCategory,
    _ODINDataset,
)
from pyrit.models import SeedDataset, SeedPrompt


@pytest.fixture
def api_key():
    """A fake API key for testing."""
    return "odin_test_key_000000000000000000000000000000000000000000000000"


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path):
    """Point the loader's on-disk cache at a per-test temp file so tests never touch real cache."""
    cache_file = tmp_path / "0din_threatfeed.json"
    with patch.object(_ODINDataset, "_cache_path", return_value=cache_file):
        yield cache_file


def _report(
    *,
    uuid,
    title="Sample Jailbreak",
    severity="low",
    security_boundary="guardrail_jailbreak",
    source="internal",
    prompts=("attack one", "attack two"),
    categories=("stratagems", "language"),
    variant_prompts=None,
):
    """Build a single threat-feed report record matching the 0DIN API schema."""
    # Each prompt is repeated across two "models" to mimic the real de-duplication scenario.
    messages = []
    for idx, prompt in enumerate(prompts):
        messages.append({"prompt": prompt, "response": "...", "model_id": idx, "interface": "api"})
        messages.append({"prompt": prompt, "response": "...", "model_id": idx + 100, "interface": "api"})

    taxonomies = [
        {"category": cat, "strategy": f"{cat}_strategy", "technique": f"{cat}_technique"} for cat in categories
    ]

    return {
        "uuid": uuid,
        "title": title,
        "summary": "A short summary.",
        "detail": "A long detail.",
        "severity": severity,
        "security_boundary": security_boundary,
        "source": source,
        "disclosed_at": "2026-06-15T14:54:11.981Z",
        "published_at": None,
        "updated_at": "2026-06-15T14:54:12.029Z",
        "detection_signatures": [{"version": "v1", "signature": f"sig-{uuid}"}],
        "models": [
            {"id": 1, "name": "Gemini 3 Flash", "vendor": {"name": "Google"}},
            {"id": 2, "name": "Command R", "vendor": {"name": "Cohere"}},
        ],
        "messages": messages,
        "taxonomies": taxonomies,
        "test_results": [{"result": 85.0, "temperature": 0.7, "model_id": 1, "test_type": {"id": 4, "name": "x"}}],
        "metadata": [{"type": "SocialImpact", "result": 4}],
        "reference_urls": [],
        "variant_prompts": variant_prompts or [],
    }


def _page(reports, *, page=1, total_pages=1, total_count=None):
    """Build a paginated list response."""
    return {
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count if total_count is not None else len(reports),
        "threat_feeds": reports,
    }


def _make_mock_response(*, json_data, status_code=200):
    """Create a mock requests.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.text = str(json_data)
    return mock_resp


@pytest.fixture
def single_page_response():
    """A one-page feed with two reports of differing severity/boundary/category."""
    return _page(
        [
            _report(
                uuid="11111111-1111-1111-1111-111111111111",
                title="Report A",
                severity="low",
                security_boundary="guardrail_jailbreak",
                prompts=("attack one", "attack two"),
                categories=("stratagems", "language"),
            ),
            _report(
                uuid="22222222-2222-2222-2222-222222222222",
                title="Report B",
                severity="high",
                security_boundary="prompt_injection",
                prompts=("attack three",),
                categories=("rhetoric",),
            ),
        ]
    )


class TestODINDatasetInit:
    """Test initialization and validation of _ODINDataset."""

    def test_init_with_api_key(self, api_key):
        loader = _ODINDataset(api_key=api_key)
        assert loader.dataset_name == "0din_threatfeed"
        assert loader._api_key == api_key

    def test_init_with_env_var(self, api_key):
        with patch.dict("os.environ", {"0DIN_API_KEY": api_key}):
            loader = _ODINDataset()
            assert loader._api_key is None  # env var resolved at fetch time

    def test_init_no_api_key_succeeds(self):
        with patch.dict("os.environ", {}, clear=True):
            loader = _ODINDataset()
            assert loader._api_key is None

    def test_init_invalid_severity_raises(self, api_key):
        with pytest.raises(ValueError, match="Expected ODINSeverity"):
            _ODINDataset(api_key=api_key, severity="low")

    def test_init_invalid_security_boundary_raises(self, api_key):
        with pytest.raises(ValueError, match="Expected ODINSecurityBoundary"):
            _ODINDataset(api_key=api_key, security_boundaries=["guardrail_jailbreak"])

    def test_init_invalid_category_raises(self, api_key):
        with pytest.raises(ValueError, match="Expected ODINTaxonomyCategory"):
            _ODINDataset(api_key=api_key, categories=["stratagems"])

    def test_init_empty_security_boundaries_raises(self, api_key):
        with pytest.raises(ValueError, match="`security_boundaries` must be a non-empty list"):
            _ODINDataset(api_key=api_key, security_boundaries=[])

    def test_init_empty_categories_raises(self, api_key):
        with pytest.raises(ValueError, match="`categories` must be a non-empty list"):
            _ODINDataset(api_key=api_key, categories=[])

    def test_init_accepts_valid_enums(self, api_key):
        loader = _ODINDataset(
            api_key=api_key,
            severity=ODINSeverity.HIGH,
            security_boundaries=[ODINSecurityBoundary.GUARDRAIL_JAILBREAK],
            categories=[ODINTaxonomyCategory.STRATAGEMS, ODINTaxonomyCategory.LANGUAGE],
        )
        assert loader._severity == ODINSeverity.HIGH
        assert loader._categories == [ODINTaxonomyCategory.STRATAGEMS, ODINTaxonomyCategory.LANGUAGE]

    def test_dataset_name(self, api_key):
        assert _ODINDataset(api_key=api_key).dataset_name == "0din_threatfeed"


class TestODINDatasetFetch:
    """Test fetch_dataset_async and data transformation."""

    async def test_fetch_no_api_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            loader = _ODINDataset()
            with pytest.raises(ValueError, match="API key is required"):
                await loader.fetch_dataset_async()

    async def test_fetch_returns_seed_dataset(self, api_key, single_page_response):
        loader = _ODINDataset(api_key=api_key)
        mock_resp = _make_mock_response(json_data=single_page_response)

        with patch("requests.get", return_value=mock_resp):
            dataset = await loader.fetch_dataset_async()

        assert isinstance(dataset, SeedDataset)
        # Report A: 2 unique prompts (de-duplicated from 4 messages); Report B: 1 prompt -> 3 total
        assert len(dataset.seeds) == 3

    async def test_deduplicates_message_prompts(self, api_key):
        report = _report(
            uuid="33333333-3333-3333-3333-333333333333",
            prompts=("only attack",),  # repeated across two models -> 2 messages
        )
        loader = _ODINDataset(api_key=api_key)
        mock_resp = _make_mock_response(json_data=_page([report]))

        with patch("requests.get", return_value=mock_resp):
            dataset = await loader.fetch_dataset_async()

        assert len(dataset.seeds) == 1
        assert dataset.seeds[0].value == "only attack"

    async def test_seed_prompt_fields(self, api_key, single_page_response):
        loader = _ODINDataset(api_key=api_key)
        mock_resp = _make_mock_response(json_data=single_page_response)

        with patch("requests.get", return_value=mock_resp):
            dataset = await loader.fetch_dataset_async()

        prompts = [s for s in dataset.seeds if isinstance(s, SeedPrompt)]
        first = prompts[0]
        assert first.data_type == "text"
        assert first.dataset_name == "0din_threatfeed"
        assert first.name == "Report A"
        assert first.description == "A short summary."
        assert first.harm_categories == ["language", "stratagems"]
        assert first.groups == ["0DIN", "Mozilla"]
        assert first.source == "https://0din.ai/threatfeed/11111111-1111-1111-1111-111111111111"

    async def test_seed_prompt_metadata(self, api_key, single_page_response):
        loader = _ODINDataset(api_key=api_key)
        mock_resp = _make_mock_response(json_data=single_page_response)

        with patch("requests.get", return_value=mock_resp):
            dataset = await loader.fetch_dataset_async()

        first = next(s for s in dataset.seeds if s.name == "Report A")
        assert first.metadata["uuid"] == "11111111-1111-1111-1111-111111111111"
        assert first.metadata["severity"] == "low"
        assert first.metadata["security_boundary"] == "guardrail_jailbreak"
        assert first.metadata["report_source"] == "internal"
        assert first.metadata["taxonomy_categories"] == "language, stratagems"
        assert "Google: Gemini 3 Flash" in first.metadata["affected_models"]
        assert first.metadata["social_impact"] == 4
        assert first.metadata["detection_signature"] == "sig-11111111-1111-1111-1111-111111111111"

    async def test_value_preserved_verbatim(self, api_key):
        # Jinja-like syntax must not be rendered for untrusted remote text.
        report = _report(uuid="44444444-4444-4444-4444-444444444444", prompts=("{{ 7 * 7 }} literal",))
        loader = _ODINDataset(api_key=api_key)
        mock_resp = _make_mock_response(json_data=_page([report]))

        with patch("requests.get", return_value=mock_resp):
            dataset = await loader.fetch_dataset_async()

        assert dataset.seeds[0].value == "{{ 7 * 7 }} literal"


class TestODINDatasetFilters:
    """Test client-side filtering."""

    async def test_severity_filter(self, api_key, single_page_response):
        loader = _ODINDataset(api_key=api_key, severity=ODINSeverity.HIGH)
        mock_resp = _make_mock_response(json_data=single_page_response)

        with patch("requests.get", return_value=mock_resp):
            dataset = await loader.fetch_dataset_async()

        # Only Report B (high) survives -> its single prompt
        assert len(dataset.seeds) == 1
        assert dataset.seeds[0].name == "Report B"

    async def test_security_boundary_filter(self, api_key, single_page_response):
        loader = _ODINDataset(
            api_key=api_key,
            security_boundaries=[ODINSecurityBoundary.PROMPT_INJECTION],
        )
        mock_resp = _make_mock_response(json_data=single_page_response)

        with patch("requests.get", return_value=mock_resp):
            dataset = await loader.fetch_dataset_async()

        assert {s.name for s in dataset.seeds} == {"Report B"}

    async def test_category_filter(self, api_key, single_page_response):
        loader = _ODINDataset(api_key=api_key, categories=[ODINTaxonomyCategory.RHETORIC])
        mock_resp = _make_mock_response(json_data=single_page_response)

        with patch("requests.get", return_value=mock_resp):
            dataset = await loader.fetch_dataset_async()

        assert {s.name for s in dataset.seeds} == {"Report B"}

    async def test_filter_empty_result_raises(self, api_key, single_page_response):
        loader = _ODINDataset(api_key=api_key, severity=ODINSeverity.SEVERE)
        mock_resp = _make_mock_response(json_data=single_page_response)

        with patch("requests.get", return_value=mock_resp):
            with pytest.raises(ValueError, match="SeedDataset cannot be empty"):
                await loader.fetch_dataset_async()


class TestODINDatasetVariants:
    """Test variant-prompt inclusion."""

    @staticmethod
    def _report_with_variants(uuid):
        return _report(
            uuid=uuid,
            prompts=("primary attack",),
            variant_prompts=[
                {
                    "industry": "automotive",
                    "subindustries": [
                        {
                            "subindustry": "autonomous_driving",
                            "industry_id": 2,
                            "prompts": [
                                {"prompt": "variant a", "key_changes": "...", "rationale": "..."},
                                {"prompt": "variant b", "key_changes": "...", "rationale": "..."},
                            ],
                        }
                    ],
                }
            ],
        )

    async def test_variants_excluded_by_default(self, api_key):
        report = self._report_with_variants("55555555-5555-5555-5555-555555555555")
        loader = _ODINDataset(api_key=api_key)
        mock_resp = _make_mock_response(json_data=_page([report]))

        with patch("requests.get", return_value=mock_resp):
            dataset = await loader.fetch_dataset_async()

        assert {s.value for s in dataset.seeds} == {"primary attack"}

    async def test_variants_included_when_requested(self, api_key):
        report = self._report_with_variants("66666666-6666-6666-6666-666666666666")
        loader = _ODINDataset(api_key=api_key, include_variant_prompts=True)
        mock_resp = _make_mock_response(json_data=_page([report]))

        with patch("requests.get", return_value=mock_resp):
            dataset = await loader.fetch_dataset_async()

        values = {s.value for s in dataset.seeds}
        assert values == {"primary attack", "variant a", "variant b"}

        variant = next(s for s in dataset.seeds if s.value == "variant a")
        assert variant.metadata["variant_industry"] == "automotive"
        assert variant.metadata["variant_subindustry"] == "autonomous_driving"


class TestODINDatasetPagination:
    """Test pagination handling."""

    async def test_fetches_all_pages(self, api_key):
        page1 = _page(
            [_report(uuid="aaaaaaaa-0000-0000-0000-000000000001", prompts=("p1",))],
            page=1,
            total_pages=2,
            total_count=2,
        )
        page2 = _page(
            [_report(uuid="aaaaaaaa-0000-0000-0000-000000000002", prompts=("p2",))],
            page=2,
            total_pages=2,
            total_count=2,
        )
        loader = _ODINDataset(api_key=api_key)
        responses = [_make_mock_response(json_data=page1), _make_mock_response(json_data=page2)]

        with patch("requests.get", side_effect=responses) as mock_get:
            dataset = await loader.fetch_dataset_async()

        assert mock_get.call_count == 2
        assert {s.value for s in dataset.seeds} == {"p1", "p2"}

    async def test_auth_header_has_no_bearer_prefix(self, api_key, single_page_response):
        loader = _ODINDataset(api_key=api_key)
        mock_resp = _make_mock_response(json_data=single_page_response)

        with patch("requests.get", return_value=mock_resp) as mock_get:
            await loader.fetch_dataset_async()

        assert mock_get.call_args.kwargs["headers"]["Authorization"] == api_key


class TestODINDatasetAPIErrors:
    """Test error handling for API failures."""

    async def test_api_401_raises_immediately(self, api_key):
        loader = _ODINDataset(api_key=api_key)
        mock_resp = _make_mock_response(json_data={"error": "unauthorized"}, status_code=401)

        with patch("requests.get", return_value=mock_resp) as mock_get:
            with pytest.raises(ConnectionError, match="status 401"):
                await loader.fetch_dataset_async()

        # 401 is not retryable -> exactly one request
        assert mock_get.call_count == 1

    async def test_api_500_retries_then_raises(self, api_key):
        loader = _ODINDataset(api_key=api_key)
        mock_resp = _make_mock_response(json_data={"error": "server"}, status_code=500)

        with patch("time.sleep"):
            with patch("requests.get", return_value=mock_resp) as mock_get:
                with pytest.raises(ConnectionError, match="status 500"):
                    await loader.fetch_dataset_async()

        assert mock_get.call_count == _ODINDataset.MAX_RETRIES

    async def test_transient_error_then_success(self, api_key, single_page_response):
        loader = _ODINDataset(api_key=api_key)
        responses = [
            _make_mock_response(json_data={"error": "bad gateway"}, status_code=502),
            _make_mock_response(json_data=single_page_response),
        ]

        with patch("time.sleep"):
            with patch("requests.get", side_effect=responses) as mock_get:
                dataset = await loader.fetch_dataset_async()

        assert mock_get.call_count == 2
        assert len(dataset.seeds) == 3


class TestODINDatasetCaching:
    """Test the incremental on-disk cache."""

    async def test_first_fetch_writes_cache(self, api_key, isolate_cache):
        loader = _ODINDataset(api_key=api_key)
        report = _report(uuid="cache-0000-0000-0000-000000000001", prompts=("p1",))
        mock_resp = _make_mock_response(json_data=_page([report]))

        assert not isolate_cache.exists()
        with patch("requests.get", return_value=mock_resp):
            await loader.fetch_dataset_async()

        assert isolate_cache.exists()
        cached = json.loads(isolate_cache.read_text(encoding="utf-8"))
        assert [r["uuid"] for r in cached] == ["cache-0000-0000-0000-000000000001"]

    async def test_no_new_reports_single_request(self, api_key):
        loader = _ODINDataset(api_key=api_key)
        report = _report(uuid="cache-0000-0000-0000-000000000001", prompts=("p1",))

        # First fetch populates the cache.
        with patch("requests.get", return_value=_make_mock_response(json_data=_page([report]))):
            await loader.fetch_dataset_async()

        # Second fetch: page 1's first UUID is already cached -> stop after one request.
        with patch("requests.get", return_value=_make_mock_response(json_data=_page([report]))) as mock_get:
            dataset = await loader.fetch_dataset_async()

        assert mock_get.call_count == 1
        assert {s.value for s in dataset.seeds} == {"p1"}

    async def test_incremental_fetch_only_pulls_new_reports(self, api_key, isolate_cache):
        loader = _ODINDataset(api_key=api_key)
        old = _report(uuid="cache-0000-0000-0000-00000000000A", prompts=("old",))
        with patch("requests.get", return_value=_make_mock_response(json_data=_page([old]))):
            await loader.fetch_dataset_async()

        # Feed now returns a new report on top of the known one (newest-first).
        new = _report(uuid="cache-0000-0000-0000-00000000000B", prompts=("new",))
        feed = _page([new, old])
        with patch("requests.get", return_value=_make_mock_response(json_data=feed)) as mock_get:
            dataset = await loader.fetch_dataset_async()

        assert mock_get.call_count == 1
        # Both old and new prompts are present, new merged on top.
        assert {s.value for s in dataset.seeds} == {"new", "old"}
        cached = json.loads(isolate_cache.read_text(encoding="utf-8"))
        assert [r["uuid"] for r in cached] == [
            "cache-0000-0000-0000-00000000000B",
            "cache-0000-0000-0000-00000000000A",
        ]

    async def test_cache_false_bypasses_cache(self, api_key, isolate_cache):
        loader = _ODINDataset(api_key=api_key)
        report = _report(uuid="cache-0000-0000-0000-000000000001", prompts=("p1",))

        with patch("requests.get", return_value=_make_mock_response(json_data=_page([report]))):
            await loader.fetch_dataset_async(cache=True)
        cached_mtime = isolate_cache.stat().st_mtime_ns

        # cache=False must not read or write the cache, and must fully paginate.
        page1 = _page([report], page=1, total_pages=2, total_count=2)
        other = _report(uuid="cache-0000-0000-0000-000000000002", prompts=("p2",))
        page2 = _page([other], page=2, total_pages=2, total_count=2)
        responses = [_make_mock_response(json_data=page1), _make_mock_response(json_data=page2)]
        with patch("requests.get", side_effect=responses) as mock_get:
            dataset = await loader.fetch_dataset_async(cache=False)

        assert mock_get.call_count == 2  # full pagination, cache ignored
        assert {s.value for s in dataset.seeds} == {"p1", "p2"}
        assert isolate_cache.stat().st_mtime_ns == cached_mtime  # cache untouched

    async def test_corrupt_cache_is_ignored(self, api_key, isolate_cache):
        isolate_cache.parent.mkdir(parents=True, exist_ok=True)
        isolate_cache.write_text("{ not json", encoding="utf-8")

        loader = _ODINDataset(api_key=api_key)
        report = _report(uuid="cache-0000-0000-0000-000000000001", prompts=("p1",))
        with patch("requests.get", return_value=_make_mock_response(json_data=_page([report]))):
            dataset = await loader.fetch_dataset_async()

        assert {s.value for s in dataset.seeds} == {"p1"}
        cached = json.loads(isolate_cache.read_text(encoding="utf-8"))
        assert [r["uuid"] for r in cached] == ["cache-0000-0000-0000-000000000001"]
