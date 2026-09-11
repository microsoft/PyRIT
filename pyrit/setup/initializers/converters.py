# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Initializer for registering the core converter presets."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.common.path import DATASETS_PATH
from pyrit.registry import ConverterRegistry
from pyrit.setup.pyrit_initializer import PyRITInitializer

if TYPE_CHECKING:
    from pyrit.converter import Converter

logger = logging.getLogger(__name__)


def _get_text_jailbreak_args() -> dict[str, Any]:
    """
    Build the constructor arguments for the text jailbreak preset.

    Returns:
        dict[str, Any]: Constructor arguments containing the jailbreak template.
    """
    from pyrit.datasets import TextJailBreak

    return {"jailbreak_template": TextJailBreak(template_file_name="jailbreak_1.yaml")}


@dataclass(frozen=True)
class ConverterConfig:
    """Configuration for a converter preset."""

    registry_name: str
    converter_type: str
    constructor_args: dict[str, Any] = field(default_factory=dict)
    constructor_args_factory: Callable[[], dict[str, Any]] | None = None

    def get_constructor_args(self) -> dict[str, Any]:
        """
        Get a new constructor argument dictionary for this preset.

        Returns:
            dict[str, Any]: A copy of the configured arguments with deferred arguments included.
        """
        args = dict(self.constructor_args)
        if self.constructor_args_factory:
            args.update(self.constructor_args_factory())
        return args


class ConverterInitializer(PyRITInitializer):
    """
    Register the curated core converter presets into the ConverterRegistry.

    Each preset explicitly declares its constructor arguments and target references.
    Custom initializers can use the same pattern to register additional or parameterized
    converter presets.
    """

    CONFIGS: ClassVar[tuple[ConverterConfig, ...]] = (
        ConverterConfig(registry_name="base64", converter_type="Base64Converter"),
        ConverterConfig(registry_name="binary", converter_type="BinaryConverter"),
        ConverterConfig(registry_name="char_swap", converter_type="CharSwapConverter"),
        ConverterConfig(registry_name="ecoji", converter_type="EcojiConverter"),
        ConverterConfig(registry_name="insert_punctuation", converter_type="InsertPunctuationConverter"),
        ConverterConfig(registry_name="leetspeak", converter_type="LeetspeakConverter"),
        ConverterConfig(registry_name="rot13", converter_type="ROT13Converter"),
        ConverterConfig(
            registry_name="search_replace",
            converter_type="SearchReplaceConverter",
            constructor_args={"pattern": r"\s+", "replace": "_"},
        ),
        ConverterConfig(registry_name="string_join", converter_type="StringJoinConverter"),
        ConverterConfig(
            registry_name="text_jailbreak",
            converter_type="TextJailbreakConverter",
            constructor_args_factory=_get_text_jailbreak_args,
        ),
        ConverterConfig(registry_name="zalgo", converter_type="ZalgoConverter"),
        ConverterConfig(
            registry_name="malicious_question_generator",
            converter_type="MaliciousQuestionGeneratorConverter",
            constructor_args={"converter_target": "adversarial_chat"},
        ),
        ConverterConfig(
            registry_name="math_prompt",
            converter_type="MathPromptConverter",
            constructor_args={"converter_target": "adversarial_chat"},
        ),
        ConverterConfig(
            registry_name="noise",
            converter_type="NoiseConverter",
            constructor_args={"converter_target": "adversarial_chat"},
        ),
        ConverterConfig(
            registry_name="tense_future",
            converter_type="TenseConverter",
            constructor_args={"converter_target": "adversarial_chat", "tense": "future"},
        ),
        ConverterConfig(
            registry_name="tense_past",
            converter_type="TenseConverter",
            constructor_args={"converter_target": "adversarial_chat", "tense": "past"},
        ),
        ConverterConfig(
            registry_name="tone_professional",
            converter_type="ToneConverter",
            constructor_args={"converter_target": "adversarial_chat", "tone": "professional"},
        ),
        ConverterConfig(
            registry_name="tone_sarcastic",
            converter_type="ToneConverter",
            constructor_args={"converter_target": "adversarial_chat", "tone": "sarcastic"},
        ),
        ConverterConfig(
            registry_name="translation_spanish",
            converter_type="TranslationConverter",
            constructor_args={"converter_target": "adversarial_chat", "language": "Spanish"},
        ),
        ConverterConfig(
            registry_name="variation",
            converter_type="VariationConverter",
            constructor_args={"converter_target": "adversarial_chat"},
        ),
        ConverterConfig(
            registry_name="add_image_text",
            converter_type="AddImageTextConverter",
            constructor_args={
                "img_to_add": str(DATASETS_PATH / "seed_datasets" / "local" / "examples" / "blank_canvas.png")
            },
        ),
        ConverterConfig(
            registry_name="add_text_image",
            converter_type="AddTextImageConverter",
            constructor_args={"text_to_add": "PyRIT"},
        ),
        ConverterConfig(
            registry_name="azure_speech_audio_to_text",
            converter_type="AzureSpeechAudioToTextConverter",
        ),
        ConverterConfig(registry_name="image_color_saturation", converter_type="ImageColorSaturationConverter"),
        ConverterConfig(registry_name="image_compression", converter_type="ImageCompressionConverter"),
        ConverterConfig(registry_name="image_rotation", converter_type="ImageRotationConverter"),
        ConverterConfig(registry_name="qr_code", converter_type="QRCodeConverter"),
        ConverterConfig(
            registry_name="transparency_attack",
            converter_type="TransparencyAttackConverter",
            constructor_args={
                "benign_image_path": DATASETS_PATH / "seed_datasets" / "local" / "examples" / "benign_cake_question.jpg"
            },
        ),
    )

    async def initialize_async(self) -> None:
        """Create and register the core converter presets."""
        converter_registry = ConverterRegistry.get_registry_singleton()

        for config in self.CONFIGS:
            try:
                converter = await asyncio.to_thread(
                    self._create_converter,
                    converter_registry=converter_registry,
                    config=config,
                )
                converter_registry.instances.register(converter, name=config.registry_name)
                logger.info("Registered converter: %s", config.registry_name)
            except (FileNotFoundError, KeyError, TypeError, ValueError) as ex:
                logger.warning("Skipping converter '%s': %s", config.registry_name, ex)

    def _create_converter(
        self,
        *,
        converter_registry: ConverterRegistry,
        config: ConverterConfig,
    ) -> "Converter":
        """
        Create one configured converter outside the event-loop thread.

        Returns:
            Converter: The configured converter instance.
        """
        return converter_registry.create_instance(config.converter_type, **config.get_constructor_args())
