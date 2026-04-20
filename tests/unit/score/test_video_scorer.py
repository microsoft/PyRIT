# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import uuid
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from pyrit.models import MessagePiece, Score
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.float_scale.video_float_scale_scorer import VideoFloatScaleScorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer
from pyrit.score.true_false.video_true_false_scorer import VideoTrueFalseScorer
from tests.unit.mocks import get_mock_scorer_identifier


def is_opencv_installed():
    try:
        import cv2  # noqa: F401

        return True
    except ModuleNotFoundError:
        return False


@pytest.fixture(autouse=True)
def video_converter_sample_video(patch_central_database):
    # Create a sample video file
    video_path = "test_video.mp4"
    width, height = 512, 512
    if is_opencv_installed():
        import cv2  # noqa: F401

        # Create a video writer object
        video_encoding = cv2.VideoWriter_fourcc(*"mp4v")
        output_video = cv2.VideoWriter(video_path, video_encoding, 20, (width, height))
        # Create a few frames for video
        for i in range(10):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            processed_frame = cv2.flip(frame, 0)
            output_video.write(processed_frame)

        output_video.release()

    message_piece = MessagePiece(
        role="user",
        original_value=video_path,
        converted_value=video_path,
        original_value_data_type="video_path",
        converted_value_data_type="video_path",
    )
    message_piece.id = uuid.uuid4()
    yield message_piece
    # Cleanup the sample video file
    if os.path.exists(video_path):
        os.remove(video_path)


class MockTrueFalseScorer(TrueFalseScorer):
    """Mock TrueFalseScorer for testing"""

    def __init__(self, return_value: bool = True):
        self.return_value = return_value
        validator = ScorerPromptValidator(supported_data_types=["image_path"])
        super().__init__(validator=validator)

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this mock scorer."""
        self._set_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        return [
            Score(
                score_type="true_false",
                score_value=str(self.return_value).lower(),
                score_rationale=f"Test rationale for {message_piece.converted_value}",
                score_category=["test_category"],
                score_metadata={},
                score_value_description="test_description",
                message_piece_id=message_piece.id or uuid.uuid4(),
                objective=objective,
                scorer_class_identifier=get_mock_scorer_identifier(),
            )
        ]


class MockFloatScaleScorer(FloatScaleScorer):
    """Mock FloatScaleScorer for testing"""

    def __init__(self, return_value: float = 0.8):
        self.return_value = return_value
        validator = ScorerPromptValidator(supported_data_types=["image_path"])
        super().__init__(validator=validator)

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this mock scorer."""
        self._set_identifier()

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        return [
            Score(
                score_type="float_scale",
                score_value=str(self.return_value),
                score_rationale=f"Test rationale for {message_piece.converted_value}",
                score_category=["test_category"],
                score_metadata={},
                score_value_description="test_description",
                message_piece_id=message_piece.id or uuid.uuid4(),
                objective=objective,
                scorer_class_identifier=get_mock_scorer_identifier(),
            )
        ]


@pytest.mark.asyncio
@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_extract_frames_true_false(video_converter_sample_video):
    """Test that frame extraction produces the expected number of frames"""
    import cv2

    image_scorer = MockTrueFalseScorer()
    scorer = VideoTrueFalseScorer(image_capable_scorer=image_scorer, num_sampled_frames=3)
    video_path = video_converter_sample_video.converted_value
    frame_paths = scorer._extract_frames(video_path=video_path)

    assert len(frame_paths) == scorer.num_sampled_frames, (
        f"Expected {scorer.num_sampled_frames} frames, got {len(frame_paths)}"
    )

    # Verify frames are valid images and cleanup
    for path in frame_paths:
        assert os.path.exists(path), f"Frame file {path} does not exist"
        img = cv2.imread(path)
        assert img is not None, f"Failed to read frame file {path}"
        assert img.shape == (512, 512, 3), f"Unexpected frame dimensions: {img.shape}"
        os.remove(path)  # Cleanup


@pytest.mark.asyncio
@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_extract_frames_float_scale(video_converter_sample_video):
    """Test that frame extraction produces the expected number of frames for float scale scorer"""
    import cv2

    image_scorer = MockFloatScaleScorer()
    scorer = VideoFloatScaleScorer(image_capable_scorer=image_scorer, num_sampled_frames=3)
    video_path = video_converter_sample_video.converted_value
    frame_paths = scorer._extract_frames(video_path=video_path)

    assert len(frame_paths) == scorer.num_sampled_frames, (
        f"Expected {scorer.num_sampled_frames} frames, got {len(frame_paths)}"
    )

    # Verify frames are valid images and cleanup
    for path in frame_paths:
        assert os.path.exists(path), f"Frame file {path} does not exist"
        img = cv2.imread(path)
        assert img is not None, f"Failed to read frame file {path}"
        assert img.shape == (512, 512, 3), f"Unexpected frame dimensions: {img.shape}"
        os.remove(path)  # Cleanup


@pytest.mark.asyncio
@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_score_video_true_false(video_converter_sample_video):
    """Test video scoring with a true/false scorer"""
    image_scorer = MockTrueFalseScorer(return_value=True)
    scorer = VideoTrueFalseScorer(image_capable_scorer=image_scorer, num_sampled_frames=3)

    scores = await scorer._score_piece_async(video_converter_sample_video)

    assert len(scores) == 1, "Expected one aggregated score"
    assert scores[0].score_type == "true_false"
    assert scores[0].score_value == "true"
    assert "Video scored by analyzing" in scores[0].score_rationale


@pytest.mark.asyncio
@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_score_video_true_false_with_false_frames(video_converter_sample_video):
    """Test video scoring when all frames score false"""
    image_scorer = MockTrueFalseScorer(return_value=False)
    scorer = VideoTrueFalseScorer(image_capable_scorer=image_scorer, num_sampled_frames=3)

    scores = await scorer._score_piece_async(video_converter_sample_video)

    assert len(scores) == 1, "Expected one aggregated score"
    assert scores[0].score_type == "true_false"
    assert scores[0].score_value == "false"
    assert "Video scored by analyzing" in scores[0].score_rationale


@pytest.mark.asyncio
@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_score_video_float_scale(video_converter_sample_video):
    """Test video scoring with a float_scale scorer"""
    image_scorer = MockFloatScaleScorer(return_value=0.8)
    scorer = VideoFloatScaleScorer(image_capable_scorer=image_scorer, num_sampled_frames=3)

    scores = await scorer._score_piece_async(video_converter_sample_video)

    assert len(scores) == 1, "Expected one aggregated score"
    assert scores[0].score_type == "float_scale"
    # With MAX aggregator (default), should return 0.8
    assert float(scores[0].score_value) == 0.8
    assert "Video scored by analyzing" in scores[0].score_rationale


@pytest.mark.asyncio
@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_score_video_no_frames(video_converter_sample_video):
    """Test error handling when no frames can be extracted"""
    image_scorer = MockTrueFalseScorer()
    scorer = VideoTrueFalseScorer(image_capable_scorer=image_scorer, num_sampled_frames=3)

    # Mock _extract_frames to return empty list
    scorer._extract_frames = MagicMock(return_value=[])

    with pytest.raises(ValueError, match="No frames extracted from video for scoring."):
        await scorer._score_piece_async(video_converter_sample_video)


@pytest.mark.asyncio
@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_score_video_no_scores(video_converter_sample_video):
    """Test error handling when frame scoring returns no scores"""
    image_scorer = MockTrueFalseScorer()

    # Mock score_prompts_batch_async to return empty list
    image_scorer.score_prompts_batch_async = AsyncMock(return_value=[])
    scorer = VideoTrueFalseScorer(image_capable_scorer=image_scorer, num_sampled_frames=3)

    with pytest.raises(ValueError, match="No scores returned for image frames extracted from video."):
        await scorer._score_piece_async(video_converter_sample_video)


@pytest.mark.asyncio
@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_video_true_false_scorer_with_objective(video_converter_sample_video):
    """Test that objective is passed through correctly"""
    image_scorer = MockTrueFalseScorer(return_value=True)
    scorer = VideoTrueFalseScorer(image_capable_scorer=image_scorer, num_sampled_frames=3)

    objective = "Test objective"
    scores = await scorer._score_piece_async(video_converter_sample_video, objective=objective)

    assert len(scores) == 1
    assert scores[0].objective == objective


@pytest.mark.asyncio
@pytest.mark.skipif(not is_opencv_installed(), reason="opencv is not installed")
async def test_video_float_scale_scorer_with_objective(video_converter_sample_video):
    """Test that objective is passed through correctly for float scale scorer"""
    image_scorer = MockFloatScaleScorer(return_value=0.7)
    scorer = VideoFloatScaleScorer(image_capable_scorer=image_scorer, num_sampled_frames=3)

    objective = "Test objective"
    scores = await scorer._score_piece_async(video_converter_sample_video, objective=objective)

    assert len(scores) == 1
    assert scores[0].objective == objective


def test_video_scorer_invalid_frames():
    """Test that VideoScorer raises error with invalid num_sampled_frames"""
    image_scorer = MockTrueFalseScorer()

    with pytest.raises(ValueError, match="num_sampled_frames must be a positive integer"):
        VideoTrueFalseScorer(image_capable_scorer=image_scorer, num_sampled_frames=0)

    with pytest.raises(ValueError, match="num_sampled_frames must be a positive integer"):
        VideoTrueFalseScorer(image_capable_scorer=image_scorer, num_sampled_frames=-1)


def test_video_scorer_default_num_frames():
    """Test that VideoScorer uses default num_sampled_frames when not specified"""
    image_scorer = MockTrueFalseScorer()
    scorer = VideoTrueFalseScorer(image_capable_scorer=image_scorer)

    assert scorer.num_sampled_frames == 5  # Default value
