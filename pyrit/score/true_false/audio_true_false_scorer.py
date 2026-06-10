# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.models import ComponentIdentifier, MessagePiece, Score
from pyrit.score.audio_transcript_scorer import AudioTranscriptHelper
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class AudioTrueFalseScorer(TrueFalseScorer):
    """
    A scorer that processes audio files by transcribing them and scoring the transcript.

    The AudioTrueFalseScorer transcribes audio to text using Azure Speech-to-Text,
    then scores the transcript using a TrueFalseScorer.
    """

    _DEFAULT_VALIDATOR: ScorerPromptValidator = ScorerPromptValidator(supported_data_types=["audio_path"])

    def __init__(
        self,
        *,
        text_capable_scorer: TrueFalseScorer,
        validator: ScorerPromptValidator | None = None,
    ) -> None:
        """
        Initialize the AudioTrueFalseScorer.

        Args:
            text_capable_scorer: A TrueFalseScorer capable of processing text.
                This scorer will be used to evaluate the transcribed audio content.
            validator: Validator for the scorer. Defaults to audio_path data type validator.

        Raises:
            ValueError: If text_capable_scorer does not support text data type.
        """
        super().__init__(validator=validator or self._DEFAULT_VALIDATOR)
        self._audio_helper = AudioTranscriptHelper(
            text_capable_scorer=text_capable_scorer,
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

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        """
        Score an audio file by transcribing it and scoring the transcript.

        Args:
            message_piece: The message piece containing the audio file path.
            objective: Optional objective description for scoring.

        Returns:
            List of scores from evaluating the transcribed audio.
        """
        return await self._audio_helper._score_audio_async(message_piece=message_piece, objective=objective)
