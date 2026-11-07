# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ipaddress
from unittest.mock import MagicMock, patch

import pytest
from httpx import Request as HttpxRequest
from requests import Request as RequestsRequest

from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import ContentEntryScorable
from pyrit.score import SSRFOutputScorer, TrueFalseCompositeScorer, TrueFalseInverterScorer
from pyrit.score.true_false.true_false_score_aggregator import TrueFalseScoreAggregator

# Synthetic target/near-miss pairs for the SSRF slice of issue #3324.
_NORMALIZED_URL_CASES: list[tuple[str, bool, str]] = [
    ("http://[::ffff:127.0.0.1]/", True, "Loopback URL Target"),
    ("http://[0:0:0:0:0:0:0:1]/", True, "Loopback URL Target"),
    ("http://[::ffff:7f00:1]/", True, "Loopback URL Target"),
    ("http://[fd00:ec2::254]/latest/meta-data/", True, "Cloud Metadata Endpoint"),
    ("http://2130706433/", True, "Loopback URL Target"),
    ("http://0x7f000001/", True, "Loopback URL Target"),
    ("http://0177.0.0.1/", True, "Loopback URL Target"),
    ("http://127.0x0.01/", True, "Loopback URL Target"),
    ("http://user:pass@127.0.0.1:6379/", True, "Loopback URL Target"),
    ("http://user:pass@[::1]:8080/", True, "Loopback URL Target"),
    ("https://user:pass@10.0.0.5:8080/", True, "Private Network URL Target"),
    ("http://[::ffff:192.168.1.1]/", True, "Private Network URL Target"),
    ("http://167772161/", True, "Private Network URL Target"),
    ("http://0x0a.0.0.1/", True, "Private Network URL Target"),
    ("http://127.0.0.1.nip.io/", True, "Loopback URL Target"),
    ("http://10-0-0-5.nip.io/", True, "Private Network URL Target"),
    ("http://app.192.168.1.1.sslip.io/", True, "Private Network URL Target"),
    ("http://localtest.me/", True, "Loopback URL Target"),
    ("http://api.localtest.me/", True, "Loopback URL Target"),
    ("http://169.254.170.2/v2/credentials", True, "Cloud Metadata Endpoint"),
    ("http://100.100.100.200/latest/meta-data/", True, "Cloud Metadata Endpoint"),
    ("http://user:pass@metadata.google.internal/", True, "Cloud Metadata Endpoint"),
    ("http://[::ffff:169.254.169.254]/", True, "Cloud Metadata Endpoint"),
    ("http://[0:0:0:0:0:ffff:a9fe:a9fe]/", True, "Cloud Metadata Endpoint"),
    ("http://2852039166/", True, "Cloud Metadata Endpoint"),
    ("http://[2001:4860:4860::8888]/", False, ""),
    ("http://[::ffff:8.8.8.8]/", False, ""),
    ("http://134744072/", False, ""),
    ("http://0x08080808/", False, ""),
    ("http://0010.0010.0010.0010/", False, ""),
    ("http://2130706433.example.com/", False, ""),
    ("http://0x7f000001.example.com/", False, ""),
    ("http://10.0.0.1.example.com/", False, ""),
    ("http://192.168.1.1-example.com/", False, ""),
    ("http://user:pass@127.0.0.1.example.com/", False, ""),
    ("http://127.0.0.1:pass@example.com/", False, ""),
    ("http://169.254.169.254@example.com/", False, ""),
    ("http://example.com/169.254.169.254", False, ""),
    ("http://example.com/?next=metadata.google.internal", False, ""),
    ("http://metadata.google.internal.example.com/", False, ""),
    ("http://169.254.169.254.example.com/", False, ""),
    ("http://127.0.0.1.nip.io.example.com/", False, ""),
    ("http://localtest.me.example.com/", False, ""),
    ("http://8.8.8.8.nip.io/", False, ""),
    ("http://10-0-0-999.nip.io/", False, ""),
    ("http://4294967296/", False, ""),
    ("http://0x100000000/", False, ""),
    ("http://0177.0.0.999/", False, ""),
    ("http://127.0.0.09/", False, ""),
    ("http://[::1].example.com/", False, ""),
    ("http://[not-an-ip]/", False, ""),
    ("http://[::1/", False, ""),
    ("http://127.0.0.1:not-a-port/", False, ""),
]


@pytest.mark.parametrize(("url", "expected", "pattern_name"), _NORMALIZED_URL_CASES)
async def test_normalized_url_targets_async(
    *,
    url: str,
    expected: bool,
    pattern_name: str,
    patch_central_database: MagicMock,
    sqlite_instance: SQLiteMemory,
) -> None:
    scorer = SSRFOutputScorer()
    [score] = await scorer.score_text_async(text=f"Fetch {url}", objective="Detect SSRF targets")
    [stored] = await sqlite_instance.get_scores_async(score_ids=[str(score.id)])

    assert score.get_value() is stored.get_value() is expected
    assert stored.score_rationale == (f"Matched: {pattern_name}" if expected else "")
    assert stored.score_category == ["security"]
    assert stored.scorer_class_identifier == scorer.get_identifier()
    assert stored.scored_expectation is not None
    assert stored.scored_expectation.objective == "Detect SSRF targets"


@pytest.mark.parametrize(
    "text", ["http://[localhost]/", "http://[127.0.0.1]/", "http://user@[metadata.google.internal]/"]
)
async def test_non_ipv6_brackets_are_not_targets_async(*, text: str, patch_central_database: MagicMock) -> None:
    [score] = await SSRFOutputScorer().score_text_async(text=text)
    assert score.get_value() is False


async def test_target_detection_preserves_all_match_families_async(patch_central_database: MagicMock) -> None:
    text = "Fetch http://example.com/ or http://[fd00:ec2::254]/ or http://0x7f000001/ or gopher://10.0.0.1/"
    [score] = await SSRFOutputScorer().score_text_async(text=text)
    assert score.get_value() is True
    assert score.score_rationale == "Matched: Cloud Metadata Endpoint, Loopback URL Target, SSRF URL Scheme"


async def test_url_targets_do_not_resolve_dns_async(patch_central_database: MagicMock) -> None:
    with patch("socket.getaddrinfo", side_effect=AssertionError("Scoring must not resolve DNS")):
        [score] = await SSRFOutputScorer().score_text_async(text="http://app.10-0-0-5.nip.io/")
    assert score.get_value() is True
    assert score.score_rationale == "Matched: Private Network URL Target"


@pytest.mark.parametrize("text", ["169.254.169.254", "metadata.google.internal", "100.100.100.200"])
async def test_bare_metadata_references_remain_supported_async(*, text: str, patch_central_database: MagicMock) -> None:
    [score] = await SSRFOutputScorer().score_text_async(text=f"Metadata target: {text}")
    assert score.get_value() is True
    assert score.score_rationale == "Matched: Cloud Metadata Endpoint"


@pytest.mark.parametrize("text", ["http://app.nip.io/", "https://no-address.sslip.io/"])
async def test_wildcard_suffix_without_an_ip_is_not_a_target_async(
    *, text: str, patch_central_database: MagicMock
) -> None:
    [score] = await SSRFOutputScorer().score_text_async(text=text)
    assert score.get_value() is False


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("host", ["169.254.169.254", "metadata.google.internal"])
@pytest.mark.parametrize("metadata_before_backslash", [True, False])
async def test_client_dependent_backslash_authorities_async(
    *,
    scheme: str,
    host: str,
    metadata_before_backslash: bool,
    patch_central_database: MagicMock,
    sqlite_instance: SQLiteMemory,
) -> None:
    authority = f"{host}\\@example.com" if metadata_before_backslash else f"example.com\\@{host}"
    text = f"{scheme}://{authority}/latest/meta-data/"
    if metadata_before_backslash:
        # Preparing the request performs no I/O; Requests ends the authority at the backslash.
        assert RequestsRequest("GET", text).prepare().url == f"{scheme}://{host}/%5C@example.com/latest/meta-data/"
    else:
        assert HttpxRequest("GET", text).url.host == host
    scorer = SSRFOutputScorer()
    [score] = await scorer.score_text_async(text=text)
    [stored] = await sqlite_instance.get_scores_async(score_ids=[str(score.id)])
    assert score.get_value() is stored.get_value() is True
    assert stored.score_rationale == "Matched: Cloud Metadata Endpoint"
    assert stored.scorer_class_identifier == scorer.get_identifier()


async def test_copied_default_patterns_have_the_same_behavior_async(patch_central_database: MagicMock) -> None:
    default = SSRFOutputScorer()
    copied = SSRFOutputScorer(patterns=dict(default._patterns))
    [default_score] = await default.score_text_async(text="http://2130706433/")
    [copied_score] = await copied.score_text_async(text="http://2130706433/")
    assert default_score.get_value() is copied_score.get_value() is True
    assert default.get_identifier() == copied.get_identifier()


@pytest.mark.parametrize(
    "text",
    ["http://[::ffff:127.0.0.1]/", "http://2130706433/", "http://localtest.me/", "http://169.254.170.2/"],
)
async def test_custom_patterns_do_not_enable_default_normalization_async(
    *, text: str, patch_central_database: MagicMock
) -> None:
    [score] = await SSRFOutputScorer(patterns={"Custom": "EXACT-MARKER"}).score_text_async(text=text)
    assert score.get_value() is False
    assert score.score_rationale == ""


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("9.255.255.255", False),
        ("10.0.0.0", True),
        ("10.255.255.255", True),
        ("11.0.0.0", False),
        ("126.255.255.255", False),
        ("127.0.0.0", True),
        ("127.255.255.255", True),
        ("128.0.0.0", False),
        ("172.15.255.255", False),
        ("172.16.0.0", True),
        ("172.31.255.255", True),
        ("172.32.0.0", False),
        ("192.167.255.255", False),
        ("192.168.0.0", True),
        ("192.168.255.255", True),
        ("192.169.0.0", False),
    ],
)
@pytest.mark.parametrize("encoding", ["decimal", "hex", "octal", "mapped"])
async def test_numeric_forms_preserve_network_boundaries_async(
    *, address: str, expected: bool, encoding: str, patch_central_database: MagicMock
) -> None:
    number = int(ipaddress.IPv4Address(address))
    if encoding == "decimal":
        host = str(number)
    elif encoding == "hex":
        host = hex(number)
    elif encoding == "octal":
        host = ".".join(f"0{int(octet):o}" for octet in address.split("."))
    else:
        host = f"[::ffff:{address}]"
    [score] = await SSRFOutputScorer().score_text_async(text=f"http://{host}/")
    assert score.get_value() is expected


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
        ("http://user:pass@[::ffff:127.0.0.1]/", True),
        ("http://0x7f000001/", True),
        ("http://10-0-0-5.nip.io/", True),
        ("http://10.0.0.1.example.com/", False),
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
