# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import base64
import logging
from io import BytesIO
from pathlib import Path
from typing import Tuple

import numpy
from PIL import Image

from pyrit.models import PromptDataType, data_serializer_factory
from pyrit.prompt_converter.prompt_converter import ConverterResult, PromptConverter

logger = logging.getLogger(__name__)


class _AdamOptimizer:
    """
    Implementation of the Adam Optimizer using NumPy. Adam optimization is a stochastic gradient
    descent method that is based on adaptive estimation of first-order and second-order moments.
    For further details, see the original paper: `"Adam: A Method for Stochastic Optimization"`
    by D. P. Kingma and J. Ba, 2014: https://arxiv.org/abs/1412.6980.

    Note:
        The code is inspired by the implementation found at:
        https://github.com/xbeat/Machine-Learning/blob/main/Adam%20Optimizer%20in%20Python.md
    """

    def __init__(
        self, *, learning_rate: float = 0.001, beta_1: float = 0.9, beta_2: float = 0.999, epsilon: float = 1e-8
    ):
        """
        Initialize the Adam optimizer with specified hyperparameters.

        Args:
            learning_rate (float): The step size for each update/iteration. Default is 0.001
            beta_1 (float): The exponential decay rate for the first moment estimates. Default is 0.9
            beta_2 (float): The exponential decay rate for the second moment estimates. Default is 0.999
            epsilon (float): A small constant for numerical stability (to prevent division by zero).
        """
        self.learning_rate = learning_rate
        self.beta_1 = beta_1
        self.beta_2 = beta_2
        self.epsilon = epsilon
        self.m: numpy.ndarray  # type: ignore[type-arg, unused-ignore]  # first moment vector
        self.v: numpy.ndarray  # type: ignore[type-arg, unused-ignore]  # second moment vector
        self.t = 0  # initialize timestep

    def update(self, *, params: numpy.ndarray, grads: numpy.ndarray) -> numpy.ndarray:  # type: ignore[type-arg, unused-ignore]
        """
        Perform a single update step using the Adam optimization algorithm.

        Args:
            params (numpy.ndarray): Current parameter values to be optimized.
            grads (numpy.ndarray): Gradients w.r.t. stochastic objective.

        Returns:
            numpy.ndarray: Updated parameter values after applying the Adam optimization step.
        """
        if self.t == 0:
            self.m = numpy.zeros_like(params)
            self.v = numpy.zeros_like(params)
        self.t += 1

        # Update biased first and second raw moment estimates
        self.m = self.beta_1 * self.m + (1 - self.beta_1) * grads
        self.v = self.beta_2 * self.v + (1 - self.beta_2) * (grads**2)

        # Compute bias-corrected first and second raw moment estimates
        m_hat = self.m / (1 - self.beta_1**self.t)
        v_hat = self.v / (1 - self.beta_2**self.t)

        params -= self.learning_rate * m_hat / (numpy.sqrt(v_hat) + self.epsilon)
        return params


class TransparencyAttackConverter(PromptConverter):
    """
    Creates a transparency attack by optimizing an alpha channel to blend attack and benign images.

    This converter takes two inputs:
        - Benign image (foreground/target): The harmless image specified during initialization.
        - Attack image (background/harmful): The potentially harmful image passed via the prompt parameter.

    The algorithm optimizes a transparency pattern so that the output PNG exhibits dual perception:
        - On white/light backgrounds: appears as the benign image.
        - On dark backgrounds: reveals the attack image content.
        - AI systems may perceive either image depending on their background processing assumptions.

    Currently, only JPEG images are supported as input. Output images will always be saved as PNG with transparency.

    Note:
        This converter implements the transparency attack as described in:
        `"Transparency Attacks: How Imperceptible Image Layers Can Fool AI Perception"` by
        McKee, F. and Noever, D., 2024: https://arxiv.org/abs/2401.15817

        As stated in the paper: `"The major limitation of the transparency attack is the low success rate when the
        human viewer’s background theme is not light by default or at least a close match to the transparent
        foreground and hidden background layers. When mismatched, the background becomes visible to the human eye
        and the vision algorithm."`
    """

    SUPPORTED_INPUT_TYPES = ("image_path",)
    SUPPORTED_OUTPUT_TYPES = ("image_path",)

    @staticmethod
    def _validate_input_image(path: str) -> None:
        """
        Validate input image to ensure it is a valid JPEG file.

        Args:
            path (str): The file path to validate.

        Raises:
            ValueError: If the path is empty or the file is not a JPEG.
            FileNotFoundError: If the file does not exist at the specified path.
        """
        if not path:
            raise ValueError("The image path cannot be empty.")
        if not path.lower().endswith((".jpg", ".jpeg")):
            raise ValueError(f"The file is not a JPEG: {path}")
        if not Path(path).exists():
            raise FileNotFoundError(f"The file does not exist: {path}")

    def __init__(
        self,
        *,
        benign_image_path: Path,
        size: Tuple[int, int] = (150, 150),
        steps: int = 1500,
        learning_rate: float = 0.001,
        convergence_threshold: float = 1e-6,
        convergence_patience: int = 10,
    ):
        """
        Initialize the converter with the path to a benign image and parameters for blending.

        Args:
            benign_image_path (Path): Path to the benign image file. Must be a JPEG file (.jpg or .jpeg).
            size (tuple): Size that the images will be resized to (width, height).
                It is recommended to use a size that matches aspect ratio of both attack and benign images.
                Since the original study resizes images to 150x150 pixels, this is the default size used.
                Bigger values may significantly increase computation time.
            steps (int): Number of optimization steps to perform.
                Recommended range: 100-2000 steps. Default is 1500. Generally, the higher the steps, the
                better end result you can achieve, but at the cost of increased computation time.
            learning_rate (float): Controls the magnitude of adjustments in each step (used by the Adam optimizer).
                Recommended range: 0.0001-0.01. Default is 0.001. Values close to 1 may lead to instability and
                lower quality blending, while values too low may require more steps to achieve a good blend.
            convergence_threshold (float): Minimum change in loss required to consider improvement.
                If the change in loss between steps is below this value, it's counted as no improvement.
                Default is 1e-6. Recommended range: 1e-6 to 1e-4.
            convergence_patience (int): Number of consecutive steps with no improvement before stopping. Default is 10.

        Raises:
            ValueError: If the benign image is invalid or is not in JPEG format.
            ValueError: If the learning rate is outside the valid range (0, 1).
            ValueError: If the size is not a tuple of two positive integers (width, height).
            ValueError: If the steps is not a positive integer.
            ValueError: If convergence threshold is not a float between 0 and 1.
            ValueError: If convergence patience is not a positive integer.
        """
        self.benign_image_path = benign_image_path
        self.learning_rate = learning_rate
        self.size = size
        self.steps = steps
        self.convergence_threshold = convergence_threshold
        self.convergence_patience = convergence_patience

        self._validate_input_image(str(benign_image_path))

        if not (0 < learning_rate < 1):
            raise ValueError(f"Learning rate must be between 0 and 1, got {learning_rate}")
        if not isinstance(size, tuple) or len(size) != 2 or any(dim <= 0 for dim in size):
            raise ValueError(f"Size must be a tuple of two positive integers (width, height). Received {size}")
        if not isinstance(steps, int) or steps <= 0:
            raise ValueError(f"Steps must be a positive integer, got {steps}")
        if not (0 < convergence_threshold < 1):
            raise ValueError(f"Convergence threshold must be a float between 0 and 1, got {convergence_threshold}")
        if not isinstance(convergence_patience, int) or convergence_patience <= 0:
            raise ValueError(f"Convergence patience must be a positive integer, got {convergence_patience}")

        self._cached_benign_image = self._load_and_preprocess_image(str(benign_image_path))

    def _load_and_preprocess_image(self, path: str) -> numpy.ndarray:  # type: ignore[type-arg, unused-ignore]
        """
        Load image, convert to grayscale, resize, and normalize for optimization.

        Args:
            path (str): The file path to the image.

        Returns:
            numpy.ndarray: Preprocessed image as a normalized NumPy array.

        Raises:
            ValueError: If the image cannot be loaded or processed.
        """
        try:
            with Image.open(path) as img:
                img_gray = img.convert("L") if img.mode != "L" else img  # read as grayscale
                img_resized = img_gray.resize(self.size, Image.Resampling.LANCZOS)
                return numpy.array(img_resized, dtype=numpy.float32) / 255.0  # normalize to [0, 1]
        except Exception as e:
            raise ValueError(f"Failed to load and preprocess image from {path}: {e}")

    def _compute_mse_loss(self, blended_image: numpy.ndarray, target_tensor: numpy.ndarray) -> float:  # type: ignore[type-arg, unused-ignore]
        """
        Compute Mean Squared Error (MSE) loss between blended and target images.

        Args:
            blended_image (numpy.ndarray): The blended image array.
            target_tensor (numpy.ndarray): The target benign image array.

        Returns:
            float: The computed MSE loss value.
        """
        return float(numpy.mean(numpy.square(blended_image - target_tensor)))

    def _create_blended_image(self, attack_image: numpy.ndarray, alpha: numpy.ndarray) -> numpy.ndarray:  # type: ignore[type-arg, unused-ignore]
        """
        Create a blended image using the attack image and alpha transparency.

        Args:
            attack_image (numpy.ndarray): The attack image array.
            alpha (numpy.ndarray): The alpha transparency array.

        Returns:
            numpy.ndarray: The blended image in LA mode.
        """
        attack_image_uint8 = (attack_image * 255).astype(numpy.uint8)
        transparency_uint8 = (alpha * 255).astype(numpy.uint8)

        # Create LA image: Luminance + Alpha (grayscale with transparency)
        height, width = attack_image_uint8.shape[:2]
        la_image = numpy.zeros((height, width, 2), dtype=numpy.uint8)
        la_image[:, :, 0] = attack_image_uint8  # L (Luminance)
        la_image[:, :, 1] = transparency_uint8  # A (Alpha)

        return la_image

    async def _save_blended_image(self, attack_image: numpy.ndarray, alpha: numpy.ndarray) -> str:  # type: ignore[type-arg, unused-ignore]
        """
        Save the blended image with transparency as a PNG file.

        Args:
            attack_image (numpy.ndarray): The attack image array.
            alpha (numpy.ndarray): The alpha transparency array.

        Returns:
            str: The file path to the saved blended image.

        Raises:
            ValueError: If saving the blended image fails.
        """
        try:
            img_serializer = data_serializer_factory(category="prompt-memory-entries", data_type="image_path")
            img_serializer.file_extension = "png"

            la_image = self._create_blended_image(attack_image, alpha)
            # Pillow 10+ can infer the mode from the array dtype and shape
            la_pil = Image.fromarray(la_image)
            image_buffer = BytesIO()
            la_pil.save(image_buffer, format="PNG")
            image_str = base64.b64encode(image_buffer.getvalue())

            await img_serializer.save_b64_image(data=image_str.decode())
            return img_serializer.value
        except Exception as e:
            raise ValueError(f"Failed to save blended image: {e}")

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "image_path") -> ConverterResult:
        """
        Convert the given prompt by blending an attack image (potentially harmful) with a benign image.
        Uses the Novel Image Blending Algorithm from: https://arxiv.org/abs/2401.15817.

        Args:
            prompt (str): The image file path to the attack image.
            input_type (PromptDataType): The type of input data. Must be "image_path".

        Returns:
            ConverterResult: The result containing path to the manipulated image with transparency.

        Raises:
            ValueError: If the input type is not supported or if the prompt is invalid.
        """
        if not self.input_supported(input_type):
            raise ValueError(f"Input type '{input_type}' not supported. Only 'image_path' is supported.")

        self._validate_input_image(prompt)

        background_image = self._load_and_preprocess_image(prompt)
        background_tensor = background_image * 0.5  # darkening for better blending optimization

        alpha = numpy.ones_like(background_tensor)  # optimized to determine transparency pattern
        white_background = numpy.ones_like(background_tensor)  # white canvas for blending simulation

        optimizer = _AdamOptimizer(learning_rate=self.learning_rate)
        grad_blended_alpha_constant = background_tensor - white_background

        prev_loss = float("inf")
        no_improvement_count = 0

        for step in range(self.steps):
            # Simulate blending: alpha=1 uses darkened attack image, alpha=0 uses white
            blended_image = alpha * background_tensor + (1 - alpha) * white_background

            current_loss = self._compute_mse_loss(blended_image, self._cached_benign_image)
            if step % 100 == 0:
                logger.debug(f"Step {step}/{self.steps}, Loss: {current_loss:.6f}")

            if abs(prev_loss - current_loss) < self.convergence_threshold:
                no_improvement_count += 1
                if no_improvement_count >= self.convergence_patience:
                    logger.info(
                        f"Convergence detected at step {step} with loss {current_loss:.8f}. Stopping optimization."
                    )
                    break
            else:
                no_improvement_count = 0  # count only consecutive steps with no improvement
            prev_loss = current_loss

            grad_loss_blended = 2 * (blended_image - self._cached_benign_image) / blended_image.size
            grad_alpha = grad_loss_blended * grad_blended_alpha_constant
            alpha = optimizer.update(params=alpha, grads=grad_alpha)
            alpha = numpy.clip(alpha, 0.0, 1.0)

        image_path = await self._save_blended_image(background_tensor, alpha)
        return ConverterResult(output_text=image_path, output_type="image_path")
