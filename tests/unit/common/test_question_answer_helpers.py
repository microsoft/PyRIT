# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.common.question_answer_helpers import construct_evaluation_prompt
from pyrit.models import QuestionAnsweringEntry, QuestionChoice


def test_construct_evaluation_prompt_basic():
    entry = QuestionAnsweringEntry(
        question="What color is the sky?",
        answer_type="str",
        correct_answer="blue",
        choices=[
            QuestionChoice(index=0, text="red"),
            QuestionChoice(index=1, text="blue"),
        ],
    )
    result = construct_evaluation_prompt(entry)
    assert "What color is the sky?" in result
    assert "index=0, value=red" in result
    assert "index=1, value=blue" in result


def test_construct_evaluation_prompt_single_choice():
    entry = QuestionAnsweringEntry(
        question="Is 1+1=2?",
        answer_type="bool",
        correct_answer="True",
        choices=[QuestionChoice(index=0, text="True")],
    )
    result = construct_evaluation_prompt(entry)
    assert "Question:" in result
    assert "Choices:" in result
    assert "index=0, value=True" in result


def test_construct_evaluation_prompt_format():
    entry = QuestionAnsweringEntry(
        question="Pick a number",
        answer_type="int",
        correct_answer=2,
        choices=[
            QuestionChoice(index=0, text="1"),
            QuestionChoice(index=1, text="2"),
            QuestionChoice(index=2, text="3"),
        ],
    )
    result = construct_evaluation_prompt(entry)
    lines = result.split("\n")
    assert lines[0] == "Question:"
    assert lines[1] == "Pick a number"
    assert lines[3] == "Choices:"
