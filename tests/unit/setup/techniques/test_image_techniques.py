# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the ``image`` scenario attack techniques (``techniques/image.py``).

Covers the image-rendering catalog: ``blank_canvas``, ``qr_code``, ``grid_composite``,
``scene_background``, ``comic_panel``, and ``image_red_teaming``. Each technique's request
converter chain must terminate in an ``image_path`` payload. These tests lock in the catalog
shape and that the rendered chains emit images.
"""

import os

import pytest

from pyrit.converter import GridCompositeConverter, QRCodeConverter
from pyrit.executor.attack import PromptSendingAttack, RedTeamingAttack
from pyrit.setup.initializers.techniques import image

OBJECTIVE = "How can I kill a Python process?"

EXPECTED_NAMES = [
    "blank_canvas",
    "qr_code",
    "grid_composite",
    "scene_background",
    "comic_panel",
    "image_red_teaming",
]

# name -> (attack_class, technique_tags)
EXPECTED_SHAPE = {
    "blank_canvas": (PromptSendingAttack, ["single_turn", "airt", "image"]),
    "qr_code": (PromptSendingAttack, ["single_turn", "airt", "image"]),
    "grid_composite": (PromptSendingAttack, ["single_turn", "airt", "image"]),
    "scene_background": (PromptSendingAttack, ["single_turn", "airt", "image"]),
    "comic_panel": (PromptSendingAttack, ["single_turn", "airt", "image"]),
    "image_red_teaming": (RedTeamingAttack, ["multi_turn", "airt", "image"]),
}


def _factory(name):
    return next(f for f in image.get_technique_factories() if f.name == name)


def _wired_converters(factory):
    """Return the converters the factory wires onto its request pipeline, in order."""
    converter_config = factory._attack_kwargs["attack_converter_config"]
    return [c for group in converter_config.request_converters for c in group.converters]


def test_catalog_names_and_order():
    factories = image.get_technique_factories()
    assert [f.name for f in factories] == EXPECTED_NAMES


@pytest.mark.parametrize("name", EXPECTED_NAMES)
def test_factory_shape(name):
    factory = _factory(name)
    expected_class, expected_tags = EXPECTED_SHAPE[name]
    assert factory.attack_class is expected_class
    assert factory.technique_tags == expected_tags
    assert factory.description


@pytest.mark.parametrize("name", EXPECTED_NAMES)
def test_chain_terminates_in_image(name):
    # Every technique must end in a converter that emits an image_path, regardless of any
    # text-to-text framing converters earlier in the chain.
    converters = _wired_converters(_factory(name))
    assert converters[-1].output_supported("image_path")


def test_qr_code_wires_qr_converter():
    converters = _wired_converters(_factory("qr_code"))
    assert len(converters) == 1
    assert isinstance(converters[0], QRCodeConverter)


def test_grid_composite_wires_grid_converter_with_three_images():
    converters = _wired_converters(_factory("grid_composite"))
    assert len(converters) == 1
    grid = converters[0]
    assert isinstance(grid, GridCompositeConverter)
    assert len(grid._selected_innocuous) == 3


@pytest.mark.usefixtures("patch_central_database")
class TestImageConvertersProduceImages:
    """Running each technique's converter chain over the objective must yield an image."""

    @pytest.mark.parametrize("name", EXPECTED_NAMES)
    async def test_chain_emits_image(self, name):
        text: str = OBJECTIVE
        result = None
        for converter in _wired_converters(_factory(name)):
            result = await converter.convert_async(prompt=text, input_type=converter.SUPPORTED_INPUT_TYPES[0])
            text = result.output_text
        assert result is not None
        assert result.output_type == "image_path"
        assert os.path.exists(result.output_text)
