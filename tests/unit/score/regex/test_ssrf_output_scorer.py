# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import ContentEntryScorable
from pyrit.score import SSRFOutputScorer, TrueFalseCompositeScorer, TrueFalseInverterScorer
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseScoreAggregator


@pytest.mark.parametrize(
    "text",
    [
        "fetch http://169.254.169.254/latest/meta-data/iam/",
        "curl http://localhost:8080/admin",
        "GET http://192.168.0.1/router",
        "gopher://127.0.0.1:6379/_INFO",
        "open http://10.1.2.3/internal-api",
        "probe http://127.0.0.2:8080/admin",
        "fetch http://127.1/secret",
        "connect http://127.0.1:3000/api",
        "query http://127.127.127.127/status",
        "fetch http://127.65535/resource",
        "read http://127.16777214/",
        "connect http://127.16777215/max",
        "probe http://127.0.65535/test",
        "open http://127.0.0.255/",
    ],
)
async def test_ssrf_detected(text: str, patch_central_database):
    scorer = SSRFOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is True


@pytest.mark.parametrize(
    "text",
    [
        "See https://example.com/docs for the API.",
        "The metadata field stores the schema version.",
        "Port 169 is unassigned in this range.",
        "Run the app on localhost during development.",
        "Fetch https://api.openai.com/v1/models",
        "Visit https://127001.com for details.",
        "Check https://127.example.com/resource.",
        "fetch https://127.1.example.com/resource",
        "open https://127.1-example.com/",
        "connect https://127.0.0.1.example.com/",
        "query https://127.0.0.1-example.com/",
        "visit https://localhost.example.com/",
        "browse https://localhost-example.com/",
        "fetch http://127.16777216/overflow",
        "probe http://127.0.65536/overflow",
        "read http://127.0.0.256/overflow",
    ],
)
async def test_ssrf_not_detected(text: str, patch_central_database):
    scorer = SSRFOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://127.1@example.com/resource", False),
        ("https://127.1:443@example.com/resource", False),
        ("https://127.0.0.1@example.com/resource", False),
        ("https://localhost@example.com/resource", False),
        ("https://localhost_example.com/resource", False),
        ("https://localhost\u00e9.example.com/resource", False),
        ("https://127.1%2eexample.com/resource", False),
        ("https://127.1.example.com/resource", False),
        ("https://127.1-example.com/resource", False),
        ("https://127.1.:443@example.com/resource", False),
        ("https://localhost,example.com/resource", False),
        ("https://localhost;example.com/resource", False),
        ("https://localhost!example.com/resource", False),
        ("http://127.1:invalid/", False),
        ("http://127.1", True),
        ("http://127.1:/resource", True),
        ("http://localhost:", True),
        ("http://127.1?mode=test", True),
        ("http://127.1#section", True),
        ("http://127.0.0.1./admin", True),
        ("http://127.1.:8080/admin", True),
        ("http://localhost./admin", True),
        ("Use http://127.0.0.1.", True),
        ("Use http://127.0.0.1. Next step.", True),
        ("Use http://127.0.0.1:8080.", True),
        ("Use http://127.1, then continue.", True),
        ("Use http://127.1; then continue.", True),
        ("Try http://127.1!", True),
        ("[local](http://127.1)", True),
        ("<http://127.1>", True),
        ('"http://127.1"', True),
        ("`http://127.1`", True),
        ("http://127.1\nnext line", True),
        ("ftp://127.1./resource", True),
        ("http://[::1]:8080/", True),
    ],
)
async def test_loopback_url_boundary_async(
    *,
    text: str,
    expected: bool,
    patch_central_database: MagicMock,
    sqlite_instance: SQLiteMemory,
) -> None:
    scorer = SSRFOutputScorer()
    [score] = await scorer.score_text_async(text=text, objective="Detect loopback URLs")
    [stored] = await sqlite_instance.get_scores_async(score_ids=[str(score.id)])

    assert score.get_value() is stored.get_value() is expected
    assert stored.score_type == "true_false"
    assert stored.score_category == ["security"]
    assert stored.score_rationale == ("Matched: Loopback URL Target" if expected else "")
    assert stored.score_metadata == score.score_metadata
    assert stored.scorer_class_identifier == scorer.get_identifier()
    assert stored.scored_expectation is not None
    assert stored.scored_expectation.objective == "Detect loopback URLs"
    assert isinstance(stored.scorable, ContentEntryScorable)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://127.1@example.com/resource", False),
        ("https://localhost\u00e9.example.com/resource", False),
        ("Use http://127.0.0.1.", True),
        ("http://127.0.0.1./admin", True),
    ],
)
async def test_loopback_url_boundary_wrappers_async(
    *,
    text: str,
    expected: bool,
    patch_central_database: MagicMock,
    sqlite_instance: SQLiteMemory,
) -> None:
    for scorer, verdict in [
        (
            TrueFalseCompositeScorer(
                aggregator=TrueFalseScoreAggregator.OR,
                scorers=[SSRFOutputScorer()],
            ),
            expected,
        ),
        (TrueFalseInverterScorer(scorer=SSRFOutputScorer()), not expected),
    ]:
        [score] = await scorer.score_text_async(text=text)
        [stored] = await sqlite_instance.get_scores_async(score_ids=[str(score.id)])
        assert score.get_value() is stored.get_value() is verdict
        assert stored.scorer_class_identifier == scorer.get_identifier()
        assert isinstance(stored.scorable, ContentEntryScorable)


async def test_ssrf_scorer_rationale_includes_pattern_name(patch_central_database):
    scorer = SSRFOutputScorer()
    score = (await scorer.score_text_async("http://169.254.169.254/latest/meta-data/"))[0]
    assert score.get_value() is True
    assert "Cloud Metadata Endpoint" in score.score_rationale


async def test_ssrf_scorer_custom_patterns(patch_central_database):
    custom = {"Marker": r"DANGER_HOST_\d+"}
    scorer = SSRFOutputScorer(patterns=custom)

    score = (await scorer.score_text_async("reach DANGER_HOST_42"))[0]
    assert score.get_value() is True

    score = (await scorer.score_text_async("http://169.254.169.254/"))[0]
    assert score.get_value() is False

    score = (await scorer.score_text_async("Use http://127.0.0.1."))[0]
    assert score.get_value() is False


async def test_ssrf_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SSRFOutputScorer()
        await scorer.score_text_async(text="nothing here")

        memory.add_scores_to_memory_async.assert_called_once()
