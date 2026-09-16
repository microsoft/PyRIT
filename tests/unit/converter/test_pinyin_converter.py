# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import threading
from random import Random
from unittest.mock import MagicMock, patch

import pytest

from pyrit.converter import ConverterResult, PinyinConverter
from pyrit.converter.pinyin_converter import PinyinMode


async def test_pinyin_full_mode_romanizes_every_hanzi():
    converter = PinyinConverter(mode="full")
    result = await converter.convert_async(prompt="中心", input_type="text")
    assert isinstance(result, ConverterResult)
    assert result.output_text == "zhongxin"
    assert result.output_type == "text"


async def test_pinyin_full_mode_with_separator_keeps_syllables_readable():
    converter = PinyinConverter(mode="full", separator=" ")
    result = await converter.convert_async(prompt="中心", input_type="text")
    # A separator is inserted between syllables but not left trailing.
    assert result.output_text == "zhong xin"


async def test_pinyin_initial_mode_uses_first_letters():
    converter = PinyinConverter(mode="initial")
    result = await converter.convert_async(prompt="中心", input_type="text")
    assert result.output_text == "zx"


async def test_pinyin_leaves_non_hanzi_untouched():
    converter = PinyinConverter(mode="full")
    result = await converter.convert_async(prompt="你好world! 123", input_type="text")
    # Latin letters, punctuation, spaces, and digits pass through unchanged.
    assert result.output_text == "nihaoworld! 123"


async def test_pinyin_separator_only_wraps_romanized_characters():
    converter = PinyinConverter(mode="full", separator="-")
    result = await converter.convert_async(prompt="中a好", input_type="text")
    # The non-Hanzi "a" gets no separator; the trailing separator after 好 is dropped.
    assert result.output_text == "zhong-ahao"


async def test_pinyin_prompt_without_hanzi_is_identity():
    converter = PinyinConverter(mode="full")
    prompt = "the quick brown fox"
    result = await converter.convert_async(prompt=prompt, input_type="text")
    assert result.output_text == prompt


async def test_pinyin_zero_proportion_is_identity():
    converter = PinyinConverter(mode="full", proportion=0.0)
    result = await converter.convert_async(prompt="你好世界", input_type="text")
    assert result.output_text == "你好世界"


async def test_pinyin_partial_proportion_converts_expected_count():
    # 4 Hanzi at proportion 0.5 -> exactly 2 romanized, 2 kept as Hanzi.
    converter = PinyinConverter(mode="full", proportion=0.5, seed=42)
    result = await converter.convert_async(prompt="你好世界", input_type="text")
    output = result.output_text
    remaining_hanzi = [c for c in output if c in "你好世界"]
    assert len(remaining_hanzi) == 2


async def test_pinyin_seed_makes_partial_selection_reproducible():
    prompt = "今天天气很好我们出去玩"
    first = (await PinyinConverter(mode="full", proportion=0.5, seed=7).convert_async(prompt=prompt)).output_text
    second = (await PinyinConverter(mode="full", proportion=0.5, seed=7).convert_async(prompt=prompt)).output_text
    assert first == second


async def test_pinyin_mixed_mode_is_reproducible_with_seed():
    prompt = "今天天气很好"
    first = (await PinyinConverter(mode="mixed", seed=13).convert_async(prompt=prompt)).output_text
    second = (await PinyinConverter(mode="mixed", seed=13).convert_async(prompt=prompt)).output_text
    assert first == second
    # Mixed mode still romanizes everything at proportion 1.0, so no Hanzi remains.
    assert not any("一" <= c <= "鿿" for c in first)


async def test_pinyin_rejects_unsupported_input_type():
    converter = PinyinConverter()
    with pytest.raises(ValueError, match="Input type not supported"):
        await converter.convert_async(prompt="你好", input_type="image_path")


def test_pinyin_rejects_invalid_mode():
    with pytest.raises(ValueError, match="mode must be one of"):
        PinyinConverter(mode="tone")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_pinyin_rejects_out_of_range_proportion(bad):
    with pytest.raises(ValueError, match="proportion must be between 0.0 and 1.0"):
        PinyinConverter(proportion=bad)


def test_pinyin_identifier_includes_parameters():
    converter = PinyinConverter(mode="initial", proportion=0.25, separator=" ", seed=99)
    identifier = converter.get_identifier()
    assert identifier.class_name == "PinyinConverter"
    assert identifier.params["mode"] == "initial"
    assert identifier.params["proportion"] == 0.25
    assert identifier.params["separator"] == " "
    assert identifier.params["seed"] == 99


@pytest.mark.parametrize(
    ("mode", "prompt", "expected"),
    [
        ("full", "银行", "yinhang"),
        ("initial", "银行", "yh"),
        ("full", "重庆", "chongqing"),
        ("initial", "重庆", "cq"),
        ("full", "音乐", "yinyue"),
        ("initial", "音乐", "yy"),
        ("full", "abc 银行! 123\n重庆 \U0001f600", "abc yinhang! 123\nchongqing \U0001f600"),
        ("initial", "abc 银行! 123\n重庆 \U0001f600", "abc yh! 123\ncq \U0001f600"),
    ],
)
async def test_pinyin_preserves_phrase_context_async(*, mode: PinyinMode, prompt: str, expected: str) -> None:
    result = await PinyinConverter(mode=mode).convert_async(prompt=prompt)

    assert result.output_text == expected


@pytest.mark.parametrize(
    ("mode", "choice_index", "expected"),
    [
        ("full", 0, "银hang"),
        ("initial", 0, "银h"),
        ("mixed", 0, "银hang"),
        ("mixed", 1, "银h"),
    ],
)
async def test_pinyin_partial_phrase_uses_unselected_context_async(
    *, mode: PinyinMode, choice_index: int, expected: str
) -> None:
    converter = PinyinConverter(mode=mode, proportion=0.5)
    rng = MagicMock(spec=Random)
    rng.sample.return_value = [1]
    rng.choice.side_effect = lambda choices: choices[choice_index]

    with patch.object(converter, "_get_random_generator", return_value=rng):
        result = await converter.convert_async(prompt="银行")

    assert result.output_text == expected


@pytest.mark.parametrize("proportion", [0.0, 0.1])
@pytest.mark.parametrize("separator", [" ", "\n", "!", "好"])
async def test_pinyin_preserves_original_trailing_characters_async(*, proportion: float, separator: str) -> None:
    prompt = f"你好{separator}"
    converter = PinyinConverter(proportion=proportion, separator=separator)

    result = await converter.convert_async(prompt=prompt)

    assert result.output_text == prompt


@pytest.mark.parametrize("separator", [" ", "\n", "!", "a"])
async def test_pinyin_preserves_original_separator_after_conversion_async(separator: str) -> None:
    converter = PinyinConverter(separator=separator)

    result = await converter.convert_async(prompt=f"中{separator}")

    assert result.output_text == f"zhong{separator}{separator}"


@pytest.mark.parametrize(
    ("mode", "prompt", "expected"),
    [
        ("full", "〇", "ling"),
        ("initial", "〇", "l"),
        ("full", "二〇二六", "erlingerliu"),
        ("full", "\U00030021", "qian"),
        ("initial", "\U00030021", "q"),
        ("full", "\U00031350", "qi"),
        ("initial", "\U00031350", "q"),
    ],
)
async def test_pinyin_romanizes_extended_hanzi_async(*, mode: PinyinMode, prompt: str, expected: str) -> None:
    result = await PinyinConverter(mode=mode).convert_async(prompt=prompt)

    assert result.output_text == expected


@pytest.mark.parametrize(
    "character",
    [
        "〇",
        "\U0002ebf0",
        "\U0002ee5f",
        "\U00030000",
        "\U0003134f",
        "\U00031350",
        "\U000323af",
        "\U000323b0",
        "\U0003347f",
    ],
)
async def test_pinyin_partial_count_includes_extended_hanzi_async(character: str) -> None:
    converter = PinyinConverter(proportion=0.5)
    rng = MagicMock(spec=Random)
    rng.sample.return_value = [1]

    with patch.object(converter, "_get_random_generator", return_value=rng):
        result = await converter.convert_async(prompt=f"{character}中")

    rng.sample.assert_called_once()
    assert rng.sample.call_args.args == ([0, 1], 1)
    assert result.output_text == f"{character}zhong"


@pytest.mark.parametrize("mode", ["full", "initial", "mixed"])
async def test_pinyin_preserves_extended_hanzi_without_readings_async(mode: PinyinMode) -> None:
    prompt = "\U0002ebf0\U000323b0"
    converter = PinyinConverter(mode=mode, separator="-")

    with patch.object(converter, "_get_pinyin_readings", return_value=list(prompt)) as readings:
        result = await converter.convert_async(prompt=prompt)

    readings.assert_called_once()
    assert result.output_text == prompt


async def test_pinyin_readings_do_not_block_event_loop_async() -> None:
    converter = PinyinConverter()
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    started = asyncio.Event()
    release = threading.Event()

    def blocking_readings(prompt: str) -> list[str]:
        assert threading.get_ident() != loop_thread
        assert prompt == "中心"
        loop.call_soon_threadsafe(started.set)
        if not release.wait(timeout=5):
            raise TimeoutError("The event loop did not release the dictionary lookup")
        return ["zhong", "xin"]

    with patch.object(converter, "_get_pinyin_readings", side_effect=blocking_readings):
        conversion = asyncio.create_task(converter.convert_async(prompt="中心"))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            assert not conversion.done()
        finally:
            release.set()
            result = await asyncio.wait_for(conversion, timeout=5)

    assert result.output_text == "zhongxin"


async def test_pinyin_reading_errors_propagate_async() -> None:
    converter = PinyinConverter()
    with (
        patch.object(converter, "_get_pinyin_readings", side_effect=RuntimeError("Dictionary lookup failed")),
        pytest.raises(RuntimeError, match="Dictionary lookup failed"),
    ):
        await converter.convert_async(prompt="中心")
