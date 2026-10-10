# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.


from pyrit.converter import ConverterResult, SuperscriptConverter


async def _check_conversion(converter, prompts, expected_outputs):
    for prompt, expected_output in zip(prompts, expected_outputs, strict=False):
        result = await converter.convert_async(prompt=prompt, input_type="text")
        assert isinstance(result, ConverterResult)
        assert result.output_text == expected_output


async def test_superscript_converter():
    defalut_converter = SuperscriptConverter()
    await _check_conversion(
        defalut_converter,
        ["Let's test this converter!", "Unsupported characters stay the same: qCFQSXYZ"],
        [
            (
                "\u1d38\u1d49\u1d57'\u02e2 \u1d57\u1d49\u02e2\u1d57 \u1d57\u02b0\u2071\u02e2 "
                "\u1d9c\u1d52\u207f\u1d5b\u1d49\u02b3\u1d57\u1d49\u02b3!"
            ),
            (
                "\u1d41\u207f\u02e2\u1d58\u1d56\u1d56\u1d52\u02b3\u1d57\u1d49\u1d48 "
                "\u1d9c\u02b0\u1d43\u02b3\u1d43\u1d9c\u1d57\u1d49\u02b3\u02e2 "
                "\u02e2\u1d57\u1d43\u02b8 \u1d57\u02b0\u1d49 \u02e2\u1d43\u1d50\u1d49: qCFQSXYZ"
            ),
        ],
    )


async def test_superscript_uppercase_b_is_superscript_b():
    """Uppercase 'B' must map to superscript B, not the superscript AE ligature.

    Regression: 'B' was mapped to U+1D2D (MODIFIER LETTER CAPITAL AE, the super-
    script ligature 'ᴭ') instead of U+1D2E (MODIFIER LETTER CAPITAL B, 'ᴮ') -- an
    off-by-one in the codepoint. The existing test only exercised lowercase text,
    so it went unnoticed.
    """
    converter = SuperscriptConverter()
    result = await converter.convert_async(prompt="B", input_type="text")
    assert result.output_text == "ᴮ"


async def test_superscript_mapped_letters_match_unicode_names():
    """Every mapped letter must resolve to the matching super/modifier letter.

    Guards the whole map against codepoint transcription errors like the 'B' case
    above, for both cases.
    """
    import unicodedata

    converter = SuperscriptConverter()
    for letter in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ":
        result = await converter.convert_async(prompt=letter, input_type="text")
        out = result.output_text
        if out == letter:
            continue  # unsupported letter (no Unicode superscript form) is left as-is
        name = unicodedata.name(out)
        assert name.endswith(f" {letter.upper()}"), (
            f"{letter!r} -> {out!r} (U+{ord(out):04X}, {name}); "
            f"expected a superscript/modifier letter for {letter.upper()!r}"
        )
