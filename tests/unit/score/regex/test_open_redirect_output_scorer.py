# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory
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
