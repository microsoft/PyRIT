# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Image-rendering scenario techniques.

Source-owned catalog for techniques that render a text objective into an image
payload for vision-capable targets. Like ``airt`` (and unlike ``core``/``extra``),
these are imported directly by their owning scenario (``ImageAdaptive``) and are
intentionally *not* part of the default ``build_technique_factories`` aggregation
or the global registry — so text-only scenarios that consume the full catalog
never select an image technique and fail at runtime against a text-only target.

Each technique pairs ``PromptSendingAttack`` with a converter that emits an
``image_path``; the target's text response is scored by the scenario's objective
scorer.
"""

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH, DATASETS_PATH
from pyrit.converter import (
    AddImageTextConverter,
    GridCompositeConverter,
    QRCodeConverter,
)
from pyrit.executor.attack import AttackConverterConfig, PromptSendingAttack, RedTeamingAttack
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory


def get_technique_factories() -> list[AttackTechniqueFactory]:
    """
    Build the image-rendering technique factories.

    These back the ``ImageAdaptive`` scenario. They carry an ``image`` tag while
    staying selectable by any scenario via their ``airt`` owner tag, and each emits
    an ``image_path`` payload so the objective target must accept image input.

    Returns:
        list[AttackTechniqueFactory]: The image-rendering technique factories.
    """
    return [
        AttackTechniqueFactory(
            name="blank_canvas",
            attack_class=PromptSendingAttack,
            description="Carries the objective text inside a blank image so it bypasses text-only input handling.",
            technique_tags=["single_turn", "airt", "image"],
            attack_kwargs={
                "attack_converter_config": AttackConverterConfig(
                    request_converters=ConverterConfiguration.from_converters(
                        converters=[
                            AddImageTextConverter(
                                img_to_add=DATASETS_PATH / "seed_datasets" / "local" / "examples" / "blank_canvas.png"
                            )
                        ]
                    )
                ),
            },
        ),
        AttackTechniqueFactory(
            name="qr_code",
            attack_class=PromptSendingAttack,
            description="Encodes the objective text as a QR code image.",
            technique_tags=["single_turn", "airt", "image"],
            attack_kwargs={
                "attack_converter_config": AttackConverterConfig(
                    request_converters=ConverterConfiguration.from_converters(converters=[QRCodeConverter()])
                ),
            },
        ),
        AttackTechniqueFactory(
            name="grid_composite",
            attack_class=PromptSendingAttack,
            description=(
                "Renders the objective into one cell of a grid whose other cells are innocuous images, "
                "so each cell looks benign on its own."
            ),
            technique_tags=["single_turn", "airt", "image"],
            attack_kwargs={
                "attack_converter_config": AttackConverterConfig(
                    request_converters=ConverterConfiguration.from_converters(
                        converters=[
                            GridCompositeConverter(
                                innocuous_images=[
                                    CONVERTER_SEED_PROMPT_PATH / "innocuous_images" / "lion.png",
                                    CONVERTER_SEED_PROMPT_PATH / "innocuous_images" / "roakey.png",
                                    CONVERTER_SEED_PROMPT_PATH / "innocuous_images" / "strawberry.png",
                                ]
                            )
                        ]
                    )
                ),
            },
        ),
        AttackTechniqueFactory(
            name="scene_background",
            attack_class=PromptSendingAttack,
            description=(
                "Renders the objective over an innocuous photo with a tilt and light color so it reads "
                "as part of the scene rather than a request."
            ),
            technique_tags=["single_turn", "airt", "image"],
            attack_kwargs={
                "attack_converter_config": AttackConverterConfig(
                    request_converters=ConverterConfiguration.from_converters(
                        converters=[
                            AddImageTextConverter(
                                img_to_add=CONVERTER_SEED_PROMPT_PATH / "innocuous_images" / "lion.png",
                                color=(255, 255, 255),
                                rotation=5.0,
                                font_size=(12, 28),
                                center_text=True,
                            )
                        ]
                    )
                ),
            },
        ),
        AttackTechniqueFactory(
            name="comic_panel",
            attack_class=PromptSendingAttack,
            description=(
                "Embeds the objective in the empty panel of a comic strip and asks the target to complete the story."
            ),
            technique_tags=["single_turn", "airt", "image"],
            attack_kwargs={
                "attack_converter_config": AttackConverterConfig(
                    request_converters=ConverterConfiguration.from_converters(
                        converters=[
                            AddImageTextConverter(
                                img_to_add=CONVERTER_SEED_PROMPT_PATH / "comic_panel" / "article.png",
                                bounding_box=(1080, 70, 1480, 680),
                                rotation=0.0,
                                center_text=True,
                                font_size=(30, 60),
                            )
                        ]
                    )
                ),
            },
        ),
        AttackTechniqueFactory(
            name="image_red_teaming",
            attack_class=RedTeamingAttack,
            description=(
                "Runs a multi-turn red teaming conversation whose turns are each rendered to an image "
                "before reaching the target."
            ),
            technique_tags=["multi_turn", "airt", "image"],
            attack_kwargs={
                "attack_converter_config": AttackConverterConfig(
                    request_converters=ConverterConfiguration.from_converters(
                        converters=[
                            AddImageTextConverter(
                                img_to_add=DATASETS_PATH / "seed_datasets" / "local" / "examples" / "blank_canvas.png",
                                font_size=(12, 28),
                            )
                        ]
                    )
                ),
            },
        ),
    ]
