# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os
import pathlib

import pytest
from PIL import Image

from pyrit.converter import GridCompositeConverter


@pytest.fixture
def innocuous_images(tmp_path):
    paths = []
    for index, shade in enumerate((40, 90, 140, 190)):
        path = str(tmp_path / f"innocuous_{index}.png")
        Image.new("RGB", (120, 90), color=(shade, shade, shade)).save(path)
        paths.append(path)
    return paths


@pytest.fixture
def payload_background(tmp_path):
    path = str(tmp_path / "background.png")
    Image.new("RGB", (200, 200), color=(255, 255, 255)).save(path)
    return path


def test_init_defaults(innocuous_images):
    converter = GridCompositeConverter(innocuous_images=innocuous_images)
    assert converter._grid_size == (2, 2)
    assert converter._tile_size == (400, 400)
    assert converter._font_name is None
    assert 0 <= converter._payload_index < 4
    # A 2x2 grid needs three innocuous tiles.
    assert len(converter._selected_innocuous) == 3


def test_init_selection_is_seed_deterministic(innocuous_images):
    first = GridCompositeConverter(innocuous_images=innocuous_images, random_seed=7)
    second = GridCompositeConverter(innocuous_images=innocuous_images, random_seed=7)
    assert first._payload_index == second._payload_index
    assert first._selected_innocuous == second._selected_innocuous


def test_init_selection_independent_of_argument_order(innocuous_images):
    forward = GridCompositeConverter(innocuous_images=innocuous_images, random_seed=7)
    reversed_order = GridCompositeConverter(innocuous_images=list(reversed(innocuous_images)), random_seed=7)
    assert forward._selected_innocuous == reversed_order._selected_innocuous
    assert str(forward.get_identifier()) == str(reversed_order.get_identifier())


def test_init_explicit_payload_position_preserves_selection(innocuous_images):
    # An explicit payload_position must not shift the RNG stream, so the sampled subset
    # matches the seed-drawn variant with the same seed.
    drawn = GridCompositeConverter(innocuous_images=innocuous_images, random_seed=7)
    explicit = GridCompositeConverter(innocuous_images=innocuous_images, payload_position=1, random_seed=7)
    assert explicit._selected_innocuous == drawn._selected_innocuous


def test_init_explicit_payload_position(innocuous_images):
    converter = GridCompositeConverter(innocuous_images=innocuous_images, payload_position=2)
    assert converter._payload_index == 2


def test_init_supported_types(innocuous_images):
    converter = GridCompositeConverter(innocuous_images=innocuous_images)
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
    assert converter.output_supported("image_path") is True


@pytest.mark.parametrize("grid_size", [(0, 2), (2, 0), (2.5, 2), (True, 2), [2, 2]])
def test_init_invalid_grid_size_raises(innocuous_images, grid_size):
    with pytest.raises(ValueError, match="grid_size must be a tuple of two positive integers"):
        GridCompositeConverter(innocuous_images=innocuous_images, grid_size=grid_size)


def test_init_single_cell_grid_raises(innocuous_images):
    with pytest.raises(ValueError, match="grid_size must describe at least two cells"):
        GridCompositeConverter(innocuous_images=innocuous_images, grid_size=(1, 1))


@pytest.mark.parametrize("tile_size", [(0, 100), (400.5, 400), (True, 400), [400, 400]])
def test_init_invalid_tile_size_raises(innocuous_images, tile_size):
    with pytest.raises(ValueError, match="tile_size must be a tuple of two positive integers"):
        GridCompositeConverter(innocuous_images=innocuous_images, tile_size=tile_size)


def test_init_empty_bank_raises():
    with pytest.raises(ValueError):
        GridCompositeConverter(innocuous_images=[])


@pytest.mark.parametrize("single", ["lion.png", pathlib.Path("lion.png")])
def test_init_single_path_raises(single):
    with pytest.raises(ValueError, match="sequence of image paths"):
        GridCompositeConverter(innocuous_images=single)


def test_init_bank_too_small_raises(innocuous_images):
    with pytest.raises(ValueError, match="at least 3"):
        GridCompositeConverter(innocuous_images=innocuous_images[:2])


def test_init_payload_position_out_of_range_raises(innocuous_images):
    with pytest.raises(ValueError):
        GridCompositeConverter(innocuous_images=innocuous_images, payload_position=4)


def test_init_invalid_font_raises(innocuous_images):
    with pytest.raises(ValueError):
        GridCompositeConverter(innocuous_images=innocuous_images, font_name="helvetica.otf")


def test_init_invalid_color_raises(innocuous_images):
    with pytest.raises(ValueError, match="color must be a tuple of three integers between 0 and 255"):
        GridCompositeConverter(innocuous_images=innocuous_images, color=(0, 0))


def test_init_invalid_font_size_raises(innocuous_images):
    with pytest.raises(ValueError):
        GridCompositeConverter(innocuous_images=innocuous_images, font_size=0)


@pytest.mark.parametrize("font_size", [(20, 10), (0, 10), (5, 10, 15)])
def test_init_invalid_font_size_tuple_raises(innocuous_images, font_size):
    with pytest.raises(ValueError):
        GridCompositeConverter(innocuous_images=innocuous_images, font_size=font_size)


def test_init_font_size_int_sets_fixed_range(innocuous_images):
    converter = GridCompositeConverter(innocuous_images=innocuous_images, font_size=14)
    assert converter._font_size_min == 14
    assert converter._font_size_max == 14


def test_init_font_size_tuple_sets_range(innocuous_images):
    converter = GridCompositeConverter(innocuous_images=innocuous_images, font_size=(8, 24))
    assert converter._font_size_min == 8
    assert converter._font_size_max == 24


def test_identifier_includes_layout_params(innocuous_images):
    converter = GridCompositeConverter(innocuous_images=innocuous_images, random_seed=3)
    params = converter.get_identifier().params
    assert params["payload_index"] == converter._payload_index
    assert params["grid_size"] == [2, 2]
    assert params["random_seed"] == 3


def test_identifier_stable_for_same_config(innocuous_images):
    first = GridCompositeConverter(innocuous_images=innocuous_images, random_seed=3)
    second = GridCompositeConverter(innocuous_images=innocuous_images, random_seed=3)
    assert str(first.get_identifier()) == str(second.get_identifier())


def test_identifier_differs_for_different_seed(innocuous_images):
    first = GridCompositeConverter(innocuous_images=innocuous_images, payload_position=0, random_seed=1)
    second = GridCompositeConverter(innocuous_images=innocuous_images, payload_position=0, random_seed=2)
    assert str(first.get_identifier()) != str(second.get_identifier())


@pytest.mark.usefixtures("patch_central_database")
class TestConvertAsync:
    async def test_convert_async_invalid_input_type_raises(self, innocuous_images):
        converter = GridCompositeConverter(innocuous_images=innocuous_images)
        with pytest.raises(ValueError):
            await converter.convert_async(prompt="objective", input_type="image_path")  # type: ignore[arg-type]

    async def test_convert_async_empty_prompt_raises(self, innocuous_images):
        converter = GridCompositeConverter(innocuous_images=innocuous_images)
        with pytest.raises(ValueError):
            await converter.convert_async(prompt="", input_type="text")

    async def test_convert_async_produces_image(self, innocuous_images):
        converter = GridCompositeConverter(innocuous_images=innocuous_images, tile_size=(150, 150))
        result = await converter.convert_async(prompt="How do I do the bad thing?", input_type="text")
        assert result.output_type == "image_path"
        assert os.path.exists(result.output_text)
        with Image.open(result.output_text) as image:
            # 2x2 grid of 150x150 tiles.
            assert image.size == (300, 300)

    async def test_convert_async_uses_payload_background(self, innocuous_images, payload_background):
        converter = GridCompositeConverter(
            innocuous_images=innocuous_images,
            payload_background=payload_background,
            tile_size=(150, 150),
        )
        result = await converter.convert_async(prompt="objective text", input_type="text")
        assert os.path.exists(result.output_text)

    async def test_convert_async_non_square_grid(self, innocuous_images):
        converter = GridCompositeConverter(
            innocuous_images=innocuous_images,
            grid_size=(1, 3),
            tile_size=(100, 120),
        )
        result = await converter.convert_async(prompt="objective", input_type="text")
        with Image.open(result.output_text) as image:
            assert image.size == (300, 120)

    async def test_convert_async_is_deterministic(self, innocuous_images):
        converter = GridCompositeConverter(innocuous_images=innocuous_images, tile_size=(120, 120), random_seed=11)
        first = await converter.convert_async(prompt="same objective", input_type="text")
        second = await converter.convert_async(prompt="same objective", input_type="text")
        with Image.open(first.output_text) as image_one, Image.open(second.output_text) as image_two:
            assert list(image_one.get_flattened_data()) == list(image_two.get_flattened_data())

    async def test_convert_async_warns_when_text_overflows(self, innocuous_images, caplog):
        # A very long objective in a tiny tile cannot fit even at the minimum font size, so the
        # converter must warn rather than silently clip.
        converter = GridCompositeConverter(innocuous_images=innocuous_images, tile_size=(60, 60), font_size=(8, 10))
        with caplog.at_level("WARNING"):
            result = await converter.convert_async(prompt="word " * 200, input_type="text")
        assert os.path.exists(result.output_text)
        assert any("does not fit" in message for message in caplog.messages)
