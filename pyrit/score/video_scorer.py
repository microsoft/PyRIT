# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
import random
import tempfile
import uuid
from abc import ABC
from typing import Optional

from pyrit.memory import CentralMemory
from pyrit.models import MessagePiece, Score
from pyrit.score.audio_transcript_scorer import AudioTranscriptHelper
from pyrit.score.scorer import Scorer

logger = logging.getLogger(__name__)


class _BaseVideoScorer(ABC):  # noqa: B024
    """
    Abstract base class for video scorers that process videos by extracting frames and scoring them.

    This class provides common functionality for extracting frames from videos and delegating
    scoring to an image-capable scorer. Concrete implementations handle aggregation logic
    specific to their scoring type (true/false or float scale).
    """

    _DEFAULT_VIDEO_FRAMES_SAMPLING_NUM = 5

    # Default templates that provide context about the scoring task
    # Use {objective} as placeholder for the actual objective
    # The image is also sent to the image scorer as part of the request
    _DEFAULT_IMAGE_OBJECTIVE_TEMPLATE = (
        "The objective is: {objective}\n\nThis request includes an image from the resultant video."
    )
    # The audio file is sent to the audio scorer which handles transcription
    _DEFAULT_AUDIO_OBJECTIVE_TEMPLATE = (
        "The objective is: {objective}\n\n"
        "This request includes audio extracted from the resultant video. The text is: {message_piece.converted_value}"
    )

    def __init__(
        self,
        *,
        image_capable_scorer: Scorer,
        num_sampled_frames: Optional[int] = None,
        image_objective_template: Optional[str] = _DEFAULT_IMAGE_OBJECTIVE_TEMPLATE,
        audio_objective_template: Optional[str] = None,
    ) -> None:
        """
        Initialize the base video scorer.

        Args:
            image_capable_scorer: A scorer capable of processing images that will be used to score
                individual video frames.
            num_sampled_frames: Number of frames to extract from the video for scoring (default: 5).
            image_objective_template: Template for formatting the objective when scoring image frames.
                Use {objective} as placeholder for the actual objective. Set to None to not pass
                objective to image scorer. Defaults to a template that provides context about the
                video frame.
            audio_objective_template: Template for formatting the objective when scoring audio.
                Use {objective} as placeholder for the actual objective. Set to None to not pass
                objective to audio scorer. Defaults to None because video objectives typically
                describe visual content that doesn't apply to audio.

        Raises:
            ValueError: If num_sampled_frames is provided and is not a positive integer.
        """
        self.image_scorer = image_capable_scorer
        self.image_objective_template = image_objective_template
        self.audio_objective_template = audio_objective_template

        # Validate num_sampled_frames if provided
        if num_sampled_frames is not None and num_sampled_frames <= 0:
            raise ValueError("num_sampled_frames must be a positive integer")

        self.num_sampled_frames = (
            num_sampled_frames if num_sampled_frames is not None else self._DEFAULT_VIDEO_FRAMES_SAMPLING_NUM
        )

    @staticmethod
    def _validate_audio_scorer(scorer: Scorer) -> None:
        """
        Validate that a scorer supports the audio_path data type.

        Args:
            scorer: The scorer to validate.

        Raises:
            ValueError: If the scorer does not support audio_path data type.
        """
        if "audio_path" not in scorer._validator._supported_data_types:
            raise ValueError(
                f"audio_scorer must support 'audio_path' data type. "
                f"Supported types: {scorer._validator._supported_data_types}"
            )

    async def _score_frames_async(self, *, message_piece: MessagePiece, objective: Optional[str] = None) -> list[Score]:
        """
        Extract frames from video and score them.

        Args:
            message_piece: The message piece containing the video.
            objective: Optional objective description for scoring.

        Returns:
            List of scores for the extracted frames.

        Raises:
            FileNotFoundError: If the video file does not exist.
            ValueError: If no frames are extracted from the video or if no scores are returned for the frames.
        """
        video_path = message_piece.converted_value

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        # Extract frames from video
        frames = self._extract_frames(video_path)
        if not frames:
            raise ValueError("No frames extracted from video for scoring.")

        image_requests = []

        for frame in frames:
            # Convert original_prompt_id to UUID if it's a string
            original_prompt_id = message_piece.original_prompt_id
            if isinstance(original_prompt_id, str):
                original_prompt_id = uuid.UUID(original_prompt_id)

            piece = MessagePiece(
                original_value=message_piece.converted_value,
                role=message_piece.get_role_for_storage(),
                original_prompt_id=original_prompt_id,
                converted_value=frame,
                converted_value_data_type="image_path",
            )
            response = piece.to_message()
            image_requests.append(response)

        # Add the frame pieces to memory before scoring so that score references are valid

        memory = CentralMemory.get_memory_instance()
        for request in image_requests:
            memory.add_message_to_memory(request=request)

        # Format objective using template if both are provided
        if objective is None or self.image_objective_template is None:
            scoring_objectives = None
        else:
            formatted_objective = self.image_objective_template.format(objective=objective)
            scoring_objectives = [formatted_objective] * len(image_requests)

        frame_scores = await self.image_scorer.score_prompts_batch_async(
            messages=image_requests, objectives=scoring_objectives, batch_size=len(frames)
        )

        if not frame_scores:
            raise ValueError("No scores returned for image frames extracted from video.")

        return frame_scores

    def _extract_frames(self, video_path: str) -> list[str]:
        """
        Extract a specified number of image frames from a video file.

        Args:
            video_path: The path to the video file.

        Returns:
            List of temporary file paths for the extracted frames.

        Raises:
            ModuleNotFoundError: If OpenCV is not installed.
        """
        try:
            import cv2  # noqa: F401
        except ModuleNotFoundError as e:
            logger.error("Could not import opencv. You may need to install it via 'pip install pyrit[opencv]'")
            raise e

        frame_paths = []
        video_capture = cv2.VideoCapture(video_path)

        try:
            total_frames = int(video_capture.get(cv2.CAP_PROP_FRAME_COUNT))
            if total_frames > 0:
                # Choose up to num_sampled_frames random unique frame indices
                frame_indices = sorted(random.sample(range(total_frames), min(self.num_sampled_frames, total_frames)))
                for frame_index in frame_indices:
                    # Set the video position to the selected frame
                    video_capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                    ret, frame = video_capture.read()
                    if not ret:
                        logger.warning(f"Failed to read frame at index {frame_index}")
                        continue

                    # Create a temporary file for the frame
                    with tempfile.NamedTemporaryFile(suffix=f"_frame_{frame_index}.png", delete=False) as temp_file:
                        # Encode and write frame to temporary file
                        ret, _ = cv2.imencode(".png", frame)
                        if not ret:
                            logger.warning(f"Failed to encode frame at index {frame_index}")
                            continue

                        cv2.imwrite(temp_file.name, frame)
                        frame_paths.append(temp_file.name)
        finally:
            video_capture.release()

        return frame_paths

    async def _score_video_audio_async(
        self, *, message_piece: MessagePiece, audio_scorer: Optional[Scorer] = None, objective: Optional[str] = None
    ) -> list[Score]:
        """
        Extract and score audio from the video.

        Args:
            message_piece: The message piece containing the video.
            audio_scorer: The scorer to use for audio scoring.
            objective: Optional objective description for scoring.

        Returns:
            List of scores for the audio content, or empty list if audio extraction/scoring fails.
        """
        if audio_scorer is None:
            return []

        video_path = message_piece.converted_value

        # Use BaseAudioTranscriptScorer's static method to extract audio

        audio_path = AudioTranscriptHelper.extract_audio_from_video(video_path)
        if not audio_path:
            logger.warning("Video does not have any audio! Skipping audio scoring.")
            return []

        should_cleanup = True
        try:
            # Create a message piece for the audio
            original_prompt_id = message_piece.original_prompt_id
            if isinstance(original_prompt_id, str):
                original_prompt_id = uuid.UUID(original_prompt_id)

            audio_piece = MessagePiece(
                original_value=audio_path,
                role=message_piece.get_role_for_storage(),
                original_prompt_id=original_prompt_id,
                converted_value=audio_path,
                converted_value_data_type="audio_path",
            )

            audio_message = audio_piece.to_message()

            # Add to memory
            memory = CentralMemory.get_memory_instance()
            memory.add_message_to_memory(request=audio_message)

            # Score the audio using the audio_scorer
            # Format objective using template if both are provided
            if objective is None or self.audio_objective_template is None:
                scoring_objectives = None
            else:
                formatted_objective = self.audio_objective_template.format(objective=objective)
                scoring_objectives = [formatted_objective]

            audio_scores = await audio_scorer.score_prompts_batch_async(
                messages=[audio_message],
                objectives=scoring_objectives,
                batch_size=1,
            )

            return audio_scores if audio_scores else []

        except Exception as e:
            # Keep the audio file for debugging on failure
            should_cleanup = False
            logger.error(f"Audio scoring failed. Temporary audio file kept for debugging: {audio_path}. Error: {e}")
            raise

        finally:
            # Clean up temporary audio file on success
            if should_cleanup and audio_path and os.path.exists(audio_path):
                os.unlink(audio_path)
