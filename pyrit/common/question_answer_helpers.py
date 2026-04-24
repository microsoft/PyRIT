# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.models import QuestionAnsweringEntry


def construct_evaluation_prompt(entry: QuestionAnsweringEntry) -> str:
    """
    From question and choices in entry, creates prompt to be send to target.

    Args:
        entry (QuestionAnsweringEntry): A single entry from which the prompt is constructed.

    Returns:
        str: The constructed prompt string.
    """
    available_choices = "\n".join(f"index={c.index}, value={c.text}" for c in entry.choices)
    return f"Question:\n{entry.question}\n\nChoices:\n{available_choices}"
