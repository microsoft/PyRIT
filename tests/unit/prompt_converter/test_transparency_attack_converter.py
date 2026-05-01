# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from pyrit.prompt_converter import ConverterResult, TransparencyAttackConverter


@pytest.fixture
def sample_benign_image():
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        img = Image.new("RGB", (256, 256), color=(192, 192, 192))
        img.save(tmp.name, "JPEG")
        yield tmp.name
    os.unlink(tmp.name)


@pytest.fixture
def sample_attack_image():
    with tempfile.NamedTemporaryFile(suffix=".jpeg", delete=False) as tmp:
        img = Image.new("RGB", (100, 100), color=(64, 64, 64))
        img.save(tmp.name, "JPEG")
        yield tmp.name
    os.unlink(tmp.name)


@pytest.fixture
def sample_invalid_image():
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(b"This is not a valid image file.")
        yield tmp.name
    os.unlink(tmp.name)


class TestTransparencyAttackConverter:
    def test_initialization_default_params(self, sample_benign_image):
        converter = TransparencyAttackConverter(benign_image_path=sample_benign_image)
        assert converter.benign_image_path == sample_benign_image
        assert converter.size == (150, 150)
        assert converter.steps == 1500
        assert converter.learning_rate == 0.001
        assert converter.convergence_threshold == 1e-6
        assert converter.convergence_patience == 10

    def test_initialization_valid_params(self, sample_benign_image):
        converter = TransparencyAttackConverter(
            benign_image_path=sample_benign_image,
            size=(128, 128),
            steps=500,
            learning_rate=0.01,
            convergence_threshold=1e-5,
            convergence_patience=5,
        )
        assert converter.benign_image_path == sample_benign_image
        assert converter.size == (128, 128)
        assert converter.steps == 500
        assert converter.learning_rate == 0.01
        assert converter.convergence_threshold == 1e-5
        assert converter.convergence_patience == 5

    def test_initialization_invalid_params(self, sample_benign_image):
        for path in [None, "", "invalid_path.txt", "image.png", "image.gif"]:
            with pytest.raises(ValueError):
                TransparencyAttackConverter(benign_image_path=path)
        for size in [(128, 0), (0, 0), 128, -1, (-128, -128), (128,), (128, 128, 128)]:
            with pytest.raises(ValueError):
                TransparencyAttackConverter(benign_image_path=sample_benign_image, size=size)
        for steps in [-1, 0]:
            with pytest.raises(ValueError):
                TransparencyAttackConverter(benign_image_path=sample_benign_image, steps=steps)
        for learning_rate in [-0.01, 0, 1, 1.5]:
            with pytest.raises(ValueError):
                TransparencyAttackConverter(benign_image_path=sample_benign_image, learning_rate=learning_rate)
        for convergence_threshold in [-1e-6, 0, 1]:
            with pytest.raises(ValueError):
                TransparencyAttackConverter(
                    benign_image_path=sample_benign_image, convergence_threshold=convergence_threshold
                )
        for convergence_patience in [-1, 0]:
            with pytest.raises(ValueError):
                TransparencyAttackConverter(
                    benign_image_path=sample_benign_image, convergence_patience=convergence_patience
                )

    def test_validate_input_image(self, sample_benign_image):
        for invalid_path in [None, "", "invalid_path.txt", "image.png", "image.gif"]:
            with pytest.raises(ValueError):
                TransparencyAttackConverter._validate_input_image(path=invalid_path)
        for nonexistent_path in ["image.jpg", "image.jpeg", "IMAGE.JPG"]:
            with pytest.raises(FileNotFoundError):
                TransparencyAttackConverter._validate_input_image(path=nonexistent_path)
        TransparencyAttackConverter._validate_input_image(path=sample_benign_image)  # should pass validation

    def test_load_and_preprocess_image(self, sample_benign_image):
        converter = TransparencyAttackConverter(benign_image_path=sample_benign_image, size=(50, 50))
        processed_image = converter._load_and_preprocess_image(sample_benign_image)

        assert processed_image.shape == (50, 50)  # height, width (single channel grayscale)
        assert processed_image.dtype == np.float32
        assert np.all(processed_image >= 0.0) and np.all(processed_image <= 1.0)

        for invalid_path in [None, "", "invalid_path.txt", "image.png", "image.gif"]:
            with pytest.raises(ValueError):
                converter._load_and_preprocess_image(invalid_path)

        with pytest.raises(ValueError):
            converter._load_and_preprocess_image(str(sample_invalid_image))

    def test_compute_mse_loss(self, sample_benign_image):
        converter = TransparencyAttackConverter(benign_image_path=sample_benign_image)
        blended = np.array([[1.0, 2.0], [3.0, 4.0]])
        target = np.array([[2.0, 3.0], [4.0, 5.0]])
        expected_loss = 1.0

        loss = converter._compute_mse_loss(blended, target)
        assert loss == expected_loss
        assert isinstance(loss, float)

    def test_create_blended_image(self, sample_benign_image):
        converter = TransparencyAttackConverter(benign_image_path=sample_benign_image)
        attack_image = np.array([[0.2]], dtype=np.float32)  # 1x1 grayscale image
        alpha = np.array([[0.8]], dtype=np.float32)  # 1x1 alpha

        la_image = converter._create_blended_image(attack_image, alpha)

        assert la_image.shape == (1, 1, 2)  # LA (Luminance + Alpha)
        assert la_image.dtype == np.uint8
        expected_gray_value = int(0.2 * 255)
        assert la_image[0, 0, 0] == expected_gray_value  # L (Luminance)
        assert la_image[0, 0, 1] == int(0.8 * 255)  # A (Alpha)

    async def test_save_blended_image(self, sample_benign_image):
        with patch("pyrit.prompt_converter.transparency_attack_converter.data_serializer_factory") as mock_factory:
            mock_serializer = MagicMock()
            mock_serializer.file_extension = "png"
            mock_serializer.value = "mock_image_path.png"
            mock_serializer.save_b64_image = AsyncMock()
            mock_factory.return_value = mock_serializer

            converter = TransparencyAttackConverter(benign_image_path=sample_benign_image)
            attack_image = np.ones((10, 10), dtype=np.float32) * 0.5
            alpha = np.ones((10, 10), dtype=np.float32) * 0.7

            result_path = await converter._save_blended_image(attack_image, alpha)

            assert result_path == "mock_image_path.png"
            mock_factory.assert_called_once_with(category="prompt-memory-entries", data_type="image_path")
            mock_serializer.save_b64_image.assert_called_once()

    async def test_convert_async_successful(self, sample_benign_image, sample_attack_image):
        with patch("pyrit.prompt_converter.transparency_attack_converter.data_serializer_factory") as mock_factory:
            mock_serializer = MagicMock()
            mock_serializer.file_extension = "png"
            mock_serializer.value = "output_image_path.png"
            mock_serializer.save_b64_image = AsyncMock()
            mock_factory.return_value = mock_serializer

            converter = TransparencyAttackConverter(
                benign_image_path=sample_benign_image,
                size=(32, 32),
                steps=5,
            )

            result = await converter.convert_async(prompt=sample_attack_image, input_type="image_path")

            assert isinstance(result, ConverterResult)
            assert result.output_text == "output_image_path.png"
            assert result.output_type == "image_path"
            mock_serializer.save_b64_image.assert_called_once()

    async def test_convert_async_early_convergence(self, sample_benign_image, sample_attack_image):
        with patch("pyrit.prompt_converter.transparency_attack_converter.data_serializer_factory") as mock_factory:
            mock_serializer = MagicMock()
            mock_serializer.file_extension = "png"
            mock_serializer.value = "output_image_path.png"
            mock_serializer.save_b64_image = AsyncMock()
            mock_factory.return_value = mock_serializer

            # Use parameters that should trigger early convergence
            converter = TransparencyAttackConverter(
                benign_image_path=sample_benign_image,
                size=(16, 16),
                steps=1000,
                learning_rate=0.001,
                convergence_threshold=1e-3,
                convergence_patience=3,
            )

            # Mock the logger to capture convergence message
            with patch("pyrit.prompt_converter.transparency_attack_converter.logger") as mock_logger:
                result = await converter.convert_async(prompt=sample_attack_image, input_type="image_path")

                assert isinstance(result, ConverterResult)
                assert result.output_text == "output_image_path.png"
                assert result.output_type == "image_path"

                # Check if convergence message was logged (indicating early stopping occurred)
                info_calls = [call for call in mock_logger.info.call_args_list if call[0]]
                convergence_logged = any("Convergence detected" in str(call[0][0]) for call in info_calls)
                assert convergence_logged, "Expected early convergence to be detected and logged"
