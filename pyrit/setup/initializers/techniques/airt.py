# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
AIRT source-owned scenario techniques.

Home for techniques that belong to an AIRT scenario/source but are not part of
the general-purpose ``core`` catalog. They live in the shared catalog (tagged
with the ``airt`` owner) so any scenario can select them, yet default to their
owning scenario.

Unlike ``core``/``extra`` (registered into the global registry by
``TechniqueInitializer``), these are imported directly by their owning scenario
and are intentionally *not* part of the default ``build_technique_factories``
aggregation — so scenarios that consume the full catalog keep their existing
technique pool.
"""

from pyrit.common.path import DATASETS_PATH
from pyrit.converter import AddImageTextConverter, FirstLetterConverter
from pyrit.executor.attack import AttackConverterConfig, PromptSendingAttack
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory

_BLANK_IMAGE_PATH = str(DATASETS_PATH / "seed_datasets" / "local" / "examples" / "blank_canvas.png")


def get_technique_factories() -> list[AttackTechniqueFactory]:
    """
    Build the AIRT source-owned technique factories.

    These back the Leakage scenario (``first_letter`` obfuscation and a blank-image
    carrier). They carry a scenario-specific ``leakage`` tag (they're quite specific
    to leakage) while staying selectable by any scenario via their ``airt`` owner tag.

    Returns:
        list[AttackTechniqueFactory]: The AIRT source-owned techniques.
    """
    return [
        AttackTechniqueFactory(
            name="first_letter",
            attack_class=PromptSendingAttack,
            description="Obfuscates the objective by asking for it encoded as the first letter of each word.",
            technique_tags=["single_turn", "airt", "leakage"],
            attack_kwargs={
                "attack_converter_config": AttackConverterConfig(
                    request_converters=ConverterConfiguration.from_converters(converters=[FirstLetterConverter()])
                ),
            },
        ),
        AttackTechniqueFactory(
            name="image",
            attack_class=PromptSendingAttack,
            description="Carries the objective text inside a blank image so it bypasses text-only input handling.",
            technique_tags=["single_turn", "airt", "leakage"],
            attack_kwargs={
                "attack_converter_config": AttackConverterConfig(
                    request_converters=ConverterConfiguration.from_converters(
                        converters=[AddImageTextConverter(img_to_add=_BLANK_IMAGE_PATH)]
                    )
                ),
            },
        ),
    ]
