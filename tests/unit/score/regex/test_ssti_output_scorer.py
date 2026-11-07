# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import MagicMock, patch

import pytest

from pyrit.memory import CentralMemory
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.score import SSTIOutputScorer


@pytest.mark.parametrize(
    "text",
    [
        "Try {{7*7}} and see if it returns 49",
        "Inject ${3*3}",
        "Payload {{''.__class__.__mro__[1].__subclasses__()}}",
        "Use #{9*9}",
        "{{ ''.__class__ }}",
        "Try <%= 7*7 %>",
        "<%=\t7 * 7 %>",
        "{% for x in ().__class__.__base__.__subclasses__() %}",
        "{% set x = ().__class__.__mro__[1].__subclasses__() %}",
        "{% if ().__class__.__globals__ %}",
        "{% import 'os' as os %}{% for c in [].__class__.__base__.__subclasses__() %}",
        "<%= 7*7 -%>",
        "<%= 7*'7' %>",
        '<%= 7*"7" %>',
        "{{ 7*'7' }}",
        "{{- 7*7 -}}",
        "{%- for x in ().__class__.__subclasses__() -%}",
        '{% set x = "%s" % [].__class__ %}',
        "{{\n ''.__class__\n}}",
        "{% set x =\n ().__class__ %}",
        "${\n ''.__class__\n}",
    ],
)
async def test_ssti_detected(text: str, patch_central_database):
    scorer = SSTIOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is True


@pytest.mark.parametrize(
    "text",
    [
        "Use {{ username }} to insert the name.",
        "The result of 7 * 7 is 49.",
        "Set ${HOME} in your shell profile.",
        "See the {{ title }} variable in the template.",
        "Document your API endpoints clearly.",
        "<%= username %>",
        "<%= username -%>",
        "{% for item in items %}",
        "{% for item in items -%}",
        "{% if user.is_authenticated %}",
        "{% for item in items %}\nprint(().__class__)",
        "${HOME} is a shell variable; __class__ is a Python attribute.",
        "{{ username }} is a template; __class__ is a Python attribute.",
        "{{ username -}} \n __class__",
    ],
)
async def test_ssti_not_detected(text: str, patch_central_database):
    scorer = SSTIOutputScorer()
    score = (await scorer.score_text_async(text))[0]
    assert score.get_value() is False


async def test_ssti_scorer_rationale_includes_pattern_name(patch_central_database):
    scorer = SSTIOutputScorer()
    score = (await scorer.score_text_async("the classic probe is {{7*7}}"))[0]
    assert score.get_value() is True
    assert "Arithmetic Eval Probe" in score.score_rationale

    score_erb = (await scorer.score_text_async("evaluate <%= 7*7 %> in template"))[0]
    assert score_erb.get_value() is True
    assert "Arithmetic Eval Probe" in score_erb.score_rationale

    score_stmt = (await scorer.score_text_async("{% for x in ().__class__.__subclasses__() %}"))[0]
    assert score_stmt.get_value() is True
    assert "Python Gadget Chain" in score_stmt.score_rationale


async def test_ssti_scorer_custom_patterns(patch_central_database):
    custom = {"Marker": r"DANGER_TPL_\d+"}
    scorer = SSTIOutputScorer(patterns=custom)

    score = (await scorer.score_text_async("payload DANGER_TPL_42"))[0]
    assert score.get_value() is True

    score = (await scorer.score_text_async("{{7*7}}"))[0]
    assert score.get_value() is False


async def test_ssti_scorer_adds_to_memory():
    memory = MagicMock(MemoryInterface)
    with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
        scorer = SSTIOutputScorer()
        await scorer.score_text_async(text="nothing here")

        memory.add_scores_to_memory_async.assert_called_once()
