# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import csv
import json
import sys
from pathlib import Path
from typing import IO, Optional

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target.common.prompt_target import PromptTarget
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration


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
        custom_configuration: Optional[TargetConfiguration] = None,
        custom_capabilities: Optional[TargetCapabilities] = None,
    ) -> None:
        """
        Initialize the TextTarget.

        Args:
            text_stream (IO[str]): The text stream to write prompts to. Defaults to sys.stdout.
            custom_configuration (TargetConfiguration, Optional): Override the default configuration for
                this target instance. Defaults to None.
            custom_capabilities (TargetCapabilities, Optional): **Deprecated.** Use
                ``custom_configuration`` instead. Will be removed in v0.14.0.
        """
        super().__init__(custom_configuration=custom_configuration, custom_capabilities=custom_capabilities)
        self._text_stream = text_stream

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        """
        Asynchronously write a message to the text stream.

        Args:
            normalized_conversation (list[Message]): The full conversation
                (history + current message) after running the normalization
                pipeline. The current message is the last element.

        Returns:
            list[Message]: An empty list (no response expected).
        """
        message = normalized_conversation[-1]

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
                    role=row["role"],  # type: ignore[ty:invalid-argument-type]
                    original_value=row["value"],
                    original_value_data_type=row.get("data_type", None),  # type: ignore[ty:invalid-argument-type]
                    conversation_id=row.get("conversation_id", None),
                    sequence=int(sequence_str) if sequence_str else 0,
                    labels=labels,  # deprecated
                    response_error=row.get("response_error", None),  # type: ignore[ty:invalid-argument-type]
                    prompt_target_identifier=self.get_identifier(),
                )
                message_pieces.append(message_piece)

        # This is post validation, so the message_pieces should be okay and normalized
        self._memory.add_message_pieces_to_memory(message_pieces=message_pieces)
        return message_pieces

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        pass

    async def cleanup_target(self) -> None:
        """Target does not require cleanup."""
