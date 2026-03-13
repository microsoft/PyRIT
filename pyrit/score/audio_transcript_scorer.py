# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
import tempfile
import uuid
from abc import ABC
from typing import Optional

from pyrit.memory import CentralMemory
from pyrit.models import MessagePiece, Score
from pyrit.prompt_converter import AzureSpeechAudioToTextConverter
from pyrit.score.scorer import Scorer

logger = logging.getLogger(__name__)


class AudioTranscriptHelper(ABC):  # noqa: B024
    """
    Abstract base class for audio scorers that process audio by transcribing and scoring the text.

    This class provides common functionality for transcribing audio files and delegating
    scoring to a text-capable scorer. Concrete implementations handle aggregation logic
    specific to their scoring type (true/false or float scale).
    """

    # Azure Speech optimal audio settings
    _DEFAULT_SAMPLE_RATE = 16000  # 16kHz - Azure Speech optimal rate
    _DEFAULT_CHANNELS = 1  # Mono - Azure Speech prefers mono
    _DEFAULT_SAMPLE_WIDTH = 2  # 16-bit audio (2 bytes per sample)
    _DEFAULT_EXPORT_PARAMS = ["-acodec", "pcm_s16le"]  # 16-bit PCM for best compatibility

    def __init__(
        self,
        *,
        text_capable_scorer: Scorer,
    ) -> None:
        """
        Initialize the base audio scorer.

        Args:
            text_capable_scorer (Scorer): A scorer capable of processing text that will be used to score
                the transcribed audio content.

        Raises:
            ValueError: If text_capable_scorer does not support text data type.
        """
        self._validate_text_scorer(text_capable_scorer)
        self.text_scorer = text_capable_scorer

    @staticmethod
    def _validate_text_scorer(scorer: Scorer) -> None:
        """
        Validate that a scorer supports the text data type.

        Args:
            scorer (Scorer): The scorer to validate.

        Raises:
            ValueError: If the scorer does not support text data type.
        """
        if "text" not in scorer._validator._supported_data_types:
            raise ValueError(
                f"text_capable_scorer must support 'text' data type. "
                f"Supported types: {scorer._validator._supported_data_types}"
            )

    async def _score_audio_async(self, *, message_piece: MessagePiece, objective: Optional[str] = None) -> list[Score]:
        """
        Transcribe audio and score the transcript.

        Args:
            message_piece (MessagePiece): The message piece containing the audio file path.
            objective (Optional[str]): Optional objective description for scoring.

        Returns:
            List of scores for the transcribed audio.

        Raises:
            FileNotFoundError: If the audio file does not exist.
            ValueError: If transcription fails or returns empty text.
        """
        audio_path = message_piece.converted_value

        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        # Transcribe audio to text
        transcript = await self._transcribe_audio_async(audio_path)

        if not transcript or not transcript.strip():
            logger.warning(f"Empty transcript from audio file: {audio_path}")
            # Return empty list - no text to score
            return []

        # Create a MessagePiece for the transcript
        original_prompt_id = message_piece.original_prompt_id
        if isinstance(original_prompt_id, str):
            original_prompt_id = uuid.UUID(original_prompt_id)

        text_piece = MessagePiece(
            original_value=transcript,
            role=message_piece.get_role_for_storage(),
            original_prompt_id=original_prompt_id,
            converted_value=transcript,
            converted_value_data_type="text",
        )

        text_message = text_piece.to_message()

        # Add to memory so score references are valid
        memory = CentralMemory.get_memory_instance()
        memory.add_message_to_memory(request=text_message)

        # Score the transcript
        transcript_scores = await self.text_scorer.score_async(message=text_message, objective=objective)

        # Add context to indicate this was scored from audio transcription
        for score in transcript_scores:
            score.score_rationale += f"\nAudio transcript scored: {score.score_rationale}"

        return transcript_scores

    async def _transcribe_audio_async(self, audio_path: str) -> str:
        """
        Transcribes an audio file to text.

        Args:
            audio_path (str): Path to the audio file.

        Returns:
            Text transcription from audio file.

        Raises:
            ModuleNotFoundError: If required transcription dependencies are not installed.\
            FileNotFoundError: If the audio file does not exist.\
            Exception: If transcription fails for any other reason.
        """
        # Convert audio to WAV if needed (Azure Speech requires WAV)
        wav_path = self._ensure_wav_format(audio_path)
        logger.info(f"Audio transcription: WAV file path = {wav_path}")

        # Check if WAV file exists and has content
        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"WAV file does not exist at {wav_path}")

        file_size = os.path.getsize(wav_path)
        logger.info(f"Audio transcription: WAV file size = {file_size} bytes")

        try:
            converter = AzureSpeechAudioToTextConverter()
            logger.info("Audio transcription: Starting Azure Speech transcription...")
            result = await converter.convert_async(prompt=wav_path, input_type="audio_path")
            logger.info(f"Audio transcription: Result = '{result.output_text}'")
            return result.output_text
        except Exception as e:
            logger.error(f"Audio transcription failed: {type(e).__name__}: {e}")
            raise
        finally:
            # Clean up temporary WAV file if it exists (ie for scoring audio from videos)
            if wav_path != audio_path and os.path.exists(wav_path):
                os.unlink(wav_path)

    def _ensure_wav_format(self, audio_path: str) -> str:
        """
        Ensure audio file is in correct WAV format for transcription.

        Args:
            audio_path (str): Path to the audio file.

        Returns:
            str: Path to WAV file (original if already WAV, or converted temporary file).

        Raises:
            ModuleNotFoundError: If pydub is not installed.
        """
        try:
            from pydub import AudioSegment
        except ModuleNotFoundError as e:
            logger.error("Could not import pydub. Install it via 'pip install pydub'")
            raise e

        audio = AudioSegment.from_file(audio_path)
        audio = (
            audio.set_frame_rate(self._DEFAULT_SAMPLE_RATE)
            .set_channels(self._DEFAULT_CHANNELS)
            .set_sample_width(self._DEFAULT_SAMPLE_WIDTH)
        )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav:
            audio.export(temp_wav.name, format="wav")
            return temp_wav.name

    def _extract_audio_from_video(self, video_path: str) -> Optional[str]:
        """
        Extract audio track from a video file.

        Args:
            video_path (str): Path to the video file.

        Returns:
            str: a path to the extracted audio file (WAV format)
                or returns None if extraction fails.

        Raises:
            ModuleNotFoundError: If pydub/ffmpeg is not installed.
        """
        return AudioTranscriptHelper.extract_audio_from_video(video_path)

    @staticmethod
    def extract_audio_from_video(video_path: str) -> Optional[str]:
        """
        Extract audio track from a video file (static version).

        Args:
            video_path (str): Path to the video file.

        Returns:
            str: a path to the extracted audio file (WAV format)
                or returns None if extraction fails.

        Raises:
            ModuleNotFoundError: If pydub/ffmpeg is not installed.
        """
        try:
            from pydub import AudioSegment
        except ModuleNotFoundError as e:
            logger.error("Could not import pydub. Install it via 'pip install pydub'")
            raise e

        try:
            # Extract audio from video using pydub (requires ffmpeg)
            logger.info(f"Extracting audio from video: {video_path}")
            audio = AudioSegment.from_file(video_path)
            logger.info(
                f"Audio extracted: duration={len(audio)}ms, channels={audio.channels}, "
                f"sample_width={audio.sample_width}, frame_rate={audio.frame_rate}"
            )

            # Optimize for Azure Speech recognition:
            # Azure Speech works best with 16kHz mono audio (same as Azure TTS output)
            if audio.frame_rate != AudioTranscriptHelper._DEFAULT_SAMPLE_RATE:
                logger.info(
                    f"Resampling audio from {audio.frame_rate}Hz to {AudioTranscriptHelper._DEFAULT_SAMPLE_RATE}Hz"
                )
                audio = audio.set_frame_rate(AudioTranscriptHelper._DEFAULT_SAMPLE_RATE)

            # Ensure 16-bit audio
            if audio.sample_width != AudioTranscriptHelper._DEFAULT_SAMPLE_WIDTH:
                logger.info(
                    f"Converting sample width from {audio.sample_width * 8}-bit"
                    f" to {AudioTranscriptHelper._DEFAULT_SAMPLE_WIDTH * 8}-bit"
                )
                audio = audio.set_sample_width(AudioTranscriptHelper._DEFAULT_SAMPLE_WIDTH)

            # Convert to mono (Azure Speech prefers mono)
            if audio.channels > AudioTranscriptHelper._DEFAULT_CHANNELS:
                logger.info(f"Converting from {audio.channels} channels to mono")
                audio = audio.set_channels(AudioTranscriptHelper._DEFAULT_CHANNELS)

            # Create temporary WAV file with PCM encoding for best compatibility
            with tempfile.NamedTemporaryFile(suffix="_video_audio.wav", delete=False) as temp_audio:
                audio.export(
                    temp_audio.name,
                    format="wav",
                    parameters=AudioTranscriptHelper._DEFAULT_EXPORT_PARAMS,
                )
                logger.info(
                    f"Audio exported to: {temp_audio.name} (duration={len(audio)}ms, rate={audio.frame_rate}Hz, mono)"
                )
                return temp_audio.name
        except Exception as e:
            logger.warning(f"Failed to extract audio from video {video_path}: {e}")
            return None
