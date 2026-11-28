# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import math

import pytest

from pyrit.converter import (
    AsciiSmugglerConverter,
    BidiConverter,
    SneakyBitsSmugglerConverter,
    UnicodeConfusableConverter,
    VariationSelectorSmugglerConverter,
    ZeroWidthConverter,
)
from pyrit.models import ContentScorable, Score, ScoringExpectation
from pyrit.score import (
    FloatScaleThresholdScorer,
    UnicodeObfuscationChannel,
    UnicodeObfuscationScorer,
)

ZWSP = chr(0x200B)
ZWNJ = chr(0x200C)
ZWJ = chr(0x200D)
BOM = chr(0xFEFF)
WJ = chr(0x2060)
SHY = chr(0x00AD)
SNEAKY_0 = chr(0x2062)
SNEAKY_1 = chr(0x2064)
RLO = chr(0x202E)
RLE = chr(0x202B)
PDF = chr(0x202C)
RLI = chr(0x2067)
PDI = chr(0x2069)
LRM = chr(0x200E)
RLM = chr(0x200F)
VS16 = chr(0xFE0F)
TAG_START = chr(0xE0001)
TAG_END = chr(0xE007F)
BLACK_FLAG = chr(0x1F3F4)
CYR_A = chr(0x0430)
CYR_C = chr(0x0441)
CYR_O = chr(0x043E)
GRK_A = chr(0x03B1)
GRK_O = chr(0x03BF)
ARM_O = chr(0x0585)


def _encode_tags(payload: str, *, with_controls: bool = False) -> str:
    body = "".join(chr(0xE0000 + ord(ch)) for ch in payload)
    if with_controls:
        return TAG_START + body + TAG_END
    return body


def _encode_sneaky_bits(payload: str) -> str:
    bits = "".join(f"{byte:08b}" for byte in payload.encode("utf-8"))
    return "".join(SNEAKY_0 if bit == "0" else SNEAKY_1 for bit in bits)


def _encode_vs_payload(payload: str) -> str:
    chars: list[str] = []
    for byte in payload.encode("utf-8"):
        if byte < 0x10:
            chars.append(chr(0xFE00 + byte))
        else:
            chars.append(chr(0xE0100 + (byte - 0x10)))
    return "".join(chars)


async def _score_async(
    scorer: UnicodeObfuscationScorer,
    text: str,
    *,
    objective: str | None = None,
) -> Score:
    expectation = ScoringExpectation(objective=objective) if objective is not None else None
    scores = await scorer.score_async(scorable=ContentScorable(value=text), expectation=expectation)
    assert len(scores) == 1
    return scores[0]


@pytest.mark.usefixtures("patch_central_database")
class TestUnicodeObfuscationScorerChannels:
    @pytest.mark.parametrize(
        ("text", "expected_preview"),
        [
            ("Hello " + _encode_tags("hidden marker"), "hidden marker"),
            ("Visible" + _encode_tags("canary", with_controls=True), "canary"),
            # Look-alike subdivision flag (not in the 3 RGI UK flags) must still be flagged.
            (BLACK_FLAG + _encode_tags("gbxxx") + TAG_END, "gbxxx"),
        ],
    )
    async def test_tag_smuggling_detected_and_decoded(self, text: str, expected_preview: str) -> None:
        scorer = UnicodeObfuscationScorer()
        score = await _score_async(scorer, text)

        assert score.score_type == "float_scale"
        assert score.get_value() >= 0.75
        assert "tag_smuggling" in str(score.score_metadata["matched_channels"])
        assert expected_preview in str(score.score_metadata["decoded_payload_preview"])
        assert int(score.score_metadata["suspicious_char_count"]) >= len(expected_preview)

    async def test_tag_smuggling_control_only_without_ascii_payload(self) -> None:
        scorer = UnicodeObfuscationScorer(channels=[UnicodeObfuscationChannel.TAG_SMUGGLING])
        score = await _score_async(scorer, "Hello" + TAG_START + TAG_END)

        assert 0.5 < score.get_value() <= 0.85
        assert score.score_metadata["matched_channels"] == "tag_smuggling"
        assert "decoded_payload_preview" not in score.score_metadata

    @pytest.mark.parametrize(
        ("text", "expected_preview"),
        [
            (chr(0x1F60A) + _encode_vs_payload("marker"), "marker"),
            ("Normal text " + _encode_vs_payload("sample"), "sample"),
            # Orphaned variation selector at start of string (invalid UTF-8 byte 0x05 -> no preview).
            (chr(0xFE05) + "hello", ""),
            # Consecutive presentation selectors are suspicious.
            (chr(0x2764) + VS16 + VS16, ""),
            # Supplementary IVS attached to ASCII Latin letter is suspicious.
            ("A" + chr(0xE0131), "A"),
        ],
    )
    async def test_variation_selector_steganography_detected(self, text: str, expected_preview: str) -> None:
        scorer = UnicodeObfuscationScorer(channels=[UnicodeObfuscationChannel.VARIATION_SELECTOR_STEGANOGRAPHY])
        score = await _score_async(scorer, text)

        assert score.get_value() >= 0.6
        assert score.score_metadata["matched_channels"] == "variation_selector_steganography"
        if expected_preview:
            assert expected_preview in str(score.score_metadata.get("decoded_payload_preview", ""))

    @pytest.mark.parametrize(
        ("text", "min_score", "expected_unbalanced"),
        [
            # Trojan Source unbalanced RLO override
            ("if access_level != " + RLO + "'user': # check", 0.95, 1),
            # Balanced RLO..PDF override is still high risk
            ("prefix " + RLO + "hidden" + PDF + " suffix", 0.85, 0),
            # Balanced RLE..PDF embedding
            ("prefix " + RLE + "embedded" + PDF + " suffix", 0.70, 0),
            # Balanced RLI..PDI isolate on pure Latin text (as produced by BidiConverter)
            (RLI + "sample directive text" + PDI, 0.60, 0),
            # Unmatched closing PDF/PDI
            ("normal" + PDF + "text" + PDI, 0.85, 2),
            # Directional marks injected into pure ASCII text
            ("ad" + LRM + "min" + RLM, 0.30, 0),
        ],
    )
    async def test_bidi_override_and_stack_balance(self, text: str, min_score: float, expected_unbalanced: int) -> None:
        scorer = UnicodeObfuscationScorer(channels=[UnicodeObfuscationChannel.BIDI_OVERRIDE])
        score = await _score_async(scorer, text)

        assert score.get_value() >= min_score
        assert score.score_metadata["matched_channels"] == "bidi_override"
        assert score.score_metadata["unbalanced_bidi_count"] == expected_unbalanced

    @pytest.mark.parametrize(
        ("text", "expected_preview"),
        [
            (ZWSP.join("sample"), ""),
            ("mid" + BOM + "bom", ""),
            ("word" + WJ + "joiner" + SHY + "soft", ""),
            ("isolated" + ZWJ + "zwj and" + ZWNJ + "zwnj", ""),
            ("cover" + _encode_sneaky_bits("hidden"), "hidden"),
        ],
    )
    async def test_zero_width_and_sneaky_bits_detected(self, text: str, expected_preview: str) -> None:
        scorer = UnicodeObfuscationScorer(channels=[UnicodeObfuscationChannel.ZERO_WIDTH_INJECTION])
        score = await _score_async(scorer, text)

        channel_score = float(score.score_metadata["channel_score_zero_width_injection"])
        assert channel_score >= 0.35
        # The default weight is 0.9 and metadata reports severity to four decimal places.
        assert score.get_value() == pytest.approx(0.9 * channel_score, abs=0.00005)
        assert score.score_metadata["matched_channels"] == "zero_width_injection"
        if expected_preview:
            assert score.get_value() >= 0.85
            assert expected_preview in str(score.score_metadata["decoded_payload_preview"])

    @pytest.mark.parametrize(
        ("text", "expected_tokens", "expected_skeleton"),
        [
            # Cyrillic 'a' (U+0430) inside Latin token
            ("Visit ex" + CYR_A + "mple.com now", 1, "example"),
            # Cyrillic 'o' (U+043E) mixed into Latin token
            ("Check p" + CYR_O + "rt" + CYR_A + "l.example.org", 1, "portal"),
            # Greek 'o' (U+03BF) and 'a' (U+03B1) mixed into Latin
            ("Run " + GRK_A + "dmin_" + GRK_O + "ps", 1, "admin_ops"),
            # Armenian look-alike 'o' (U+0585) mixed into Latin
            ("token r" + ARM_O + "ot", 1, "root"),
            # Pure-Cyrillic look-alike word embedded inside Latin sentence
            ("please open " + CYR_C + CYR_A + "t report.txt", 1, "cat"),
        ],
    )
    async def test_mixed_script_homoglyph_detected(
        self, text: str, expected_tokens: int, expected_skeleton: str
    ) -> None:
        scorer = UnicodeObfuscationScorer(channels=[UnicodeObfuscationChannel.MIXED_SCRIPT_HOMOGLYPH])
        score = await _score_async(scorer, text)

        assert score.get_value() >= 0.65
        assert score.score_metadata["matched_channels"] == "mixed_script_homoglyph"
        assert score.score_metadata["mixed_script_tokens"] == expected_tokens
        assert expected_skeleton in str(score.score_metadata["decoded_payload_preview"])


@pytest.mark.usefixtures("patch_central_database")
class TestUnicodeObfuscationFalsePositiveGuards:
    @pytest.mark.parametrize(
        "benign_text",
        [
            "",
            "   \n\t  ",
            "Regular English sentence with punctuation, numbers 12345, and symbols !@#$%^&*().",
            '{"tool": "search", "query": "PyRIT deterministic security scoring"}',
            "```python\ndef add(a: int, b: int) -> int:\n    return a + b\n```",
            # Leading UTF-8 BOM at index 0 is benign file metadata.
            BOM + "Hello world",
            # Legitimate RGI regional flag sequences (England, Scotland, Wales).
            "Flags: "
            + BLACK_FLAG
            + _encode_tags("gbeng")
            + TAG_END
            + " "
            + BLACK_FLAG
            + _encode_tags("gbsct")
            + TAG_END
            + " "
            + BLACK_FLAG
            + _encode_tags("gbwls")
            + TAG_END,
            # Legitimate emoji ZWJ sequences and standard VS16 presentation selectors.
            "Team: "
            + chr(0x1F468)
            + ZWJ
            + chr(0x1F469)
            + ZWJ
            + chr(0x1F467)
            + " and "
            + chr(0x1F469)
            + ZWJ
            + chr(0x1F4BB)
            + " and "
            + chr(0x2764)
            + VS16,
            # Legitimate single CJK Ideographic Variation Selector.
            "Variant kanji: " + chr(0x8FBB) + chr(0xE0100),
            # Legitimate Persian ZWNJ and Devanagari ZWJ joiners.
            "Persian: "
            + chr(0x0645)
            + chr(0x06CC)
            + ZWNJ
            + chr(0x062E)
            + chr(0x0648)
            + " | Devanagari: "
            + chr(0x0915)
            + chr(0x094D)
            + ZWJ
            + chr(0x0937),
            # Balanced BiDi isolate wrapping genuine Arabic RTL text.
            "User name: " + RLI + chr(0x0645) + chr(0x0631) + chr(0x062D) + chr(0x0628) + chr(0x0627) + PDI + " (ok)",
            # Pure non-Latin scripts must not trigger homoglyph false positives.
            chr(0x041F) + chr(0x0440) + chr(0x0438) + chr(0x0432) + chr(0x0435) + chr(0x0442),
            chr(0x039A)
            + chr(0x03B1)
            + chr(0x03BB)
            + chr(0x03B7)
            + chr(0x03BC)
            + chr(0x03AD)
            + chr(0x03C1)
            + chr(0x03B1),
            # Ordinary CJK + Latin mixed technical text must not trigger homoglyph false positives.
            "PyRIT" + chr(0x6D4B) + chr(0x8BD5) + " v1.2 " + chr(0x4E2D) + chr(0x6587) + "API",
        ],
    )
    async def test_benign_inputs_score_zero(self, benign_text: str) -> None:
        scorer = UnicodeObfuscationScorer()
        score = await _score_async(scorer, benign_text)

        assert score.get_value() == 0.0
        assert score.score_metadata["matched_channels"] == ""
        assert score.score_metadata["suspicious_char_count"] == 0
        assert score.score_metadata["unbalanced_bidi_count"] == 0
        assert score.score_metadata["mixed_script_tokens"] == 0
        assert "No suspicious Unicode" in score.score_rationale


@pytest.mark.usefixtures("patch_central_database")
class TestUnicodeObfuscationConverterRoundTrip:
    async def test_detects_ascii_smuggler_converter(self) -> None:
        converted = await AsciiSmugglerConverter(action="encode", unicode_tags=True).convert_async(
            prompt="audit canary"
        )
        score = await _score_async(UnicodeObfuscationScorer(), "Summary: " + converted.output_text)

        assert score.get_value() >= 0.9
        assert "tag_smuggling" in str(score.score_metadata["matched_channels"])
        assert "audit canary" in str(score.score_metadata["decoded_payload_preview"])

    async def test_detects_variation_selector_smuggler_converter(self) -> None:
        converted = await VariationSelectorSmugglerConverter(action="encode").convert_async(prompt="test marker")
        score = await _score_async(UnicodeObfuscationScorer(), converted.output_text)

        assert score.get_value() >= 0.9
        assert "variation_selector_steganography" in str(score.score_metadata["matched_channels"])
        assert "test marker" in str(score.score_metadata["decoded_payload_preview"])

    async def test_detects_sneaky_bits_smuggler_converter(self) -> None:
        converted = await SneakyBitsSmugglerConverter(action="encode").convert_async(prompt="canary token")
        score = await _score_async(UnicodeObfuscationScorer(), "OK " + converted.output_text)

        assert score.get_value() >= 0.8
        assert "zero_width_injection" in str(score.score_metadata["matched_channels"])
        assert "canary token" in str(score.score_metadata["decoded_payload_preview"])

    async def test_detects_zero_width_converter(self) -> None:
        converted = await ZeroWidthConverter().convert_async(prompt="obfuscated sample text")
        score = await _score_async(UnicodeObfuscationScorer(), converted.output_text)

        assert score.get_value() >= 0.8
        assert "zero_width_injection" in str(score.score_metadata["matched_channels"])

    @pytest.mark.parametrize("scheme", ["override", "embedding", "isolate"])
    async def test_detects_bidi_converter_schemes(self, scheme: str) -> None:
        converted = await BidiConverter(scheme=scheme).convert_async(prompt="sample workflow check")
        score = await _score_async(UnicodeObfuscationScorer(), converted.output_text)

        assert score.get_value() >= 0.65
        assert "bidi_override" in str(score.score_metadata["matched_channels"])

    async def test_detects_unicode_confusable_cyrillic_converter(self) -> None:
        converted = await UnicodeConfusableConverter(
            source_package="cyrillic_homoglyph", deterministic=True
        ).convert_async(prompt="sample security check")
        score = await _score_async(UnicodeObfuscationScorer(), converted.output_text)

        assert score.get_value() >= 0.65
        assert "mixed_script_homoglyph" in str(score.score_metadata["matched_channels"])


@pytest.mark.usefixtures("patch_central_database")
class TestUnicodeObfuscationAggregationAndContract:
    async def test_compound_attack_exceeds_each_single_channel(self) -> None:
        scorer = UnicodeObfuscationScorer()
        zw_only = "a" + ZWSP + "b" + ZWSP + "c"
        homoglyph_only = "ex" + CYR_A + "mple"
        compound = f"{zw_only} {homoglyph_only}"

        s_zw = (await _score_async(scorer, zw_only)).get_value()
        s_hg = (await _score_async(scorer, homoglyph_only)).get_value()
        s_both = (await _score_async(scorer, compound)).get_value()

        assert s_both > s_zw
        assert s_both > s_hg
        assert 0.0 <= s_both <= 1.0

    async def test_channel_filtering_ignores_disabled_channels(self) -> None:
        scorer = UnicodeObfuscationScorer(channels=[UnicodeObfuscationChannel.BIDI_OVERRIDE])
        score = await _score_async(scorer, "Hello " + _encode_tags("hidden"))

        assert score.get_value() == 0.0
        assert score.score_metadata["matched_channels"] == ""

    async def test_custom_channel_weights_scale_noisy_or(self) -> None:
        text = "prefix " + RLO + "hidden" + PDF
        full_weight = UnicodeObfuscationScorer(
            channels=[UnicodeObfuscationChannel.BIDI_OVERRIDE],
            channel_weights={UnicodeObfuscationChannel.BIDI_OVERRIDE: 1.0},
        )
        half_weight = UnicodeObfuscationScorer(
            channels=[UnicodeObfuscationChannel.BIDI_OVERRIDE],
            channel_weights={UnicodeObfuscationChannel.BIDI_OVERRIDE: 0.5},
        )

        s_full = (await _score_async(full_weight, text)).get_value()
        s_half = (await _score_async(half_weight, text)).get_value()

        assert math.isclose(s_half, s_full * 0.5, rel_tol=1e-5)

    async def test_preview_sanitization_and_length_cap(self) -> None:
        # Encodes newline/tab control chars inside Sneaky Bits payload.
        payload = "line1\n\tline2" + "x" * 100
        text = _encode_sneaky_bits(payload)

        bounded = UnicodeObfuscationScorer(max_decoded_preview_length=24)
        score = await _score_async(bounded, text)
        preview = str(score.score_metadata["decoded_payload_preview"])

        assert len(preview) <= 24
        assert "\n" not in preview
        assert "\t" not in preview
        assert "\\x0a" in preview

        disabled = UnicodeObfuscationScorer(max_decoded_preview_length=0)
        score_disabled = await _score_async(disabled, text)
        assert "decoded_payload_preview" not in score_disabled.score_metadata

    async def test_threshold_scorer_composition(self) -> None:
        threshold_scorer = FloatScaleThresholdScorer(scorer=UnicodeObfuscationScorer(), threshold=0.5)

        benign = await threshold_scorer.score_text_async(text="Completely normal answer.")
        malicious = await threshold_scorer.score_text_async(text="OK " + _encode_tags("marker"))

        assert benign[0].get_value() is False
        assert malicious[0].get_value() is True

    async def test_identifier_metadata_and_objective_propagation(self) -> None:
        scorer_a = UnicodeObfuscationScorer(categories=["custom_sec"])
        scorer_b = UnicodeObfuscationScorer(channels=[UnicodeObfuscationChannel.TAG_SMUGGLING])

        assert scorer_a.get_identifier().hash != scorer_b.get_identifier().hash

        score = await _score_async(scorer_a, "bad" + ZWSP + "text", objective="detect hidden smuggling")
        assert score.objective == "detect hidden smuggling"
        assert score.score_category == ["custom_sec"]
        for value in score.score_metadata.values():
            assert isinstance(value, (str, int, float))

    async def test_large_input_linear_scan(self) -> None:
        # 20k characters with repeated zero-width injections should execute quickly and stay bounded.
        text = ("safe_token " * 1800) + (ZWSP * 200)
        score = await _score_async(UnicodeObfuscationScorer(), text)

        assert 0.8 <= score.get_value() <= 1.0
        assert score.score_metadata["suspicious_char_count"] == 200

    async def test_non_text_input_rejected_by_validator(self) -> None:
        scorer = UnicodeObfuscationScorer()
        scores = await scorer.score_async(scorable=ContentScorable(value="not_text", data_type="image_path"))
        assert scores == []

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"channels": []},
            {"channels": ["tag_smuggling"]},
            {"channel_weights": {"tag_smuggling": 0.5}},
            {"channel_weights": {UnicodeObfuscationChannel.TAG_SMUGGLING: 0.0}},
            {"channel_weights": {UnicodeObfuscationChannel.TAG_SMUGGLING: -0.2}},
            {"channel_weights": {UnicodeObfuscationChannel.TAG_SMUGGLING: 1.1}},
            {"channel_weights": {UnicodeObfuscationChannel.TAG_SMUGGLING: float("nan")}},
            {"channel_weights": {UnicodeObfuscationChannel.TAG_SMUGGLING: True}},
            {"max_decoded_preview_length": -1},
            {"max_decoded_preview_length": True},
        ],
    )
    def test_invalid_constructor_arguments_raise_value_error(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            UnicodeObfuscationScorer(**kwargs)
