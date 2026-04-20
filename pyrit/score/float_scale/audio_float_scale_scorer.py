# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

from pyrit.identifiers import ComponentIdentifier
from pyrit.models import MessagePiece, Score
from pyrit.score.audio_transcript_scorer import AudioTranscriptHelper
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator


class AudioFloatScaleScorer(FloatScaleScorer):
    """
    A scorer that processes audio files by transcribing them and scoring the transcript.

    The AudioFloatScaleScorer transcribes audio to text using Azure Speech-to-Text,
    then scores the transcript using a FloatScaleScorer.
    """

    _default_validator: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["audio_path"])

    def __init__(
        self,
        *,
        text_capable_scorer: FloatScaleScorer,
        validator: Optional[ScorerPromptValidator] = None,
        use_entra_auth: Optional[bool] = None,
    ) -> None:
        """
        Initialize the AudioFloatScaleScorer.

        Args:
            text_capable_scorer: A FloatScaleScorer capable of processing text.
                This scorer will be used to evaluate the transcribed audio content.
            validator: Validator for the scorer. Defaults to audio_path data type validator.
            use_entra_auth: Whether to use Entra ID authentication for Azure Speech.
                Defaults to True if None.

        Raises:
            ValueError: If text_capable_scorer does not support text data type.
        """
        super().__init__(validator=validator or self._default_validator)
        self._audio_helper = AudioTranscriptHelper(
            text_capable_scorer=text_capable_scorer,
            use_entra_auth=use_entra_auth,
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the identifier for this scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier(
            children={
                "sub_scorers": [self._audio_helper.text_scorer.get_identifier()],
            },
        )

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        """
        Score an audio file by transcribing it and scoring the transcript.

        Args:
            message_piece: The message piece containing the audio file path.
            objective: Optional objective description for scoring.

        Returns:
            List of scores from evaluating the transcribed audio.
        """
        return await self._audio_helper._score_audio_async(message_piece=message_piece, objective=objective)
