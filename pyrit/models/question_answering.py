# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict


class QuestionChoice(BaseModel):
    """
    Represents a choice for a question.
    """

    model_config = ConfigDict(extra="forbid")
    index: int
    text: str


class QuestionAnsweringEntry(BaseModel):
    """
    Represents a question model.
    """

    model_config = ConfigDict(extra="forbid")
    question: str
    answer_type: Literal["int", "float", "str", "bool"]
    correct_answer: Union[int, str, float]
    choices: list[QuestionChoice]

    def get_correct_answer_text(self) -> str:
        """
        Get the text of the correct answer.

        Returns:
            str: Text corresponding to the configured correct answer index.

        Raises:
            ValueError: If no choice matches the configured correct answer.

        """
        correct_answer_index = self.correct_answer
        try:
            # Match using the explicit choice.index (not enumerate position) so non-sequential indices are supported
            return next(choice for choice in self.choices if str(choice.index) == str(correct_answer_index)).text
        except StopIteration as e:
            raise ValueError(
                f"No matching choice found for correct_answer '{correct_answer_index}'. "
                f"Available choices are: {[f'{i}: {c.text}' for i, c in enumerate(self.choices)]}"
            ) from e

    def __hash__(self) -> int:
        """
        Return a stable hash for this question entry.

        Returns:
            int: Hash computed from serialized model content.

        """
        return hash(self.model_dump_json())


class QuestionAnsweringDataset(BaseModel):
    """
    Represents a dataset for question answering.
    """

    model_config = ConfigDict(extra="forbid")
    name: str = ""
    version: str = ""
    description: str = ""
    author: str = ""
    group: str = ""
    source: str = ""
    questions: list[QuestionAnsweringEntry]
