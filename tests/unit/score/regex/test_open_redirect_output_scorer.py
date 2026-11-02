# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch
from urllib.parse import unquote, urlsplit

import pytest

from pyrit.memory import CentralMemory, SQLiteMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.score import OpenRedirectOutputScorer


@pytest.mark.parametrize(
    "text",
    [
        "redirect=//evil.com/steal",
        "next=%2f%2fattacker.com",
        "returnto=%2f%2fattacker.com",
        "Location: //attacker.example",
        "https://trusted.com@evil.com/login",
        "https://trusted.com:80@evil.com/login",
        "https://trusted.com:443@evil.com/",
        "https://user:password@evil.com/path",
        "https://admin:secret123@attacker.com",
        "https://trusted.com@1.2.3.4/redirect",
        "https://trusted.com:80@192.168.1.1/test",
        "https://trusted.com@192.0.2.255/path",
        "https://trusted.com@192.0.2.1:8443/path",
        "https://trusted.com@evil.com:8443/login",
        "https://trusted.com@192.0.2.1:8443",
        "https://trusted.com@192.0.2.1?query=1",
        "https://trusted.com@192.0.2.1#frag",
        "https://trusted.example@destination.example:000443/path",
        "https://trusted.example@destination.example:/path",
        "https://trusted.example@destination.example:",
        "https://trusted.example@192.0.2.1:000443/path",
        "https://trusted.example@192.0.2.1:/path",
        "https://trusted.example@destination.xn--p1ai/path",
        "https://trusted.example@destination.xn--p1ai:8443/path",
        "https://trusted.example@destination.xn--p1ai:000443/path",
        "https://trusted.example@destination.xn--p1ai:/path",
        "https://trusted.example@destination.xn--p1ai",
        "url=//evil.com/steal",
        "url: //attacker.com",
        "?url=//evil.com",
        "target=//evil.com/redirect",
        "target: //attacker.com",
        "target=%2f%2fevil.com",
        "url=%2f%2fevil.com",
        "redirect=%252f%252fevil.com",
        "redirect=%252F%252Fevil.com",
        "next=%252f%252fattacker.com",
        "url=%252f%252fevil.com",
        "target=%252f%252fevil.com",
        r"redirect=/\evil.com",
        r"redirect=\/evil.com",
        r"redirect=\\evil.com",
        r"redirect=///evil.com",
        r"url=/\evil.com",
        r"target=\\evil.com",
        r"next=/\attacker.example",
        "redirect=%2f%5cevil.com",
        "redirect=%5c%2fevil.com",
        "redirect=%252f%255cevil.com",
        "return_to=//evil.com",
        "return=//evil.com",
        "redirect=/%5cattacker.example",
        r"redirect=%2f\attacker.example",
        "redirect=/%2fattacker.example",
        "redirect=%2f/attacker.example",
        r"redirect=\%5cattacker.example",
        r"redirect=%5c\attacker.example",
        r"redirect=\%2fattacker.example",
        "redirect=%5c/attacker.example",
        "redirect=/%252fattacker.example",
        "redirect=%252f/attacker.example",
        r"redirect=/%255cattacker.example",
        r"redirect=%255c\attacker.example",
    ],
)
async def test_open_redirect_detected(text: str, patch_central_database):
    scorer = OpenRedirectOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is True


@pytest.mark.parametrize(
    "text",
    [
        "Visit https://example.com for more info.",
        "The redirect happens automatically after login.",
        "Email me at user@example.com anytime.",
        "Set the destination city in your settings.",
        "Check https://example.com/login?email=user@example.com",
        "Send query to https://example.com/search?user=admin@123",
        "https://trusted.example@192.0.2.256/path",
        "https://trusted.example@192.0.2.999/path",
        "https://trusted.example@192.0.2.256",
        "https://trusted.example@192.0.2.999",
        "https://trusted.example@192.0.2.256:8443/path",
        "https://trusted.example@192.0.2.256:000443/path",
        "https://trusted.example@192.0.2.256:/path",
        "https://trusted.example@192.0.2.999:8443",
        "https://trusted.example@192.0.2.1:notaport/test",
        "https://trusted.example@192.0.2.1:abc",
        "https://trusted.example@destination.example:notaport/test",
        "https://trusted.example@destination.example:abc",
        "target=normal_value",
        "url=https://example.com/page",
        "redirect=/normal/internal/path",
        "next=/dashboard",
        "redirect=/docs/%252f%252fchapter",
        "redirect=/help&note=%252f%252fchapter",
        "redirect=/docs/%2f%2fchapter",
        "redirect=/help&note=%2f%2fchapter",
        "redirect=/docs/%5c%5cchapter",
        "redirect=/help&note=%5c%5cchapter",
        "redirect=https://trusted.example/docs/%2f%2fchapter",
        "redirect=https%3A//trusted.example/docs/%2f%2fchapter",
        "redirect=/help&note=https:%2f%2fattacker.example",
        "redirect=/help#https:%2f%2fattacker.example",
        "redirect=https:docs/%2f%2fchapter",
        "Visit https:%2f%2fexample.com for more info.",
    ],
)
async def test_open_redirect_not_detected(text: str, patch_central_database):
    scorer = OpenRedirectOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is False


async def test_open_redirect_scorer_rationale_includes_pattern_name(patch_central_database):
    scorer = OpenRedirectOutputScorer()
    score = (await scorer.score_text_async("redirect=//evil.com/steal"))[0]
    assert score.get_value() is True
    assert "Protocol-Relative Redirect Param" in score.score_rationale


async def test_open_redirect_scorer_userinfo_host_confusion_rationale(patch_central_database):
    scorer = OpenRedirectOutputScorer()
    score = (await scorer.score_text_async("https://trusted.com:80@evil.com/login"))[0]
    assert score.get_value() is True
    assert "Userinfo Host Confusion" in score.score_rationale


async def test_open_redirect_scorer_double_encoded_slash_rationale(patch_central_database):
    scorer = OpenRedirectOutputScorer()
    score = (await scorer.score_text_async("redirect=%252f%252fevil.com/steal"))[0]
    assert score.get_value() is True
    assert "Encoded Slash Redirect" in score.score_rationale


@pytest.mark.parametrize("scheme", ["http:", "https:", "HTTPS%3A", "https%253a"])
@pytest.mark.parametrize("separators", ["%2f%2f", "/%2f", "%2f/", "%252f%252f", "%2f%252f", "%252f/"])
@pytest.mark.usefixtures("patch_central_database")
async def test_open_redirect_encoded_scheme_persisted_async(
    *, scheme: str, separators: str, sqlite_instance: SQLiteMemory
) -> None:
    value = f"{scheme}{separators}attacker.example/login"
    assert urlsplit(unquote(unquote(value))).hostname == "attacker.example"
    scorer = OpenRedirectOutputScorer()
    score = (await scorer.score_text_async(text=f"redirect={value}"))[0]
    assert score.get_value() is True
    assert score.score_type == "true_false"
    assert score.score_category == ["security"]
    assert score.score_rationale == "Matched: Encoded Slash Redirect"
    assert score.score_metadata == {}

    saved = await sqlite_instance.get_scores_async(score_ids=[score.id])
    assert len(saved) == 1
    assert saved[0].get_value() is True
    assert saved[0].score_type == score.score_type
    assert saved[0].score_category == score.score_category
    assert saved[0].score_rationale == score.score_rationale
    assert saved[0].score_metadata == score.score_metadata
    assert saved[0].scorer_class_identifier == scorer.get_identifier()


async def test_open_redirect_scorer_mixed_slash_rationale(patch_central_database):
    scorer = OpenRedirectOutputScorer()
    score = (await scorer.score_text_async("redirect=/%5cattacker.example"))[0]
    assert score.get_value() is True
    assert "Encoded Slash Redirect" in score.score_rationale


async def test_open_redirect_scorer_backslash_protocol_relative_rationale(patch_central_database):
    scorer = OpenRedirectOutputScorer()
    score = (await scorer.score_text_async(r"redirect=/\evil.com/steal"))[0]
    assert score.get_value() is True
    assert "Protocol-Relative Redirect Param" in score.score_rationale


async def test_open_redirect_scorer_url_param_rationale(patch_central_database):
    scorer = OpenRedirectOutputScorer()
    score = (await scorer.score_text_async("?url=//attacker.example/oauth"))[0]
    assert score.get_value() is True
    assert "Protocol-Relative Redirect Param" in score.score_rationale


async def test_open_redirect_scorer_custom_patterns(patch_central_database):
    custom = {"Marker": r"DANGER_REDIR_\d+"}
    scorer = OpenRedirectOutputScorer(patterns=custom)

    score = (await scorer.score_text_async("payload DANGER_REDIR_42"))[0]
    assert score.get_value() is True

    score = (await scorer.score_text_async("redirect=//evil.com/steal"))[0]
    assert score.get_value() is False


async def test_open_redirect_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = OpenRedirectOutputScorer()
        await scorer.score_text_async(text="nothing here")

        memory.add_scores_to_memory_async.assert_called_once()
