# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import ValidationError

from pyrit.models.question_answering import (
    QuestionAnsweringDataset,
    QuestionAnsweringEntry,
    QuestionChoice,
)


def test_question_choice_init():
    choice = QuestionChoice(index=0, text="Option A")
    assert choice.index == 0
    assert choice.text == "Option A"


def test_question_choice_forbids_extra():
    with pytest.raises(ValidationError):
        QuestionChoice(index=0, text="A", extra="bad")


def test_question_answering_entry_init():
    choices = [QuestionChoice(index=0, text="A"), QuestionChoice(index=1, text="B")]
    entry = QuestionAnsweringEntry(
        question="What is 1+1?",
        answer_type="int",
        correct_answer=1,
        choices=choices,
    )
    assert entry.question == "What is 1+1?"
    assert entry.answer_type == "int"
    assert entry.correct_answer == 1
    assert len(entry.choices) == 2


def test_question_answering_entry_invalid_answer_type():
    with pytest.raises(ValidationError):
        QuestionAnsweringEntry(
            question="Q",
            answer_type="invalid",
            correct_answer=0,
            choices=[QuestionChoice(index=0, text="A")],
        )


def test_question_answering_entry_get_correct_answer_text():
    choices = [QuestionChoice(index=0, text="Paris"), QuestionChoice(index=1, text="London")]
    entry = QuestionAnsweringEntry(
        question="Capital of France?",
        answer_type="int",
        correct_answer=0,
        choices=choices,
    )
    assert entry.get_correct_answer_text() == "Paris"


def test_question_answering_entry_get_correct_answer_text_string_answer():
    choices = [QuestionChoice(index=0, text="Paris"), QuestionChoice(index=1, text="London")]
    entry = QuestionAnsweringEntry(
        question="Capital of France?",
        answer_type="str",
        correct_answer="0",
        choices=choices,
    )
    assert entry.get_correct_answer_text() == "Paris"


def test_question_answering_entry_get_correct_answer_text_no_match():
    choices = [QuestionChoice(index=0, text="A"), QuestionChoice(index=1, text="B")]
    entry = QuestionAnsweringEntry(
        question="Q",
        answer_type="int",
        correct_answer=99,
        choices=choices,
    )
    with pytest.raises(ValueError, match="No matching choice"):
        entry.get_correct_answer_text()


def test_question_answering_entry_hash():
    choices = [QuestionChoice(index=0, text="A")]
    entry1 = QuestionAnsweringEntry(question="Q", answer_type="str", correct_answer="A", choices=choices)
    entry2 = QuestionAnsweringEntry(question="Q", answer_type="str", correct_answer="A", choices=choices)
    assert hash(entry1) == hash(entry2)


def test_question_answering_entry_hash_different():
    choices = [QuestionChoice(index=0, text="A")]
    entry1 = QuestionAnsweringEntry(question="Q1", answer_type="str", correct_answer="A", choices=choices)
    entry2 = QuestionAnsweringEntry(question="Q2", answer_type="str", correct_answer="A", choices=choices)
    assert hash(entry1) != hash(entry2)


def test_question_answering_entry_forbids_extra():
    with pytest.raises(ValidationError):
        QuestionAnsweringEntry(
            question="Q",
            answer_type="str",
            correct_answer="A",
            choices=[],
            extra="bad",
        )


def test_question_answering_dataset_init():
    choices = [QuestionChoice(index=0, text="A")]
    entry = QuestionAnsweringEntry(question="Q", answer_type="str", correct_answer="A", choices=choices)
    dataset = QuestionAnsweringDataset(
        name="test_ds",
        version="1.0",
        description="test",
        author="tester",
        group="grp",
        source="src",
        questions=[entry],
    )
    assert dataset.name == "test_ds"
    assert dataset.version == "1.0"
    assert len(dataset.questions) == 1


def test_question_answering_dataset_defaults():
    choices = [QuestionChoice(index=0, text="A")]
    entry = QuestionAnsweringEntry(question="Q", answer_type="str", correct_answer="A", choices=choices)
    dataset = QuestionAnsweringDataset(questions=[entry])
    assert dataset.name == ""
    assert dataset.version == ""
    assert dataset.description == ""
    assert dataset.author == ""
    assert dataset.group == ""
    assert dataset.source == ""


def test_question_answering_dataset_forbids_extra():
    with pytest.raises(ValidationError):
        QuestionAnsweringDataset(questions=[], extra="bad")


@pytest.mark.parametrize("answer_type", ["int", "float", "str", "bool"])
def test_question_answering_entry_valid_answer_types(answer_type):
    choices = [QuestionChoice(index=0, text="A")]
    entry = QuestionAnsweringEntry(
        question="Q",
        answer_type=answer_type,
        correct_answer=0,
        choices=choices,
    )
    assert entry.answer_type == answer_type
