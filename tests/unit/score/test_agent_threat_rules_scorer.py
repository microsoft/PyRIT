# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from unittest.mock import MagicMock, patch

import pytest

from pyrit.score.true_false.regex.agent_threat_rules_scorer import (
    SUPPORTED_DIGEST_SCHEMA,
    AgentThreatRulesScorer,
    _patterns_from_digest,
)

_MODULE = "pyrit.score.true_false.regex.agent_threat_rules_scorer"


def _digest(**overrides):
    """A minimal digest in the shape ATR's exporter publishes."""
    base = {
        "schema": SUPPORTED_DIGEST_SCHEMA,
        "atr_version": "4.0.0",
        "atr_commit": "54d3e13e94f8980d7b36f9d79511b26174954dfc",
        "default_fields": ["agent_output", "content"],
        "rules_seen": 3,
        "rules_emitted": 3,
        "conditions_by_field": {"content": 2, "tool_response": 1},
        "conditions": {
            "ATR-2026-00030#0": {
                "rule_id": "ATR-2026-00030",
                "pattern": r"(?i)ignore\s+(?:all\s+)?previous\s+instructions",
                "field": "content",
                "category": "prompt-injection",
            },
            "ATR-2026-00031#0": {
                "rule_id": "ATR-2026-00031",
                "pattern": r"(?i)speaking\s+as\s+the\s+admin\s+agent",
                "field": "agent_output",
                "category": "agent-manipulation",
            },
            "ATR-2026-00032#0": {
                "rule_id": "ATR-2026-00032",
                "pattern": r"(?i)exfiltrate\s+the\s+system\s+prompt",
                "field": "tool_response",
                "category": "context-exfiltration",
            },
        },
        "excluded": {},
    }
    base.update(overrides)
    return base


@pytest.fixture
def offline_digest():
    """Serve the fixture digest without touching the network or the cache."""
    payload = json.dumps(_digest()).encode("utf-8")
    response = MagicMock()
    response.read.return_value = payload
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    with patch(f"{_MODULE}.urllib.request.urlopen", return_value=response) as urlopen:
        yield urlopen


class TestFieldSelection:
    """A scorer sees unlabelled text, so field selection decides what it loads."""

    def test_defaults_to_the_digests_default_fields(self):
        patterns = _patterns_from_digest(_digest())
        assert set(patterns) == {"ATR-2026-00030#0", "ATR-2026-00031#0"}

    def test_explicit_fields_override_the_default(self):
        patterns = _patterns_from_digest(_digest(), fields=["tool_response"])
        assert set(patterns) == {"ATR-2026-00032#0"}

    def test_condition_keys_keep_a_match_traceable_to_its_rule(self):
        patterns = _patterns_from_digest(_digest(), fields=["content"])
        assert all(name.startswith("ATR-") and "#" in name for name in patterns)

    def test_a_digest_without_default_fields_and_no_request_is_an_error(self):
        with pytest.raises(ValueError, match="no fields requested|default_fields"):
            _patterns_from_digest(_digest(default_fields=[]))


class TestDigestValidation:
    """An ATR-side regression must fail loudly here, not score silently less."""

    def test_unsupported_schema_is_rejected(self, offline_digest):
        payload = json.dumps(_digest(schema=SUPPORTED_DIGEST_SCHEMA + 1)).encode("utf-8")
        offline_digest.return_value.read.return_value = payload
        with pytest.raises(ValueError, match="schema"):
            AgentThreatRulesScorer(cache=False)

    def test_a_pattern_that_does_not_compile_is_rejected_at_construction(self, offline_digest):
        broken = _digest()
        broken["conditions"]["ATR-2026-00030#0"]["pattern"] = r"(?i)unclosed[group"
        offline_digest.return_value.read.return_value = json.dumps(broken).encode("utf-8")
        with pytest.raises(ValueError, match="does not compile"):
            AgentThreatRulesScorer(cache=False)

    def test_an_empty_digest_is_rejected(self, offline_digest):
        offline_digest.return_value.read.return_value = json.dumps(_digest(conditions={})).encode("utf-8")
        with pytest.raises(ValueError, match="no conditions"):
            AgentThreatRulesScorer(cache=False)

    def test_requesting_a_field_the_digest_has_none_of_names_what_is_available(self, offline_digest):
        with pytest.raises(ValueError, match="tool_name"):
            AgentThreatRulesScorer(fields=["tool_name"], cache=False)


class TestConstruction:
    def test_loads_only_default_field_patterns(self, offline_digest):
        scorer = AgentThreatRulesScorer(cache=False)
        assert len(scorer._patterns) == 2
        assert scorer._atr_version == "4.0.0"

    def test_pins_to_a_commit_by_default(self, offline_digest):
        AgentThreatRulesScorer(cache=False)
        url = offline_digest.call_args[0][0]
        assert "54d3e13e94f8980d7b36f9d79511b26174954dfc" in url
        assert url.startswith("https://raw.githubusercontent.com/Agent-Threat-Rule/agent-threat-rules/")

    def test_an_explicit_ref_is_honoured(self, offline_digest):
        AgentThreatRulesScorer(ref="main", cache=False)
        assert "/main/data/pyrit-digest.json" in offline_digest.call_args[0][0]

    def test_categories_default_to_agent_threat(self, offline_digest):
        scorer = AgentThreatRulesScorer(cache=False)
        assert scorer._score_categories == ["agent_threat"]

    def test_categories_can_be_overridden(self, offline_digest):
        scorer = AgentThreatRulesScorer(categories=["custom"], cache=False)
        assert scorer._score_categories == ["custom"]

    def test_a_fetch_failure_names_the_url(self):
        with patch(f"{_MODULE}.urllib.request.urlopen", side_effect=OSError("no route to host")):
            with pytest.raises(ValueError, match="Could not fetch the ATR digest"):
                AgentThreatRulesScorer(cache=False)


class TestCaching:
    def test_a_cached_digest_is_used_without_refetching(self, offline_digest, tmp_path):
        cache_file = tmp_path / "pyrit-digest-cached.json"
        cache_file.write_text(json.dumps(_digest()), encoding="utf-8")
        with patch(f"{_MODULE}._cache_path", return_value=cache_file):
            AgentThreatRulesScorer(cache=True)
        offline_digest.assert_not_called()

    def test_a_corrupt_cache_entry_falls_back_to_fetching(self, offline_digest, tmp_path):
        cache_file = tmp_path / "pyrit-digest-corrupt.json"
        cache_file.write_text("{ not json", encoding="utf-8")
        with patch(f"{_MODULE}._cache_path", return_value=cache_file):
            scorer = AgentThreatRulesScorer(cache=True)
        offline_digest.assert_called_once()
        assert len(scorer._patterns) == 2

    def test_the_fetched_digest_is_written_to_cache(self, offline_digest, tmp_path):
        cache_file = tmp_path / "nested" / "pyrit-digest.json"
        with patch(f"{_MODULE}._cache_path", return_value=cache_file):
            AgentThreatRulesScorer(cache=True)
        assert json.loads(cache_file.read_text(encoding="utf-8"))["atr_version"] == "4.0.0"


class TestCachePath:
    def test_a_branch_name_cannot_escape_the_cache_directory(self):
        from pyrit.score.true_false.regex.agent_threat_rules_scorer import _cache_path

        path = _cache_path("../../etc/passwd")
        assert ".." not in path.parts
        assert path.name.startswith("pyrit-digest-")
