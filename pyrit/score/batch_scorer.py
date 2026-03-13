# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Optional

from pyrit.memory import CentralMemory
from pyrit.models import (
    Message,
    MessagePiece,
    Score,
    group_message_pieces_into_conversations,
)
from pyrit.score.scorer import Scorer

logger = logging.getLogger(__name__)


class BatchScorer:
    """
    A utility class for scoring prompts in batches in a parallelizable and convenient way.

    This class provides functionality to score existing prompts stored in memory
    without any target interaction, making it a pure scoring utility.
    """

    def __init__(
        self,
        *,
        batch_size: int = 10,
    ) -> None:
        """
        Initialize the BatchScorer.

        Args:
            batch_size (int): The (max) batch size for sending prompts. Defaults to 10.
                Note: If using a scorer that takes a prompt target, and providing max requests per
                minute on the target, this should be set to 1 to ensure proper rate limit management.
        """
        self._memory = CentralMemory.get_memory_instance()
        self._batch_size = batch_size

    async def score_responses_by_filters_async(
        self,
        *,
        scorer: Scorer,
        attack_id: Optional[str | uuid.UUID] = None,
        conversation_id: Optional[str | uuid.UUID] = None,
        prompt_ids: Optional[list[str] | list[uuid.UUID]] = None,
        labels: Optional[dict[str, str]] = None,
        sent_after: Optional[datetime] = None,
        sent_before: Optional[datetime] = None,
        original_values: Optional[list[str]] = None,
        converted_values: Optional[list[str]] = None,
        data_type: Optional[str] = None,
        not_data_type: Optional[str] = None,
        converted_value_sha256: Optional[list[str]] = None,
        objective: str = "",
    ) -> list[Score]:
        """
        Score the responses that match the specified filters.

        Args:
            scorer (Scorer): The Scorer object to use for scoring.
            attack_id (Optional[str | uuid.UUID]): The ID of the attack. Defaults to None.
            conversation_id (Optional[str | uuid.UUID]): The ID of the conversation. Defaults to None.
            prompt_ids (Optional[list[str] | list[uuid.UUID]]): A list of prompt IDs. Defaults to None.
            labels (Optional[dict[str, str]]): A dictionary of labels. Defaults to None.
            sent_after (Optional[datetime]): Filter for prompts sent after this datetime. Defaults to None.
            sent_before (Optional[datetime]): Filter for prompts sent before this datetime. Defaults to None.
            original_values (Optional[list[str]]): A list of original values. Defaults to None.
            converted_values (Optional[list[str]]): A list of converted values. Defaults to None.
            data_type (Optional[str]): The data type to filter by. Defaults to None.
            not_data_type (Optional[str]): The data type to exclude. Defaults to None.
            converted_value_sha256 (Optional[list[str]]): A list of SHA256 hashes of converted values.
                Defaults to None.
            objective (str): A task is used to give the scorer more context on what exactly to score.
                A task might be the request prompt text or the original attack model's objective.
                **Note: the same task is applied to all matched prompts.** Defaults to an empty string.

        Returns:
            list[Score]: A list of Score objects for responses that match the specified filters.

        Raises:
            ValueError: If no entries match the provided filters.
        """
        message_pieces: Sequence[MessagePiece] = []
        message_pieces = self._memory.get_message_pieces(
            attack_id=attack_id,
            conversation_id=conversation_id,
            prompt_ids=prompt_ids,
            labels=labels,
            sent_after=sent_after,
            sent_before=sent_before,
            original_values=original_values,
            converted_values=converted_values,
            data_type=data_type,
            not_data_type=not_data_type,
            converted_value_sha256=converted_value_sha256,
        )

        if not message_pieces:
            raise ValueError("No entries match the provided filters. Please check your filters.")

        # Group pieces by conversation
        conversations = group_message_pieces_into_conversations(message_pieces)

        # Flatten all conversations into a single list of responses
        responses: list[Message] = []
        for conversation in conversations:
            responses.extend(conversation)

        return await scorer.score_prompts_batch_async(
            messages=responses, objectives=[objective] * len(responses), batch_size=self._batch_size
        )

    def _remove_duplicates(self, messages: Sequence[MessagePiece]) -> list[MessagePiece]:
        """
        Remove duplicates from the list of MessagePiece objects.

        This ensures that identical prompts are not scored twice by filtering
        to only include original prompts (where original_prompt_id == id).

        Args:
            messages (Sequence[MessagePiece]): The request responses to deduplicate.

        Returns:
            list[MessagePiece]: A list with duplicates removed.
        """
        return [response for response in messages if response.original_prompt_id == response.id]
