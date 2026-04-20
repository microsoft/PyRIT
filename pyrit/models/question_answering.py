# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict


class QuestionChoice(BaseModel):
    """
    Represents a choice for a question.

    Parameters:
        index (int): The index of the choice.
        text (str): The text of the choice.
    """

    model_config = ConfigDict(extra="forbid")
    index: int
    text: str


class QuestionAnsweringEntry(BaseModel):
    """
    Represents a question model.

    Parameters:
        question (str): The question text.
        answer_type (Literal["int", "float", "str", "bool"]): The type of the answer.
            `int` for integer answers (e.g., when the answer is an index of the correct option in a multiple-choice
            question).
            `float` for answers that are floating-point numbers.
            `str` for text-based answers.
            `bool` for boolean answers.
        correct_answer (Union[int, str, float]): The correct answer.
        choices (list[QuestionChoice]): The list of choices for the question.
    """

    model_config = ConfigDict(extra="forbid")
    question: str
    answer_type: Literal["int", "float", "str", "bool"]
    correct_answer: Union[int, str, float]
    choices: list[QuestionChoice]

    def get_correct_answer_text(self) -> str:
        """Get the text of the correct answer."""
        correct_answer_index = self.correct_answer
        try:
            # Match using the explicit choice.index (not enumerate position) so non-sequential indices are supported
            return next(choice for choice in self.choices if str(choice.index) == str(correct_answer_index)).text
        except StopIteration:
            raise ValueError(
                f"No matching choice found for correct_answer '{correct_answer_index}'. "
                f"Available choices are: {[f'{i}: {c.text}' for i, c in enumerate(self.choices)]}"
            )

    def __hash__(self) -> int:
        return hash(self.model_dump_json())


class QuestionAnsweringDataset(BaseModel):
    """
    Represents a dataset for question answering.

    Parameters:
        name (str): The name of the dataset.
        version (str): The version of the dataset.
        description (str): A description of the dataset.
        author (str): The author of the dataset.
        group (str): The group associated with the dataset.
        source (str): The source of the dataset.
        questions (list[QuestionAnsweringEntry]): A list of question models.
    """

    model_config = ConfigDict(extra="forbid")
    name: str = ""
    version: str = ""
    description: str = ""
    author: str = ""
    group: str = ""
    source: str = ""
    questions: list[QuestionAnsweringEntry]
