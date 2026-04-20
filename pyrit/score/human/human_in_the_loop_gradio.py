# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from typing import Optional

from pyrit.models import MessagePiece, Score
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_score_aggregator import (
    TrueFalseAggregatorFunc,
    TrueFalseScoreAggregator,
)
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class HumanInTheLoopScorerGradio(TrueFalseScorer):
    """
    Create scores from manual human input using Gradio and adds them to the database.

    In the future this will not be a TrueFalseScorer. However, it is all that is supported currently.
    """

    _default_validator: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["text"])

    def __init__(
        self,
        *,
        open_browser: bool = False,
        validator: Optional[ScorerPromptValidator] = None,
        score_aggregator: TrueFalseAggregatorFunc = TrueFalseScoreAggregator.OR,
    ) -> None:
        """
        Initialize the HumanInTheLoopScorerGradio.

        Args:
            open_browser (bool): If True, the scorer will open the Gradio interface in a browser
                instead of opening it in PyWebview. Defaults to False.
            validator (Optional[ScorerPromptValidator]): Custom validator. Defaults to None.
            score_aggregator (TrueFalseAggregatorFunc): Aggregator for combining scores. Defaults to
                TrueFalseScoreAggregator.OR.
        """
        # Import here to avoid importing rpyc in the main module that might not be installed
        from pyrit.ui.rpc import AppRPCServer

        super().__init__(validator=validator or self._default_validator, score_aggregator=score_aggregator)
        self._rpc_server = AppRPCServer(open_browser=open_browser)
        self._rpc_server.start()

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this scorer."""
        self._set_identifier(
            score_aggregator=self._score_aggregator.__name__,
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Score a message piece using human input through Gradio interface.

        Args:
            message_piece (MessagePiece): The message piece to be scored by a human.
            objective (Optional[str]): The objective to evaluate against. Defaults to None.

        Returns:
            list[Score]: A list containing a single Score object based on human evaluation.

        Raises:
            asyncio.CancelledError: If the scoring operation is cancelled.
        """
        try:
            score = await asyncio.to_thread(self.retrieve_score, message_piece, objective=objective)
            return score
        except asyncio.CancelledError:
            self._rpc_server.stop()
            raise

    def retrieve_score(self, request_prompt: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Retrieve a score from the human evaluator through the RPC server.

        Args:
            request_prompt (MessagePiece): The message piece to be scored.
            objective (Optional[str]): The objective to evaluate against. Defaults to None.

        Returns:
            list[Score]: A list containing a single Score object from the human evaluator.
        """
        self._rpc_server.wait_for_client()
        self._rpc_server.send_score_prompt(request_prompt)
        score = self._rpc_server.wait_for_score()
        score.scorer_class_identifier = self.get_identifier()
        return [score]

    def __del__(self) -> None:
        """Stop the RPC server when the scorer is deleted."""
        self._rpc_server.stop()
