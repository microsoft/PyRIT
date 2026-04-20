# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import csv
import json
import sys
from pathlib import Path
from typing import IO

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.common.prompt_target import PromptTarget


class TextTarget(PromptTarget):
    """
    The TextTarget takes prompts, adds them to memory and writes them to io
    which is sys.stdout by default.

    This can be useful in various situations, for example, if operators want to generate prompts
    but enter them manually.
    """

    def __init__(
        self,
        *,
        text_stream: IO[str] = sys.stdout,
    ) -> None:
        """
        Initialize the TextTarget.

        Args:
            text_stream (IO[str]): The text stream to write prompts to. Defaults to sys.stdout.
        """
        super().__init__()
        self._text_stream = text_stream

    async def send_prompt_async(self, *, message: Message) -> list[Message]:
        """
        Asynchronously write a message to the text stream.

        Args:
            message (Message): The message object to write to the stream.

        Returns:
            list[Message]: An empty list (no response expected).
        """
        self._validate_request(message=message)

        self._text_stream.write(f"{str(message)}\n")
        self._text_stream.flush()

        return []

    def import_scores_from_csv(self, csv_file_path: Path) -> list[MessagePiece]:
        """
        Import message pieces and their scores from a CSV file.

        Args:
            csv_file_path (Path): The path to the CSV file containing scores.

        Returns:
            list[MessagePiece]: A list of message pieces imported from the CSV.
        """
        message_pieces = []

        with open(csv_file_path, newline="") as csvfile:
            csvreader = csv.DictReader(csvfile)

            for row in csvreader:
                sequence_str = row.get("sequence", None)
                labels_str = row.get("labels", None)
                labels = json.loads(labels_str) if labels_str else None

                message_piece = MessagePiece(
                    role=row["role"],  # type: ignore
                    original_value=row["value"],
                    original_value_data_type=row.get["data_type", None],  # type: ignore
                    conversation_id=row.get("conversation_id", None),
                    sequence=int(sequence_str) if sequence_str else None,
                    labels=labels,
                    response_error=row.get("response_error", None),  # type: ignore
                    prompt_target_identifier=self.get_identifier(),
                )
                message_pieces.append(message_piece)

        # This is post validation, so the message_pieces should be okay and normalized
        self._memory.add_message_pieces_to_memory(message_pieces=message_pieces)
        return message_pieces

    def _validate_request(self, *, message: Message) -> None:
        pass

    async def cleanup_target(self) -> None:
        """Target does not require cleanup."""
        pass
