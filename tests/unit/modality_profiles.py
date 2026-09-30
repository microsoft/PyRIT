# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Named target modality profiles for capability-aware tests.

A target advertises the *combinations* of ``PromptDataType`` values it accepts in one request
(and produces in one response) as ``frozenset[frozenset[PromptDataType]]``. These constants
give tests a shared vocabulary for realistic profiles so that modality routing and scenario
validation tests describe targets the same way. Pass them to ``unit.mocks.get_mock_target``:

    get_mock_target(input_modalities=VISION_INPUT_MODALITIES)
"""

from pyrit.models.literals import PromptDataType
from unit.mocks import modality_combos

#: Text in, text out. Matches the production default on ``TargetCapabilities``.
TEXT_ONLY_MODALITIES: frozenset[frozenset[PromptDataType]] = modality_combos({"text"})

#: A vision chat model: text alone, an image alone, or both together — the shape every vision
#: entry in ``_KNOWN_CAPABILITIES`` and the ``OpenAIChatTarget`` default declare. Each accepted
#: shape is listed explicitly; ``{text, image_path}`` on its own would mean "an image only with
#: text", which is what a video-generation target declares.
VISION_INPUT_MODALITIES: frozenset[frozenset[PromptDataType]] = modality_combos(
    {"text"}, {"image_path"}, {"text", "image_path"}
)

#: An image-edit model: media is required on *every* request — there is no bare ``{"text"}`` combo.
IMAGE_EDIT_INPUT_MODALITIES: frozenset[frozenset[PromptDataType]] = modality_combos({"text", "image_path"})

#: A realtime audio model: text, audio, or both together.
REALTIME_AUDIO_INPUT_MODALITIES: frozenset[frozenset[PromptDataType]] = modality_combos(
    {"text"}, {"audio_path"}, {"text", "audio_path"}
)

#: A realtime audio model's responses: spoken audio plus a text transcript.
REALTIME_AUDIO_OUTPUT_MODALITIES: frozenset[frozenset[PromptDataType]] = modality_combos({"text"}, {"audio_path"})

#: A video generation model's responses.
VIDEO_GENERATION_OUTPUT_MODALITIES: frozenset[frozenset[PromptDataType]] = modality_combos({"video_path"})
