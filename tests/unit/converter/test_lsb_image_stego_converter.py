# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import warnings
from io import BytesIO
from pathlib import Path
from threading import Event, get_ident
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from pyrit.converter import LSBImageStegoConverter, LSBImageStegoDecodeConverter
from pyrit.memory import DataTypeSerializer
from pyrit.registry import ConverterRegistry

if TYPE_CHECKING:
    from pyrit.converter.lsb_image_stego_converter import StegoChannelMode


def _make_serializer_mock(output_file: Path) -> MagicMock:
    """Create a mock image serializer that writes PNG bytes to ``output_file``."""
    serializer = MagicMock(spec=DataTypeSerializer)
    serializer.value = str(output_file)

    async def _save_data_async(*, data: bytes) -> None:
        await asyncio.to_thread(output_file.write_bytes, data)

    serializer.save_data_async = AsyncMock(side_effect=_save_data_async)
    return serializer


def _assert_event_loop_responsive(*, loop: asyncio.AbstractEventLoop, event_loop_thread_id: int) -> None:
    assert get_ident() != event_loop_thread_id
    progressed = Event()
    loop.call_soon_threadsafe(progressed.set)
    assert progressed.wait(timeout=5), "The event loop did not progress while image processing was paused"


@pytest.mark.asyncio
async def test_lsb_converter_default_rgba_roundtrip(tmp_path: Path) -> None:
    out_png = tmp_path / "stego_default.png"
    serializer = _make_serializer_mock(out_png)

    converter = LSBImageStegoConverter()
    prompt = "Verify system configuration and return diagnostic report."

    with patch("pyrit.converter.lsb_image_stego_converter.data_serializer_factory", return_value=serializer):
        result = await converter.convert_async(prompt=prompt, input_type="text")

    assert result.output_type == "image_path"
    assert result.output_text == str(out_png)
    assert out_png.exists()
    assert LSBImageStegoConverter.decode_image(out_png) == prompt


@pytest.mark.asyncio
async def test_lsb_converter_backward_compatible_with_original_extract_secret(tmp_path: Path) -> None:
    """Verify the default RGBA + '====EOF====' output decodes with the original 13-line extract_secret.py logic."""
    out_png = tmp_path / "deadrop_secret.png"
    serializer = _make_serializer_mock(out_png)

    converter = LSBImageStegoConverter(channels="RGBA", delimiter="====EOF====")
    prompt = "Diagnostic payload: check port 443 TLS cipher suites."

    with patch("pyrit.converter.lsb_image_stego_converter.data_serializer_factory", return_value=serializer):
        await converter.convert_async(prompt=prompt)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        px = [v for p in Image.open(out_png).convert("RGBA").getdata() if isinstance(p, tuple) for v in p]
    out, b, g = "", 0, 0
    for v in px:
        b = (b << 1) | (v & 1)
        g += 1
        if g == 8:
            out += chr(b)
            if out.endswith("====EOF===="):
                break
            b, g = 0, 0
    assert out[:-11] == prompt


@pytest.mark.asyncio
async def test_lsb_converter_multilingual_utf8_roundtrip(tmp_path: Path) -> None:
    out_png = tmp_path / "stego_multilingual.png"
    serializer = _make_serializer_mock(out_png)

    converter = LSBImageStegoConverter(channels="RGB")
    prompt = (
        "EN: System Audit | "
        "RU: Проверка безопасности системы | "
        "UR: سسٹم سیکیورٹی آڈٹ | "
        "ZH: 系统安全审计测试 | "
        "ID: Pengujian keamanan sistem"
    )

    with patch("pyrit.converter.lsb_image_stego_converter.data_serializer_factory", return_value=serializer):
        await converter.convert_async(prompt=prompt)

    assert LSBImageStegoConverter.decode_image(out_png, channels="RGB") == prompt


@pytest.mark.asyncio
async def test_lsb_converter_custom_carrier_pixel_delta_at_most_one(tmp_path: Path) -> None:
    carrier_path = tmp_path / "carrier.png"
    carrier_img = Image.new("RGBA", (80, 80), color=(123, 200, 45, 255))
    carrier_img.save(carrier_path, format="PNG")

    out_png = tmp_path / "stego_carrier.png"
    serializer = _make_serializer_mock(out_png)

    converter = LSBImageStegoConverter(carrier_image_path=carrier_path, channels="RGBA")
    prompt = "Stealth check: pixel channel delta must never exceed 1."

    with patch("pyrit.converter.lsb_image_stego_converter.data_serializer_factory", return_value=serializer):
        await converter.convert_async(prompt=prompt)

    orig_bytes = carrier_img.tobytes()
    stego_bytes = Image.open(out_png).convert("RGBA").tobytes()
    max_delta = max(abs(a - b) for a, b in zip(orig_bytes, stego_bytes, strict=True))
    assert max_delta <= 1
    assert LSBImageStegoConverter.decode_image(out_png) == prompt


@pytest.mark.asyncio
async def test_lsb_converter_carrier_too_small_raises(tmp_path: Path) -> None:
    tiny_carrier = tmp_path / "tiny.png"
    Image.new("RGB", (2, 2), color=(0, 0, 0)).save(tiny_carrier, format="PNG")

    converter = LSBImageStegoConverter(carrier_image_path=tiny_carrier, channels="RGB")
    with pytest.raises(ValueError, match="too small to hold payload"):
        await converter.convert_async(prompt="This prompt requires far more than 12 bits of capacity.")


@pytest.mark.asyncio
async def test_lsb_converter_concurrent_convert_async_isolated_base_image(tmp_path: Path) -> None:
    """Verify concurrent convert_async calls on a shared instance never mutate _base_image or leak bits."""
    carrier_path = tmp_path / "shared_carrier.png"
    carrier_img = Image.new("RGBA", (64, 64), color=(80, 120, 160, 255))
    carrier_img.save(carrier_path, format="PNG")
    expected_base_bytes = carrier_img.tobytes()

    converter = LSBImageStegoConverter(carrier_image_path=carrier_path, channels="RGBA")
    prompts = [f"Concurrent payload #{i}: {'X' * (i * 5 + 1)}" for i in range(8)]
    counter = 0

    def _factory(**_kwargs: object) -> MagicMock:
        nonlocal counter
        out_file = tmp_path / f"concurrent_{counter}.png"
        counter += 1
        return _make_serializer_mock(out_file)

    with patch("pyrit.converter.lsb_image_stego_converter.data_serializer_factory", side_effect=_factory):
        results = await asyncio.gather(*(converter.convert_async(prompt=p) for p in prompts))

    assert converter._base_image is not None
    assert converter._base_image.tobytes() == expected_base_bytes

    for prompt_text, res in zip(prompts, results, strict=True):
        assert LSBImageStegoConverter.decode_image(res.output_text) == prompt_text


@pytest.mark.asyncio
async def test_lsb_auto_scaling_dimensions_and_missing_delimiter(tmp_path: Path) -> None:
    """Verify synthetic carrier auto-scales above min_dimensions and decode_image rejects unencoded images."""
    out_png = tmp_path / "autoscale.png"
    serializer = _make_serializer_mock(out_png)

    converter = LSBImageStegoConverter(
        channels="RGB",
        min_dimensions=(2, 2),
        background_color=(10, 20, 30),
    )
    long_prompt = "Auto-scaling synthetic carrier check! " * 20
    with patch("pyrit.converter.lsb_image_stego_converter.data_serializer_factory", return_value=serializer):
        await converter.convert_async(prompt=long_prompt)

    with Image.open(out_png) as img:
        assert img.width * img.height > 4

    assert LSBImageStegoConverter.decode_image(out_png, channels="RGB") == long_prompt

    blank_buf = BytesIO()
    Image.new("RGBA", (16, 16), color=(255, 255, 255, 255)).save(blank_buf, format="PNG")
    blank_buf.seek(0)
    with pytest.raises(ValueError, match="not found in image LSB stream"):
        LSBImageStegoConverter.decode_image(blank_buf, channels="RGBA")


@pytest.mark.asyncio
async def test_lsb_delimiter_collision_and_partial_utf8_boundary(tmp_path: Path) -> None:
    """
    Verify that delimiter framing:
    1. Rejects prompts containing the full delimiter or suffix-prefix overlap that would cause early truncation.
    2. Round-trips cleanly when multi-byte UTF-8 characters and non-colliding partial delimiters
       appear immediately before the delimiter.
    """
    converter = LSBImageStegoConverter(channels="RGBA", delimiter="====EOF====")

    with pytest.raises(ValueError, match="collides with delimiter"):
        await converter.convert_async(prompt="Prefix ====EOF==== Suffix")

    with pytest.raises(ValueError, match="collides with delimiter"):
        await converter.convert_async(prompt="Payload ending with ====EOF")

    out_png = tmp_path / "partial_utf8_boundary.png"
    serializer = _make_serializer_mock(out_png)
    edge_prompt = "Multi-byte boundary 🔥🚀 === ====EO! Проверка ====EO"
    with patch("pyrit.converter.lsb_image_stego_converter.data_serializer_factory", return_value=serializer):
        await converter.convert_async(prompt=edge_prompt)

    assert LSBImageStegoConverter.decode_image(out_png, channels="RGBA", delimiter="====EOF====") == edge_prompt

    corrupt_framed = b"\xff\xfe" + b"====EOF===="
    raw_corrupt = bytearray(len(corrupt_framed) * 8)
    for idx, byte_val in enumerate(corrupt_framed):
        for shift in range(7, -1, -1):
            raw_corrupt[idx * 8 + (7 - shift)] = (byte_val >> shift) & 1
    corrupt_buf = BytesIO()
    Image.frombytes("RGBA", (len(raw_corrupt) // 4, 1), bytes(raw_corrupt)).save(corrupt_buf, format="PNG")
    corrupt_buf.seek(0)
    with pytest.raises(ValueError, match="not valid UTF-8"):
        LSBImageStegoConverter.decode_image(corrupt_buf, channels="RGBA", delimiter="====EOF====")


@pytest.mark.asyncio
async def test_lsb_decode_converter_pipeline_and_serializer_fallback(tmp_path: Path) -> None:
    """Verify companion LSBImageStegoDecodeConverter works in both file and serializer-backed pipelines."""
    out_png = tmp_path / "pipeline_stego.png"
    serializer = _make_serializer_mock(out_png)

    encoder = LSBImageStegoConverter(channels="RGBA", delimiter="<<END>>")
    decoder = LSBImageStegoDecodeConverter(channels="RGBA", delimiter="<<END>>")

    assert decoder.input_supported("image_path") is True
    assert decoder.input_supported("text") is False
    assert decoder.output_supported("text") is True
    ident = decoder.get_identifier()
    assert ident.params["channels"] == "RGBA"
    assert ident.params["delimiter"] == "<<END>>"

    prompt = "Pipeline round-trip via LSBImageStegoDecodeConverter!"
    with patch("pyrit.converter.lsb_image_stego_converter.data_serializer_factory", return_value=serializer):
        enc_result = await encoder.convert_async(prompt=prompt, input_type="text")

    dec_result = await decoder.convert_async(prompt=enc_result.output_text, input_type="image_path")
    assert dec_result.output_type == "text"
    assert dec_result.output_text == prompt

    read_serializer = MagicMock()
    read_serializer.read_data_async = AsyncMock(return_value=out_png.read_bytes())
    with patch("pyrit.converter.lsb_image_stego_converter.data_serializer_factory", return_value=read_serializer):
        virtual_result = await decoder.convert_async(
            prompt="memory://virtual/nonexistent_stego.png",
            input_type="image_path",
        )
    assert virtual_result.output_text == prompt

    with pytest.raises(ValueError, match="Input type not supported"):
        await decoder.convert_async(prompt=str(out_png), input_type="text")
    with pytest.raises(ValueError, match="Please provide valid image_path value"):
        await decoder.convert_async(prompt="   ", input_type="image_path")


@pytest.mark.asyncio
async def test_lsb_converter_validation_and_identifier() -> None:
    converter = LSBImageStegoConverter(channels="RGB", delimiter="###", background_color=(10, 20, 30))
    other_bg = LSBImageStegoConverter(channels="RGB", delimiter="###", background_color=(40, 50, 60))
    ident = converter.get_identifier()
    assert ident.params["channels"] == "RGB"
    assert ident.params["delimiter"] == "###"
    assert list(ident.params["background_color"]) == [10, 20, 30, 255]
    assert ident != other_bg.get_identifier()
    assert converter.input_supported("text") is True
    assert converter.input_supported("image_path") is False
    assert converter.output_supported("image_path") is True

    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="test", input_type="image_path")

    with pytest.raises(ValueError, match="Please provide valid text value"):
        await converter.convert_async(prompt="   ", input_type="text")

    with pytest.raises(ValueError, match="channels must be either 'RGB' or 'RGBA'"):
        LSBImageStegoConverter(channels="CMYK")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="delimiter must be a non-empty string"):
        LSBImageStegoConverter(delimiter="")

    with pytest.raises(ValueError, match="Please provide a valid carrier_image_path"):
        LSBImageStegoConverter(carrier_image_path="   ")

    with pytest.raises(ValueError, match="Please provide a valid carrier_image_path"):
        LSBImageStegoConverter(carrier_image_path=Path(""))

    with pytest.raises(ValueError, match="min_dimensions must be a \\(width, height\\) tuple with positive integers"):
        LSBImageStegoConverter(min_dimensions=(True, 64))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="min_dimensions must be a \\(width, height\\) tuple with positive integers"):
        LSBImageStegoConverter(min_dimensions=(0, 64))

    with pytest.raises(ValueError, match="background_color must be an RGB or RGBA tuple"):
        LSBImageStegoConverter(background_color=(256, 0, 0, 255))

    with pytest.raises(ValueError, match="background_color must be an RGB or RGBA tuple"):
        LSBImageStegoConverter(background_color=(True, 0, 0, 255))  # type: ignore[arg-type]


@pytest.mark.parametrize("use_path", [False, True])
def test_lsb_converter_registry_accepts_carrier_image_path(tmp_path: Path, *, use_path: bool) -> None:
    carrier_path = tmp_path / "registry_carrier.png"
    with Image.new("RGB", (64, 64)) as carrier:
        carrier.save(carrier_path, format="PNG")

    registry = ConverterRegistry()
    metadata = registry.get_registered_class_metadata("LSBImageStegoConverter")
    assert metadata is not None
    parameter = next(parameter for parameter in metadata.parameters if parameter.name == "carrier_image_path")
    assert parameter.is_external_input
    assert parameter.is_path_or_str

    converter = registry.create_instance_from_external_input(
        "LSBImageStegoConverter",
        params={"carrier_image_path": carrier_path if use_path else str(carrier_path)},
    )
    assert isinstance(converter, LSBImageStegoConverter)
    assert converter.get_identifier().params["carrier_image_path"] == str(carrier_path)


@pytest.mark.usefixtures("patch_central_database")
async def test_lsb_converter_image_processing_keeps_event_loop_responsive() -> None:
    converter = LSBImageStegoConverter()
    loop = asyncio.get_running_loop()
    event_loop_thread_id = get_ident()
    embed_lsb = converter._embed_lsb
    save_image = Image.Image.save

    def embed_in_worker(prompt: str) -> Image.Image:
        _assert_event_loop_responsive(loop=loop, event_loop_thread_id=event_loop_thread_id)
        return embed_lsb(prompt)

    def save_in_worker(image: Image.Image, *, fp: BytesIO, **params: str) -> None:
        _assert_event_loop_responsive(loop=loop, event_loop_thread_id=event_loop_thread_id)
        save_image(image, fp=fp, **params)

    with (
        patch.object(converter, "_embed_lsb", side_effect=embed_in_worker) as embed_mock,
        patch.object(Image.Image, "save", autospec=True, side_effect=save_in_worker) as save_mock,
    ):
        result = await converter.convert_async(prompt="Responsive encoder fixture.")

    embed_mock.assert_called_once()
    save_mock.assert_called_once()
    decoded = await LSBImageStegoDecodeConverter().convert_async(prompt=result.output_text)
    assert decoded.output_text == "Responsive encoder fixture."


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("has_payload", [True, False])
async def test_lsb_decode_converter_keeps_event_loop_responsive(tmp_path: Path, *, has_payload: bool) -> None:
    prompt = "Responsive decoder fixture."
    if has_payload:
        encoded = await LSBImageStegoConverter().convert_async(prompt=prompt)
        image_path = encoded.output_text
    else:
        blank_path = tmp_path / "blank.png"
        with Image.new("RGBA", (16, 16), (255, 255, 255, 255)) as image:
            await asyncio.to_thread(image.save, blank_path, format="PNG")
        image_path = str(blank_path)

    loop = asyncio.get_running_loop()
    event_loop_thread_id = get_ident()
    decode_image = LSBImageStegoConverter.decode_image

    def decode_in_worker(image_path: BytesIO, *, channels: StegoChannelMode, delimiter: str) -> str:
        _assert_event_loop_responsive(loop=loop, event_loop_thread_id=event_loop_thread_id)
        return decode_image(image_path, channels=channels, delimiter=delimiter)

    with patch.object(LSBImageStegoConverter, "decode_image", side_effect=decode_in_worker) as decode_mock:
        decoder = LSBImageStegoDecodeConverter()
        if has_payload:
            result = await decoder.convert_async(prompt=image_path)
            assert result.output_text == prompt
            assert result.output_type == "text"
        else:
            with pytest.raises(ValueError, match="not found in image LSB stream"):
                await decoder.convert_async(prompt=image_path)

    decode_mock.assert_called_once()
