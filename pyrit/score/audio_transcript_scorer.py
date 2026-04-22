# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
import tempfile
import uuid
import warnings
from typing import Optional

import av

from pyrit.memory import CentralMemory
from pyrit.models import MessagePiece, Score
from pyrit.prompt_converter import AzureSpeechAudioToTextConverter
from pyrit.score.scorer import Scorer

logger = logging.getLogger(__name__)


def _is_compliant_wav(input_path: str, *, sample_rate: int, channels: int) -> bool:
    """
    Check if the audio file is already a compliant WAV with the target format.

    Args:
        input_path (str): Path to the audio file.
        sample_rate (int): Expected sample rate in Hz.
        channels (int): Expected number of channels.

    Returns:
        bool: True if the file is already compliant, False otherwise.
    """
    try:
        with av.open(input_path) as container:
            if not container.streams.audio:
                return False
            stream = container.streams.audio[0]
            codec_name = stream.codec_context.name
            is_pcm_s16 = codec_name == "pcm_s16le"
            is_correct_rate = stream.rate == sample_rate
            is_correct_channels = stream.channels == channels
            return bool(is_pcm_s16 and is_correct_rate and is_correct_channels)
    except Exception:
        return False


def _audio_to_wav(input_path: str, *, sample_rate: int, channels: int) -> str:
    """
    Convert any audio or video file to a normalised PCM WAV using PyAV.

    If the input is already a compliant WAV (correct sample rate, channels, and codec),
    returns the original path without re-encoding.

    Args:
        input_path (str): Source audio or video file.
        sample_rate (int): Target sample rate in Hz.
        channels (int): Target number of channels (1 = mono).

    Returns:
        str: Path to the WAV file (original if compliant, otherwise a temporary file).
    """
    # Skip conversion if already compliant
    if _is_compliant_wav(input_path, sample_rate=sample_rate, channels=channels):
        logger.debug(f"Audio file already compliant, skipping conversion: {input_path}")
        return input_path

    layout = "mono" if channels == 1 else "stereo"
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        output_path = tmp.name

    with av.open(input_path) as in_container:
        with av.open(output_path, "w", format="wav") as out_container:
            out_stream = out_container.add_stream("pcm_s16le", rate=sample_rate, layout=layout)
            resampler = av.AudioResampler(format="s16", layout=layout, rate=sample_rate)

            for frame in in_container.decode(audio=0):
                for out_frame in resampler.resample(frame):
                    for packet in out_stream.encode(out_frame):
                        out_container.mux(packet)

            for out_frame in resampler.resample(None):
                for packet in out_stream.encode(out_frame):
                    out_container.mux(packet)

            for packet in out_stream.encode(None):
                out_container.mux(packet)

    return output_path


class AudioTranscriptHelper:  # noqa: B024
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

    def __init__(
        self,
        *,
        text_capable_scorer: Scorer,
        use_entra_auth: Optional[bool] = None,
    ) -> None:
        """
        Initialize the base audio scorer.

        Args:
            text_capable_scorer (Scorer): A scorer capable of processing text that will be used to score
                the transcribed audio content.
            use_entra_auth (bool, Optional): **Deprecated.** Will be removed in v0.15.0.
                Authentication is now auto-detected by the underlying converter.

        Raises:
            ValueError: If text_capable_scorer does not support text data type.
        """
        if use_entra_auth is not None:
            warnings.warn(
                "'use_entra_auth' is deprecated and will be removed in v0.15.0. "
                "Authentication is now auto-detected by the underlying AzureSpeechAudioToTextConverter.",
                DeprecationWarning,
                stacklevel=2,
            )
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
        """
        return _audio_to_wav(
            audio_path,
            sample_rate=self._DEFAULT_SAMPLE_RATE,
            channels=self._DEFAULT_CHANNELS,
        )

    def _extract_audio_from_video(self, video_path: str) -> Optional[str]:
        """
        Extract audio track from a video file.

        Args:
            video_path (str): Path to the video file.

        Returns:
            str: a path to the extracted audio file (WAV format)
                or returns None if extraction fails.
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
        """
        try:
            logger.info(f"Extracting audio from video: {video_path}")
            output_path = _audio_to_wav(
                video_path,
                sample_rate=AudioTranscriptHelper._DEFAULT_SAMPLE_RATE,
                channels=AudioTranscriptHelper._DEFAULT_CHANNELS,
            )
            logger.info(f"Audio exported to: {output_path} (rate={AudioTranscriptHelper._DEFAULT_SAMPLE_RATE}Hz, mono)")
            return output_path
        except Exception as e:
            logger.warning(f"Failed to extract audio from video {video_path}: {e}")
            return None
