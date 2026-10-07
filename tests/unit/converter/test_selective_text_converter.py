# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import AsyncMock, call, patch

import pytest

from pyrit.common.random_context import configure_random_seed, get_configured_random_seed
from pyrit.converter import (
    Base64Converter,
    Converter,
    ConverterResult,
    LeetspeakConverter,
    RandomCapitalLettersConverter,
    ROT13Converter,
    SelectiveTextConverter,
)
from pyrit.converter.text_selection_strategy import (
    IndexSelectionStrategy,
    KeywordSelectionStrategy,
    PositionSelectionStrategy,
    ProportionSelectionStrategy,
    RangeSelectionStrategy,
    RegexSelectionStrategy,
    TokenSelectionStrategy,
    WordIndexSelectionStrategy,
    WordPositionSelectionStrategy,
    WordProportionSelectionStrategy,
    WordRegexSelectionStrategy,
)
from pyrit.models import Message, PromptDataType
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer


@pytest.mark.parametrize("token_entry", [False, True])
@pytest.mark.parametrize("preserve_tokens", [False, True])
@pytest.mark.parametrize(
    ("prompt", "consumed", "preserved"),
    [
        (
            "keep ⟪⟪test⟫⟫ / ⟪⟪test2⟫⟫",
            "keep ⟪dGVzdA==⟫ / ⟪dGVzdDI=⟫",
            "keep ⟪⟪dGVzdA==⟫⟫ / ⟪⟪dGVzdDI=⟫⟫",
        ),
        ("keep ⟪test⟫ / ⟪test2⟫", "keep dGVzdA== / dGVzdDI=", "keep ⟪dGVzdA==⟫ / ⟪dGVzdDI=⟫"),
        ("⟪test⟫⟪test2⟫", "dGVzdA==dGVzdDI=", "⟪dGVzdA==⟫⟪dGVzdDI=⟫"),
        (
            "keep ⟪⟪test⟫⟫ / ⟪test2⟫",
            "keep ⟪dGVzdA==⟫ / dGVzdDI=",
            "keep ⟪⟪dGVzdA==⟫⟫ / ⟪dGVzdDI=⟫",
        ),
        (
            "keep ⟪outer ⟪test⟫ and ⟪test2⟫ end⟫",
            "keep ⟪outer dGVzdA== and dGVzdDI= end⟫",
            "keep ⟪outer ⟪dGVzdA==⟫ and ⟪dGVzdDI=⟫ end⟫",
        ),
        ("keep ⟪⟫ / ⟪⟪⟫⟫", "keep  / ⟪⟫", "keep ⟪⟫ / ⟪⟪⟫⟫"),
        ("test", "dGVzdA==", "⟪dGVzdA==⟫"),
    ],
)
async def test_token_selection_entry_paths_async(
    *, prompt: str, consumed: str, preserved: str, preserve_tokens: bool, token_entry: bool
) -> None:
    converter = SelectiveTextConverter(
        sub_converter=Base64Converter(),
        selection_strategy=TokenSelectionStrategy(),
        preserve_tokens=preserve_tokens,
    )
    result = (
        await converter.convert_tokens_async(prompt=prompt)
        if token_entry
        else await converter.convert_async(prompt=prompt)
    )
    assert result.output_text == (preserved if preserve_tokens else consumed)
    assert result.output_type == "text"


@pytest.mark.parametrize("preserve_tokens", [False, True])
@pytest.mark.parametrize(("start_token", "end_token"), [("<<", ">>"), ("[.*", ".*]"), ("|", "|")])
async def test_token_selection_custom_delimiter_entry_paths_async(
    *, preserve_tokens: bool, start_token: str, end_token: str
) -> None:
    converter = SelectiveTextConverter(
        sub_converter=Base64Converter(),
        selection_strategy=TokenSelectionStrategy(),
        preserve_tokens=preserve_tokens,
        start_token=start_token,
        end_token=end_token,
    )
    prompt = f"keep {start_token}test{end_token} / {start_token}test2{end_token}"
    expected = (
        f"keep {start_token}dGVzdA=={end_token} / {start_token}dGVzdDI={end_token}"
        if preserve_tokens
        else "keep dGVzdA== / dGVzdDI="
    )
    direct = await converter.convert_async(prompt=prompt)
    selected = await converter.convert_tokens_async(prompt=prompt, start_token=start_token, end_token=end_token)
    assert direct.output_text == selected.output_text == expected


@pytest.mark.parametrize("keep_tokens", [False, True])
@pytest.mark.parametrize("preserve_tokens", [False, True])
async def test_token_selection_keep_tokens_retains_the_call_markers_async(
    *, keep_tokens: bool, preserve_tokens: bool
) -> None:
    converter = SelectiveTextConverter(
        sub_converter=Base64Converter(),
        selection_strategy=TokenSelectionStrategy(),
        preserve_tokens=preserve_tokens,
    )
    result = await converter.convert_tokens_async(
        prompt="keep ⟪literal⟫ [[test]]", start_token="[", end_token="]", keep_tokens=keep_tokens
    )
    expected = "keep ⟪literal⟫ [[dGVzdA==]]" if keep_tokens or preserve_tokens else "keep ⟪literal⟫ [dGVzdA==]"
    assert result.output_text == expected


async def test_legacy_token_override_rejects_keep_tokens_without_calling_it_async() -> None:
    legacy = _LegacyTokenConverter()
    converter = SelectiveTextConverter(sub_converter=legacy, selection_strategy=TokenSelectionStrategy())
    with patch.object(legacy, "convert_tokens_async", wraps=legacy.convert_tokens_async) as convert:
        with pytest.raises(ValueError, match="custom convert_tokens_async override"):
            await converter.convert_tokens_async(prompt="a ⟪b⟫ c", keep_tokens=True)
    convert.assert_not_awaited()


@pytest.mark.parametrize("token_entry", [False, True])
async def test_token_selection_preserves_generated_markers_async(*, token_entry: bool) -> None:
    sub_converter = Base64Converter()
    converter = SelectiveTextConverter(
        sub_converter=sub_converter,
        selection_strategy=TokenSelectionStrategy(),
        preserve_tokens=True,
    )
    with patch.object(sub_converter, "convert_async", new_callable=AsyncMock) as convert:
        convert.return_value = ConverterResult(output_text="generated ⟪new⟫", output_type="text")
        result = (
            await converter.convert_tokens_async(prompt="keep ⟪⟪test⟫⟫ after")
            if token_entry
            else await converter.convert_async(prompt="keep ⟪⟪test⟫⟫ after")
        )
    assert convert.await_args_list == [call(prompt="test", input_type="text")]
    assert result.output_text == "keep ⟪⟪generated ⟪new⟫⟫⟫ after"


@pytest.mark.parametrize("token_entry", [False, True])
@pytest.mark.parametrize("prompt", ["⟪outer ⟪inner⟫", "⟪valid⟫ then ⟫"])
async def test_token_selection_rejects_malformed_regions_async(*, prompt: str, token_entry: bool) -> None:
    sub_converter = Base64Converter()
    converter = SelectiveTextConverter(
        sub_converter=sub_converter,
        selection_strategy=TokenSelectionStrategy(),
        preserve_tokens=True,
    )
    with patch.object(sub_converter, "convert_async", new_callable=AsyncMock) as convert:
        with pytest.raises(ValueError, match="Unmatched"):
            if token_entry:
                await converter.convert_tokens_async(prompt=prompt)
            else:
                await converter.convert_async(prompt=prompt)
    convert.assert_not_awaited()


@pytest.mark.parametrize("preserve_tokens", [False, True])
async def test_token_selection_rejects_nontext_output_async(*, preserve_tokens: bool) -> None:
    sub_converter = Base64Converter()
    converter = SelectiveTextConverter(
        sub_converter=sub_converter,
        selection_strategy=TokenSelectionStrategy(),
        preserve_tokens=preserve_tokens,
    )
    with patch.object(sub_converter, "convert_async", new_callable=AsyncMock) as convert:
        convert.return_value = ConverterResult(output_text="output.png", output_type="image_path")
        with pytest.raises(ValueError, match="text output"):
            await converter.convert_async(prompt="⟪test⟫")


@pytest.mark.parametrize("token_entry", [False, True])
@pytest.mark.parametrize("outer_preserves", [False, True])
@pytest.mark.parametrize("inner_preserves", [False, True])
@pytest.mark.parametrize("depth", [1, 2])
async def test_nested_token_wrappers_preserve_one_pair_per_selection_async(
    *, token_entry: bool, outer_preserves: bool, inner_preserves: bool, depth: int
) -> None:
    inner = SelectiveTextConverter(
        sub_converter=ROT13Converter(),
        selection_strategy=TokenSelectionStrategy(),
        preserve_tokens=inner_preserves,
    )
    outer = SelectiveTextConverter(
        sub_converter=inner,
        selection_strategy=TokenSelectionStrategy(),
        preserve_tokens=outer_preserves,
    )
    prompt = f"prefix {'⟪' * depth}word{'⟫' * depth} suffix"
    result = (
        await outer.convert_tokens_async(prompt=prompt) if token_entry else await outer.convert_async(prompt=prompt)
    )
    remaining = depth if outer_preserves or inner_preserves else depth - 1
    assert result.output_text == f"prefix {'⟪' * remaining}jbeq{'⟫' * remaining} suffix"
    if remaining:
        later = await Base64Converter().convert_tokens_async(prompt=result.output_text)
        assert later.output_text == f"prefix {'⟪' * (remaining - 1)}amJlcQ=={'⟫' * (remaining - 1)} suffix"


async def test_nested_programmatic_wrapper_keeps_its_separate_selection_async() -> None:
    inner = SelectiveTextConverter(
        sub_converter=Base64Converter(),
        selection_strategy=WordRegexSelectionStrategy(pattern=r"\d+"),
        preserve_tokens=True,
    )
    outer = SelectiveTextConverter(
        sub_converter=inner, selection_strategy=TokenSelectionStrategy(), preserve_tokens=True
    )
    result = await outer.convert_async(prompt="keep ⟪code 123⟫ after")
    assert result.output_text == "keep ⟪code ⟪MTIz⟫⟫ after"


async def test_custom_selective_subclass_conversion_is_not_collapsed_async() -> None:
    class CustomSelectiveConverter(SelectiveTextConverter):
        async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
            return ConverterResult(output_text=prompt.upper(), output_type="text")

    inner = CustomSelectiveConverter(
        sub_converter=ROT13Converter(), selection_strategy=TokenSelectionStrategy(), preserve_tokens=True
    )
    outer = SelectiveTextConverter(
        sub_converter=inner, selection_strategy=TokenSelectionStrategy(), preserve_tokens=True
    )
    result = await outer.convert_async(prompt="keep ⟪word⟫ after")
    assert result.output_text == "keep ⟪WORD⟫ after"


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("pipeline_entry", [False, True])
@pytest.mark.parametrize("marked", [False, True])
async def test_top_level_selective_subclass_override_is_used_async(*, pipeline_entry: bool, marked: bool) -> None:
    class CustomSelectiveConverter(SelectiveTextConverter):
        async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
            return ConverterResult(output_text=prompt.upper(), output_type="text")

    converter = CustomSelectiveConverter(sub_converter=ROT13Converter(), selection_strategy=TokenSelectionStrategy())
    prompt = "keep ⟪word⟫ after" if marked else "word"
    if pipeline_entry:
        message = Message.from_prompt(prompt=prompt, role="user")
        await PromptNormalizer().convert_values_async(
            converter_configurations=[ConverterConfiguration(converters=[converter])], message=message
        )
        value = message.get_value()
    else:
        value = (await converter.convert_tokens_async(prompt=prompt)).output_text
    assert value == ("keep WORD after" if marked else "WORD")


@pytest.mark.parametrize("marked", [False, True])
async def test_selective_subclass_can_delegate_to_super_async(*, marked: bool) -> None:
    class DelegatingSelectiveConverter(SelectiveTextConverter):
        async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
            result = await super().convert_async(prompt=prompt, input_type=input_type)
            return ConverterResult(output_text=f"{result.output_text}!", output_type="text")

    converter = DelegatingSelectiveConverter(
        sub_converter=ROT13Converter(), selection_strategy=TokenSelectionStrategy()
    )
    prompt = "keep ⟪word⟫ after" if marked else "word"
    result = await converter.convert_tokens_async(prompt=prompt)
    assert result.output_text == ("keep jbeq! after" if marked else "jbeq!")


async def test_selective_subclass_can_inherit_native_selection_async() -> None:
    class InheritedSelectiveConverter(SelectiveTextConverter):
        pass

    converter = InheritedSelectiveConverter(
        sub_converter=ROT13Converter(), selection_strategy=TokenSelectionStrategy(), preserve_tokens=True
    )
    result = await converter.convert_tokens_async(prompt="keep ⟪word⟫ after")
    assert result.output_text == "keep ⟪jbeq⟫ after"


async def test_selective_subclass_token_override_is_used_by_direct_conversion_async() -> None:
    class CustomTokenSelectiveConverter(SelectiveTextConverter):
        async def convert_tokens_async(
            self, *, prompt: str, input_type: PromptDataType = "text", start_token: str = "⟪", end_token: str = "⟫"
        ) -> ConverterResult:
            return ConverterResult(output_text=prompt.upper(), output_type="text")

    converter = CustomTokenSelectiveConverter(
        sub_converter=ROT13Converter(), selection_strategy=TokenSelectionStrategy()
    )
    result = await converter.convert_async(prompt="word")
    assert result.output_text == "WORD"


async def test_selective_subclass_randomness_keeps_its_own_operation_scope_async() -> None:
    class RandomSelectiveConverter(SelectiveTextConverter):
        async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
            generator = self._get_random_generator(stream="custom-conversion")
            value = "".join(character.upper() if generator.random() < 0.5 else character for character in prompt)
            return ConverterResult(output_text=value, output_type="text")

    original_seed = get_configured_random_seed()
    configure_random_seed(seed=42)
    try:
        converter = RandomSelectiveConverter(
            sub_converter=ROT13Converter(), selection_strategy=TokenSelectionStrategy()
        )
        direct = await converter.convert_async(prompt="abcdefghijklmno")
        selected = await converter.convert_tokens_async(prompt="keep ⟪abcdefghijklmno⟫ after")
        repeated = await converter.convert_async(prompt="abcdefghijklmno")
        assert direct.output_text == repeated.output_text
        assert selected.output_text == f"keep {direct.output_text} after"
    finally:
        configure_random_seed(seed=original_seed)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("pipeline_entry", [False, True])
@pytest.mark.parametrize("inner_uses_call_markers", [False, True])
async def test_nested_wrappers_use_active_call_markers_async(
    *, pipeline_entry: bool, inner_uses_call_markers: bool
) -> None:
    inner = SelectiveTextConverter(
        sub_converter=Base64Converter(),
        selection_strategy=TokenSelectionStrategy(),
        preserve_tokens=True,
        start_token="[" if inner_uses_call_markers else "⟪",
        end_token="]" if inner_uses_call_markers else "⟫",
    )
    outer = SelectiveTextConverter(
        sub_converter=inner, selection_strategy=TokenSelectionStrategy(), preserve_tokens=True
    )
    prompt = "keep [test] after" if inner_uses_call_markers else "keep [⟪test⟫] after"
    if pipeline_entry:
        message = Message.from_prompt(prompt=prompt, role="user")
        await PromptNormalizer(start_token="[", end_token="]").convert_values_async(
            converter_configurations=[ConverterConfiguration(converters=[outer])], message=message
        )
        value = message.get_value()
    else:
        value = (await outer.convert_tokens_async(prompt=prompt, start_token="[", end_token="]")).output_text
    expected = "keep [dGVzdA==] after" if inner_uses_call_markers else "keep [⟪dGVzdA==⟫] after"
    assert value == expected


async def test_concurrent_nested_wrappers_do_not_share_call_markers_async() -> None:
    inner = SelectiveTextConverter(
        sub_converter=Base64Converter(), selection_strategy=TokenSelectionStrategy(), preserve_tokens=True
    )
    outer = SelectiveTextConverter(
        sub_converter=inner, selection_strategy=TokenSelectionStrategy(), preserve_tokens=True
    )
    square, unicode = await asyncio.gather(
        outer.convert_tokens_async(prompt="keep [⟪test⟫] after", start_token="[", end_token="]"),
        outer.convert_tokens_async(prompt="keep ⟪⟪test⟫⟫ after"),
    )
    assert square.output_text == "keep [⟪dGVzdA==⟫] after"
    assert unicode.output_text == "keep ⟪⟪dGVzdA==⟫⟫ after"


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("pipeline_entry", [False, True])
async def test_three_stage_selective_chain_preserves_original_regions_async(*, pipeline_entry: bool) -> None:
    converters = [
        SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=WordPositionSelectionStrategy(start_proportion=0.5, end_proportion=1.0),
            preserve_tokens=True,
        ),
        SelectiveTextConverter(
            sub_converter=ROT13Converter(), selection_strategy=TokenSelectionStrategy(), preserve_tokens=True
        ),
        SelectiveTextConverter(
            sub_converter=Base64Converter(), selection_strategy=TokenSelectionStrategy(), preserve_tokens=True
        ),
    ]
    expected = [
        "tell me how ⟪dG8=⟫ ⟪ZG8=⟫ ⟪aXQ=⟫",
        "tell me how ⟪qT8=⟫ ⟪MT8=⟫ ⟪nKD=⟫",
        "tell me how ⟪cVQ4PQ==⟫ ⟪TVQ4PQ==⟫ ⟪bktEPQ==⟫",
    ]
    value = "tell me how to do it"
    normalizer = PromptNormalizer()
    for converter, output in zip(converters, expected, strict=True):
        if pipeline_entry:
            message = Message.from_prompt(prompt=value, role="user")
            await normalizer.convert_values_async(
                converter_configurations=[ConverterConfiguration(converters=[converter])], message=message
            )
            value = message.get_value()
        else:
            value = (await converter.convert_async(prompt=value)).output_text
        assert value == output


class _LegacyTokenConverter(ROT13Converter):
    async def convert_tokens_async(
        self, *, prompt: str, input_type: PromptDataType = "text", start_token: str = "⟪", end_token: str = "⟫"
    ) -> ConverterResult:
        return ConverterResult(
            output_text=prompt.replace(start_token, "").replace(end_token, "").upper(), output_type="text"
        )


@pytest.mark.parametrize("token_entry", [False, True])
@pytest.mark.parametrize("nested_wrapper", [False, True])
@pytest.mark.parametrize("matching_markers", [False, True])
async def test_legacy_token_override_is_delegated_without_new_arguments_async(
    *, token_entry: bool, nested_wrapper: bool, matching_markers: bool
) -> None:
    legacy = _LegacyTokenConverter()
    sub_converter: Converter = legacy
    if nested_wrapper:
        sub_converter = SelectiveTextConverter(
            sub_converter=legacy,
            selection_strategy=TokenSelectionStrategy(),
            start_token="[" if matching_markers else "⟪",
            end_token="]" if matching_markers else "⟫",
        )
    converter = SelectiveTextConverter(
        sub_converter=sub_converter, selection_strategy=TokenSelectionStrategy(), start_token="[", end_token="]"
    )
    with patch.object(legacy, "convert_tokens_async", wraps=legacy.convert_tokens_async) as convert:
        result = (
            await converter.convert_tokens_async(prompt="a [b] c", start_token="[", end_token="]")
            if token_entry
            else await converter.convert_async(prompt="a [b] c")
        )
    # A different marker pair is a separate selection, not a wrapper to collapse.
    assert result.output_text == ("a B c" if nested_wrapper and not matching_markers else "A B C")
    assert convert.await_count == 1
    assert "keep_tokens" not in convert.await_args.kwargs


@pytest.mark.parametrize("token_entry", [False, True])
@pytest.mark.parametrize("nested_wrapper", [False, True])
async def test_legacy_token_override_preservation_fails_before_conversion_async(
    *, token_entry: bool, nested_wrapper: bool
) -> None:
    legacy = _LegacyTokenConverter()
    converter = SelectiveTextConverter(
        sub_converter=legacy, selection_strategy=TokenSelectionStrategy(), preserve_tokens=True
    )
    if nested_wrapper:
        converter = SelectiveTextConverter(sub_converter=converter, selection_strategy=TokenSelectionStrategy())
    with patch.object(legacy, "convert_tokens_async", wraps=legacy.convert_tokens_async) as convert:
        with pytest.raises(ValueError, match="custom convert_tokens_async override"):
            if token_entry:
                await converter.convert_tokens_async(prompt="a ⟪b⟫ c")
            else:
                await converter.convert_async(prompt="a ⟪b⟫ c")
    convert.assert_not_awaited()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("preserve_tokens", [False, True])
@pytest.mark.parametrize(("start_token", "end_token"), [("⟪", "⟫"), ("<<", ">>")])
@pytest.mark.parametrize("nested_wrapper", [False, True])
@pytest.mark.parametrize("inner_preserves", [False, True])
async def test_token_selection_seeded_pipeline_entry_paths_async(
    *, preserve_tokens: bool, start_token: str, end_token: str, nested_wrapper: bool, inner_preserves: bool
) -> None:
    original_seed = get_configured_random_seed()
    configure_random_seed(seed=42)
    try:
        sub_converter: Converter = RandomCapitalLettersConverter(percentage=50)
        if nested_wrapper:
            sub_converter = SelectiveTextConverter(
                sub_converter=sub_converter,
                selection_strategy=TokenSelectionStrategy(),
                preserve_tokens=inner_preserves,
                start_token=start_token,
                end_token=end_token,
            )
        converter = SelectiveTextConverter(
            sub_converter=sub_converter,
            selection_strategy=TokenSelectionStrategy(),
            preserve_tokens=preserve_tokens,
            start_token=start_token,
            end_token=end_token,
        )
        prompt = (
            f"keep {start_token}{start_token}abcdefghijklmno{end_token}{end_token} "
            f"and {start_token}{start_token}pqrstuvwxyz{end_token}{end_token}"
        )
        direct = await converter.convert_async(prompt=prompt)
        selected = await converter.convert_tokens_async(prompt=prompt, start_token=start_token, end_token=end_token)
        message = Message.from_prompt(prompt=prompt, role="user")
        await PromptNormalizer(start_token=start_token, end_token=end_token).convert_values_async(
            converter_configurations=[ConverterConfiguration(converters=[converter])],
            message=message,
        )
        assert direct.output_text == selected.output_text == message.get_value()
        assert (await converter.convert_async(prompt=prompt)).output_text == direct.output_text
    finally:
        configure_random_seed(seed=original_seed)


class TestSelectiveTextConverter:
    async def test_initialization_valid(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
        )
        assert converter is not None

    async def test_initialization_with_preserve_tokens(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
            preserve_tokens=True,
            start_token="<<",
            end_token=">>",
        )
        assert converter is not None

    async def test_initialization_invalid_converter_input_type(self):
        # Create a mock converter that doesn't support text input
        class NonTextConverter(Base64Converter):
            def input_supported(self, input_type):
                return False

        with pytest.raises(ValueError, match="does not support text input"):
            SelectiveTextConverter(
                sub_converter=NonTextConverter(),
                selection_strategy=IndexSelectionStrategy(start=0, end=5),
            )

    async def test_initialization_invalid_converter_output_type(self):
        # Create a mock converter that doesn't support text output
        class NonTextConverter(Base64Converter):
            def output_supported(self, output_type):
                return False

        with pytest.raises(ValueError, match="does not support text output"):
            SelectiveTextConverter(
                sub_converter=NonTextConverter(),
                selection_strategy=IndexSelectionStrategy(start=0, end=5),
            )

    async def test_convert_async_with_index_strategy(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
        )
        result = await converter.convert_async(prompt="Hello World", input_type="text")
        # "Hello" in base64 is "SGVsbG8="  # noqa: ERA001
        assert result.output_text == "SGVsbG8= World"
        assert result.output_type == "text"

    async def test_convert_async_with_regex_strategy(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=RegexSelectionStrategy(pattern=r"\d+"),
        )
        result = await converter.convert_async(prompt="The code is 12345 here", input_type="text")
        # "12345" in base64 is "MTIzNDU="  # noqa: ERA001
        assert result.output_text == "The code is MTIzNDU= here"
        assert result.output_type == "text"

    async def test_convert_async_with_keyword_strategy(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=KeywordSelectionStrategy(keyword="secret"),
        )
        result = await converter.convert_async(prompt="The secret is here", input_type="text")
        # "secret" in base64 is "c2VjcmV0"  # noqa: ERA001
        assert result.output_text == "The c2VjcmV0 is here"
        assert result.output_type == "text"

    async def test_convert_async_with_position_strategy(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=PositionSelectionStrategy(start_proportion=0.0, end_proportion=0.5),
        )
        result = await converter.convert_async(prompt="0123456789", input_type="text")
        # "01234" in base64 is "MDEyMzQ="  # noqa: ERA001
        assert result.output_text == "MDEyMzQ=56789"
        assert result.output_type == "text"

    async def test_convert_async_with_proportion_strategy(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=ProportionSelectionStrategy(proportion=0.5, anchor="start"),
        )
        result = await converter.convert_async(prompt="0123456789", input_type="text")
        # "01234" in base64 is "MDEyMzQ="  # noqa: ERA001
        assert result.output_text == "MDEyMzQ=56789"
        assert result.output_type == "text"

    async def test_convert_async_with_range_strategy(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=RangeSelectionStrategy(start_proportion=0.0, end_proportion=0.5),
        )
        result = await converter.convert_async(prompt="0123456789", input_type="text")
        # "01234" in base64 is "MDEyMzQ="  # noqa: ERA001
        assert result.output_text == "MDEyMzQ=56789"
        assert result.output_type == "text"

    async def test_convert_async_with_preserve_tokens(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
            preserve_tokens=True,
        )
        result = await converter.convert_async(prompt="Hello World", input_type="text")
        # "Hello" in base64 is "SGVsbG8="  # noqa: ERA001
        assert result.output_text == "⟪SGVsbG8=⟫ World"
        assert result.output_type == "text"

    async def test_convert_async_with_custom_tokens(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
            preserve_tokens=True,
            start_token="<<",
            end_token=">>",
        )
        result = await converter.convert_async(prompt="Hello World", input_type="text")
        # "Hello" in base64 is "SGVsbG8="  # noqa: ERA001
        assert result.output_text == "<<SGVsbG8=>> World"
        assert result.output_type == "text"

    async def test_convert_async_no_match_returns_original(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=RegexSelectionStrategy(pattern=r"\d+"),
        )
        result = await converter.convert_async(prompt="No numbers here", input_type="text")
        assert result.output_text == "No numbers here"
        assert result.output_type == "text"

    async def test_convert_async_invalid_input_type(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
        )
        with pytest.raises(ValueError, match="only supports text input"):
            await converter.convert_async(prompt="Hello", input_type="image_path")

    async def test_convert_async_chaining_with_preserved_tokens(self):
        # First converter: convert first half with preserve_tokens
        converter1 = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=PositionSelectionStrategy(start_proportion=0.0, end_proportion=0.5),
            preserve_tokens=True,
        )
        result1 = await converter1.convert_async(prompt="HelloWorld", input_type="text")

        # Second converter: convert second half with preserve_tokens
        converter2 = SelectiveTextConverter(
            sub_converter=ROT13Converter(),
            selection_strategy=PositionSelectionStrategy(start_proportion=0.5, end_proportion=1.0),
            preserve_tokens=True,
        )
        result2 = await converter2.convert_async(prompt="HelloWorld", input_type="text")

        # Verify both conversions can be identified
        assert "⟪" in result1.output_text
        assert "⟫" in result1.output_text
        assert "⟪" in result2.output_text
        assert "⟫" in result2.output_text

    async def test_convert_async_middle_section(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=4, end=10),
        )
        result = await converter.convert_async(prompt="The secret code", input_type="text")
        # "secret" in base64 is "c2VjcmV0"  # noqa: ERA001
        assert result.output_text == "The c2VjcmV0 code"
        assert result.output_type == "text"

    async def test_convert_async_end_section(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=11, end=None),
        )
        result = await converter.convert_async(prompt="Hello World", input_type="text")
        # "" (empty string) in base64 is ""
        assert result.output_text == "Hello World"
        assert result.output_type == "text"

    async def test_input_supported(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
        )
        assert converter.input_supported("text") is True
        assert converter.input_supported("image_path") is False

    async def test_output_supported(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
        )
        assert converter.output_supported("text") is True
        assert converter.output_supported("image_path") is False

    async def test_convert_async_with_keyword_and_context(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=KeywordSelectionStrategy(keyword="secret", context_before=4, context_after=3),
        )
        result = await converter.convert_async(prompt="The secret is here", input_type="text")
        # "The secret is" in base64 is "VGhlIHNlY3JldCBpcw=="  # noqa: ERA001
        assert result.output_text == "VGhlIHNlY3JldCBpcw== here"
        assert result.output_type == "text"

    async def test_convert_async_entire_text_with_range(self):
        converter = SelectiveTextConverter(
            sub_converter=ROT13Converter(),
            selection_strategy=RangeSelectionStrategy(start_proportion=0.0, end_proportion=1.0),
        )
        result = await converter.convert_async(prompt="Hello", input_type="text")
        assert result.output_text == "Uryyb"
        assert result.output_type == "text"

    async def test_initialization_word_level_strategy_with_word_level_converter_raises(self):
        """Test that using a word-level selection strategy with a WordLevelConverter
        that has a non-default word_selection_strategy raises ValueError."""
        with pytest.raises(ValueError, match="Cannot use a WordSelectionStrategy"):
            SelectiveTextConverter(
                sub_converter=LeetspeakConverter(
                    word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5)
                ),
                selection_strategy=WordIndexSelectionStrategy(indices=[0, 1]),
            )

    async def test_initialization_word_level_strategy_with_default_word_level_converter_allowed(self):
        """Test that using a word-level selection strategy with a WordLevelConverter
        that has the default (AllWordsSelectionStrategy) is allowed."""
        # This should NOT raise - LeetspeakConverter with no explicit strategy uses AllWordsSelectionStrategy
        converter = SelectiveTextConverter(
            sub_converter=LeetspeakConverter(),
            selection_strategy=WordIndexSelectionStrategy(indices=[0]),
        )
        assert converter is not None

    async def test_initialization_char_level_strategy_with_word_level_converter_allowed(self):
        """Test that using a character-level selection strategy with a WordLevelConverter
        that has a non-default word_selection_strategy is allowed (this is meaningful)."""
        # This should NOT raise - character-level strategy passes a substring to the converter,
        # so the converter's word selection strategy can meaningfully operate on it
        converter = SelectiveTextConverter(
            sub_converter=LeetspeakConverter(word_selection_strategy=WordProportionSelectionStrategy(proportion=0.5)),
            selection_strategy=IndexSelectionStrategy(start=0, end=20),
        )
        assert converter is not None

    def test_identifier_includes_nested_word_strategy_params(self):
        left = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=WordIndexSelectionStrategy(indices=[0]),
        )
        right = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=WordIndexSelectionStrategy(indices=[3]),
        )
        assert left.get_identifier() != right.get_identifier()
        assert left.get_identifier().params["selection_strategy"] == "WordIndexSelectionStrategy"
        assert left.get_identifier().params["selection_strategy_params"]["indices"] == [0]
        assert right.get_identifier().params["selection_strategy_params"]["indices"] == [3]
        assert "indices" not in left.get_identifier().params

    def test_identifier_uses_empty_strategy_params_for_char_level_strategies(self):
        converter = SelectiveTextConverter(
            sub_converter=Base64Converter(),
            selection_strategy=IndexSelectionStrategy(start=0, end=5),
        )
        params = converter.get_identifier().params
        assert params["selection_strategy"] == "IndexSelectionStrategy"
        assert params["selection_strategy_params"] == {}
