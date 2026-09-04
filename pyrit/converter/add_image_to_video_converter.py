# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import contextlib
import logging
import tempfile
from pathlib import Path

import numpy as np

from pyrit.common.path import DB_DATA_PATH
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.memory import data_serializer_factory
from pyrit.models import ComponentIdentifier, PromptDataType

logger = logging.getLogger(__name__)


# Choose the codec based on extension
video_encoding_map = {
    "mp4": "mp4v",
    "avi": "XVID",
    "mov": "MJPG",
    "mkv": "X264",
}


class AddImageVideoConverter(Converter):
    """
    Adds an image to a video at a specified position.

    Currently the image is placed in the whole video, not at a specific timepoint.
    """

    SUPPORTED_INPUT_TYPES = ("image_path",)
    SUPPORTED_OUTPUT_TYPES = ("video_path",)

    def __init__(
        self,
        *,
        video_path: Path,
        img_position: tuple[int, int] = (10, 10),
        img_resize_size: tuple[int, int] = (500, 500),
    ) -> None:
        """
        Initialize the converter with the video path and image properties.

        Args:
            video_path (Path): File path of video to add image to.
            img_position (tuple): Position to place image in video. Defaults to (10, 10).
            img_resize_size (tuple): Size to resize image to. Defaults to (500, 500).
        """
        self._img_position = img_position
        self._img_resize_size = img_resize_size
        self._video_path = video_path

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build identifier with video converter parameters.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        return self._create_identifier(
            params={
                "video_path": str(self._video_path),
                "img_position": self._img_position,
                "img_resize_size": self._img_resize_size,
            }
        )

    async def _add_image_to_video_async(self, image_path: str) -> bytes:
        """
        Add an image to video.

        Args:
            image_path (str): The image path to add to video.

        Returns:
            bytes: The converted video data.

        Raises:
            ModuleNotFoundError: If OpenCV is not installed.
            ValueError: If the image path is invalid or unsupported video format.
        """
        try:
            import cv2  # noqa: F401
        except ModuleNotFoundError as e:
            logger.error("Could not import opencv. You may need to install it via 'pip install pyrit[opencv]'")
            raise e

        if not image_path:
            raise ValueError("Please provide valid image path value")

        input_image_data = data_serializer_factory(
            category="prompt-memory-entries", data_type="image_path", value=image_path
        )
        input_video_data = data_serializer_factory(
            category="prompt-memory-entries", data_type="video_path", value=str(self._video_path)
        )

        # Open the video to ensure it exists
        video_bytes = await input_video_data.read_data_async()
        input_image_bytes = await input_image_data.read_data_async()

        return await asyncio.to_thread(
            self._add_image_to_video_sync,
            video_bytes=video_bytes,
            image_bytes=input_image_bytes,
        )

    def _add_image_to_video_sync(
        self,
        *,
        video_bytes: bytes,
        image_bytes: bytes,
    ) -> bytes:
        """
        Run the blocking cv2 pipeline and temp-file I/O on a worker thread.

        Designed to be invoked via ``asyncio.to_thread`` so the event loop is not blocked.

        Returns:
            bytes: The converted video data.

        Raises:
            ValueError: If the input video format is unsupported or the overlay image cannot
                be decoded.
        """
        import cv2

        file_extension = self._video_path.suffix.removeprefix(".").lower()
        if file_extension not in video_encoding_map:
            raise ValueError(f"Unsupported video format: {file_extension}")

        with tempfile.NamedTemporaryFile(suffix=f".{file_extension}", dir=DB_DATA_PATH, delete=False) as input_file:
            input_file.write(video_bytes)
            input_path = Path(input_file.name)
        with tempfile.NamedTemporaryFile(suffix=f".{file_extension}", dir=DB_DATA_PATH, delete=False) as output_file:
            output_path = Path(output_file.name)

        cap: cv2.VideoCapture | None = None
        output_video: cv2.VideoWriter | None = None

        try:
            try:
                cap = cv2.VideoCapture(str(input_path))

                # Get video properties
                fps = int(cap.get(cv2.CAP_PROP_FPS))
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                video_char_code = cv2.VideoWriter.fourcc(*video_encoding_map[file_extension])
                output_video = cv2.VideoWriter(str(output_path), video_char_code, fps, (width, height))

                # Load and resize the overlay image

                image_np_arr = np.frombuffer(image_bytes, np.uint8)
                decoded = cv2.imdecode(image_np_arr, cv2.IMREAD_UNCHANGED)
                if decoded is None:
                    raise ValueError("Failed to decode overlay image")
                overlay = cv2.resize(decoded, self._img_resize_size)

                # Get overlay image dimensions
                image_height, image_width, _ = overlay.shape
                x, y = self._img_position  # Position where the overlay will be placed

                while cap.isOpened():
                    ret, frame = cap.read()
                    if not ret:
                        break

                    # Ensure overlay fits within the frame boundaries
                    if x + image_width > width or y + image_height > height:
                        logger.info("Overlay image is too large for the video frame. Resizing to fit.")
                        overlay = cv2.resize(overlay, (width - x, height - y))
                        image_height, image_width, _ = overlay.shape

                    # Blend overlay with frame
                    if overlay.shape[2] == 4:  # Check number of channels on image
                        alpha_overlay = overlay[:, :, 3] / 255.0
                        for c in range(3):
                            frame[y : y + image_height, x : x + image_width, c] = (
                                alpha_overlay * overlay[:, :, c]
                                + (1 - alpha_overlay) * frame[y : y + image_height, x : x + image_width, c]
                            )
                    else:
                        frame[y : y + image_height, x : x + image_width] = overlay

                    # Write the modified frame to the output video
                    output_video.write(frame)
            finally:
                if cap is not None:
                    cap.release()
                if output_video is not None:
                    output_video.release()
                with contextlib.suppress(cv2.error):
                    cv2.destroyAllWindows()  # Not available in headless OpenCV builds

            return output_path.read_bytes()
        finally:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "image_path") -> ConverterResult:
        """
        Convert the given prompt (image) by adding it to a video.

        Args:
            prompt (str): The image path to be added to the video.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The result containing filename of the converted video.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        output_video_serializer = data_serializer_factory(
            category="prompt-memory-entries",
            data_type="video_path",
            extension=self._video_path.suffix.removeprefix("."),
        )
        updated_video = await self._add_image_to_video_async(image_path=prompt)
        await output_video_serializer.save_data_async(data=updated_video)
        logger.info(f"Video saved as {output_video_serializer.value}")

        return ConverterResult(output_text=str(output_video_serializer.value), output_type="video_path")
