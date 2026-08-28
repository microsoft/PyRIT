# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# ruff: noqa: F401

"""
Converters for transforming prompts before sending them to targets in red teaming workflows.

Converters are organized into categories: Text-to-Text (encoding, obfuscation, translation, variation),
Audio (text-to-audio, audio-to-text, audio-to-audio), Image (text-to-image, image-to-image),
Video (image-to-video), File (text-to-PDF/URL), Selective Converting (partial prompt transformation),
and Human-in-the-Loop (interactive review). Converters can be stacked together to create complex
transformation pipelines for testing AI system robustness.
"""

from typing import TYPE_CHECKING

from pyrit.common.lazy_imports import get_lazy_dir, resolve_lazy_export

if TYPE_CHECKING:
    from pyrit.converter.acrostic_converter import AcrosticConverter
    from pyrit.converter.add_image_text_converter import AddImageTextConverter
    from pyrit.converter.add_image_to_video_converter import AddImageVideoConverter
    from pyrit.converter.add_text_image_converter import AddTextImageConverter
    from pyrit.converter.ansi_escape.ansi_attack_converter import AnsiAttackConverter
    from pyrit.converter.arabic_presentation_form_converter import ArabicPresentationFormConverter
    from pyrit.converter.arabizi_converter import ArabiziConverter
    from pyrit.converter.ascii_art_converter import AsciiArtConverter
    from pyrit.converter.ask_to_decode_converter import AskToDecodeConverter
    from pyrit.converter.atbash_converter import AtbashConverter
    from pyrit.converter.audio_echo_converter import AudioEchoConverter
    from pyrit.converter.audio_frequency_converter import AudioFrequencyConverter
    from pyrit.converter.audio_speed_converter import AudioSpeedConverter
    from pyrit.converter.audio_volume_converter import AudioVolumeConverter
    from pyrit.converter.audio_white_noise_converter import AudioWhiteNoiseConverter
    from pyrit.converter.azure_speech_audio_to_text_converter import AzureSpeechAudioToTextConverter
    from pyrit.converter.azure_speech_text_to_audio_converter import AzureSpeechTextToAudioConverter
    from pyrit.converter.base64_converter import Base64Converter
    from pyrit.converter.base2048_converter import Base2048Converter
    from pyrit.converter.bidi_converter import BidiConverter
    from pyrit.converter.bijection_converter import (
        DigitBijectionConverter,
        LetterBijectionConverter,
        TokenBijectionConverter,
    )
    from pyrit.converter.bin_ascii_converter import BinAsciiConverter
    from pyrit.converter.binary_converter import BinaryConverter
    from pyrit.converter.braille_converter import BrailleConverter
    from pyrit.converter.caesar_converter import CaesarConverter
    from pyrit.converter.char_noise_converter import CharNoiseConverter
    from pyrit.converter.character_space_converter import CharacterSpaceConverter
    from pyrit.converter.charswap_attack_converter import CharSwapConverter
    from pyrit.converter.code_attack_converter import CodeAttackConverter
    from pyrit.converter.codechameleon_converter import CodeChameleonConverter
    from pyrit.converter.colloquial_wordswap_converter import ColloquialWordswapConverter
    from pyrit.converter.converter import Converter, ConverterResult, get_converter_modalities
    from pyrit.converter.decomposition_converter import DecompositionConverter
    from pyrit.converter.denylist_converter import DenylistConverter
    from pyrit.converter.diacritic_converter import DiacriticConverter
    from pyrit.converter.ecoji_converter import EcojiConverter
    from pyrit.converter.emoji_converter import EmojiConverter
    from pyrit.converter.first_letter_converter import FirstLetterConverter
    from pyrit.converter.flip_converter import FlipConverter
    from pyrit.converter.image_color_saturation_converter import ImageColorSaturationConverter
    from pyrit.converter.image_compression_converter import ImageCompressionConverter
    from pyrit.converter.image_overlay_converter import ImageOverlayConverter
    from pyrit.converter.image_prompt_style_converter import ImagePromptStyleConverter
    from pyrit.converter.image_resizing_converter import ImageResizingConverter
    from pyrit.converter.image_rotation_converter import ImageRotationConverter
    from pyrit.converter.insert_punctuation_converter import InsertPunctuationConverter
    from pyrit.converter.ipa_converter import IPAConverter
    from pyrit.converter.json_string_converter import JsonStringConverter
    from pyrit.converter.leetspeak_converter import LeetspeakConverter
    from pyrit.converter.llm_generic_text_converter import LLMGenericTextConverter
    from pyrit.converter.malicious_question_generator_converter import MaliciousQuestionGeneratorConverter
    from pyrit.converter.math_obfuscation_converter import MathObfuscationConverter
    from pyrit.converter.math_prompt_converter import MathPromptConverter
    from pyrit.converter.morse_converter import MorseConverter
    from pyrit.converter.nato_converter import NatoConverter
    from pyrit.converter.negation_trap_converter import NegationTrapConverter
    from pyrit.converter.noise_converter import NoiseConverter
    from pyrit.converter.pdf_converter import PDFConverter
    from pyrit.converter.persuasion_converter import PersuasionConverter
    from pyrit.converter.policy_puppetry_converter import PolicyPuppetryConverter, PolicyPuppetryTemplate
    from pyrit.converter.puzzled import PuzzledConverter, PuzzleType
    from pyrit.converter.qr_code_converter import QRCodeConverter
    from pyrit.converter.random_capital_letters_converter import RandomCapitalLettersConverter
    from pyrit.converter.random_translation_converter import RandomTranslationConverter
    from pyrit.converter.repeat_token_converter import RepeatTokenConverter
    from pyrit.converter.rot13_converter import ROT13Converter
    from pyrit.converter.sata_masking_converter import SATA_TASK_TEMPLATE, SATAMaskingConverter
    from pyrit.converter.scientific_translation_converter import ScientificTranslationConverter
    from pyrit.converter.search_replace_converter import SearchReplaceConverter
    from pyrit.converter.selective_text_converter import SelectiveTextConverter
    from pyrit.converter.string_join_converter import StringJoinConverter
    from pyrit.converter.suffix_append_converter import SuffixAppendConverter
    from pyrit.converter.superscript_converter import SuperscriptConverter
    from pyrit.converter.task_framing_converter import TaskFramingConverter
    from pyrit.converter.tatweel_converter import TatweelConverter
    from pyrit.converter.template_segment_converter import TemplateSegmentConverter
    from pyrit.converter.tense_converter import TenseConverter
    from pyrit.converter.text_jailbreak_converter import TextJailbreakConverter
    from pyrit.converter.text_selection_strategy import (
        AllWordsSelectionStrategy,
        ContentWordSelectionStrategy,
        IndexSelectionStrategy,
        KeywordSelectionStrategy,
        PositionSelectionStrategy,
        ProportionSelectionStrategy,
        RangeSelectionStrategy,
        RegexSelectionStrategy,
        TextSelectionStrategy,
        TokenSelectionStrategy,
        WordIndexSelectionStrategy,
        WordKeywordSelectionStrategy,
        WordPositionSelectionStrategy,
        WordProportionSelectionStrategy,
        WordRegexSelectionStrategy,
        WordSelectionStrategy,
    )
    from pyrit.converter.token_smuggling import (
        AsciiSmugglerConverter,
        SneakyBitsSmugglerConverter,
        VariationSelectorSmugglerConverter,
    )
    from pyrit.converter.tone_converter import ToneConverter
    from pyrit.converter.toxic_sentence_generator_converter import ToxicSentenceGeneratorConverter
    from pyrit.converter.translation_converter import TranslationConverter
    from pyrit.converter.transparency_attack_converter import TransparencyAttackConverter
    from pyrit.converter.unicode_confusable_converter import UnicodeConfusableConverter
    from pyrit.converter.unicode_replacement_converter import UnicodeReplacementConverter
    from pyrit.converter.unicode_sub_converter import UnicodeSubstitutionConverter
    from pyrit.converter.url_converter import UrlConverter
    from pyrit.converter.variation_converter import VariationConverter
    from pyrit.converter.vigenere_converter import VigenereConverter
    from pyrit.converter.word_doc_converter import WordDocConverter
    from pyrit.converter.zalgo_converter import ZalgoConverter
    from pyrit.converter.zero_width_converter import ZeroWidthConverter

_LAZY_EXPORTS: dict[str, str | tuple[str, str | None]] = {
    "AcrosticConverter": "pyrit.converter.acrostic_converter",
    "AddImageTextConverter": "pyrit.converter.add_image_text_converter",
    "AddImageVideoConverter": "pyrit.converter.add_image_to_video_converter",
    "AddTextImageConverter": "pyrit.converter.add_text_image_converter",
    "AllWordsSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "AnsiAttackConverter": "pyrit.converter.ansi_escape.ansi_attack_converter",
    "ArabicPresentationFormConverter": "pyrit.converter.arabic_presentation_form_converter",
    "ArabiziConverter": "pyrit.converter.arabizi_converter",
    "AsciiArtConverter": "pyrit.converter.ascii_art_converter",
    "AsciiSmugglerConverter": "pyrit.converter.token_smuggling",
    "AskToDecodeConverter": "pyrit.converter.ask_to_decode_converter",
    "AtbashConverter": "pyrit.converter.atbash_converter",
    "AudioEchoConverter": "pyrit.converter.audio_echo_converter",
    "AudioFrequencyConverter": "pyrit.converter.audio_frequency_converter",
    "AudioSpeedConverter": "pyrit.converter.audio_speed_converter",
    "AudioVolumeConverter": "pyrit.converter.audio_volume_converter",
    "AudioWhiteNoiseConverter": "pyrit.converter.audio_white_noise_converter",
    "AzureSpeechAudioToTextConverter": "pyrit.converter.azure_speech_audio_to_text_converter",
    "AzureSpeechTextToAudioConverter": "pyrit.converter.azure_speech_text_to_audio_converter",
    "Base2048Converter": "pyrit.converter.base2048_converter",
    "Base64Converter": "pyrit.converter.base64_converter",
    "BidiConverter": "pyrit.converter.bidi_converter",
    "DigitBijectionConverter": "pyrit.converter.bijection_converter",
    "LetterBijectionConverter": "pyrit.converter.bijection_converter",
    "TokenBijectionConverter": "pyrit.converter.bijection_converter",
    "BinAsciiConverter": "pyrit.converter.bin_ascii_converter",
    "BinaryConverter": "pyrit.converter.binary_converter",
    "BrailleConverter": "pyrit.converter.braille_converter",
    "CaesarConverter": "pyrit.converter.caesar_converter",
    "CharNoiseConverter": "pyrit.converter.char_noise_converter",
    "CharSwapConverter": "pyrit.converter.charswap_attack_converter",
    "CharacterSpaceConverter": "pyrit.converter.character_space_converter",
    "CodeAttackConverter": "pyrit.converter.code_attack_converter",
    "CodeChameleonConverter": "pyrit.converter.codechameleon_converter",
    "ColloquialWordswapConverter": "pyrit.converter.colloquial_wordswap_converter",
    "ContentWordSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "ConverterResult": "pyrit.converter.converter",
    "DecompositionConverter": "pyrit.converter.decomposition_converter",
    "DenylistConverter": "pyrit.converter.denylist_converter",
    "DiacriticConverter": "pyrit.converter.diacritic_converter",
    "EcojiConverter": "pyrit.converter.ecoji_converter",
    "EmojiConverter": "pyrit.converter.emoji_converter",
    "FirstLetterConverter": "pyrit.converter.first_letter_converter",
    "FlipConverter": "pyrit.converter.flip_converter",
    "ImageColorSaturationConverter": "pyrit.converter.image_color_saturation_converter",
    "ImageCompressionConverter": "pyrit.converter.image_compression_converter",
    "ImageOverlayConverter": "pyrit.converter.image_overlay_converter",
    "ImagePromptStyleConverter": "pyrit.converter.image_prompt_style_converter",
    "ImageResizingConverter": "pyrit.converter.image_resizing_converter",
    "ImageRotationConverter": "pyrit.converter.image_rotation_converter",
    "IndexSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "InsertPunctuationConverter": "pyrit.converter.insert_punctuation_converter",
    "IPAConverter": "pyrit.converter.ipa_converter",
    "JsonStringConverter": "pyrit.converter.json_string_converter",
    "KeywordSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "LeetspeakConverter": "pyrit.converter.leetspeak_converter",
    "LLMGenericTextConverter": "pyrit.converter.llm_generic_text_converter",
    "MaliciousQuestionGeneratorConverter": "pyrit.converter.malicious_question_generator_converter",
    "MathObfuscationConverter": "pyrit.converter.math_obfuscation_converter",
    "MathPromptConverter": "pyrit.converter.math_prompt_converter",
    "MorseConverter": "pyrit.converter.morse_converter",
    "NatoConverter": "pyrit.converter.nato_converter",
    "NegationTrapConverter": "pyrit.converter.negation_trap_converter",
    "NoiseConverter": "pyrit.converter.noise_converter",
    "PDFConverter": "pyrit.converter.pdf_converter",
    "PersuasionConverter": "pyrit.converter.persuasion_converter",
    "PolicyPuppetryConverter": "pyrit.converter.policy_puppetry_converter",
    "PolicyPuppetryTemplate": "pyrit.converter.policy_puppetry_converter",
    "PositionSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "Converter": "pyrit.converter.converter",
    "ProportionSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "PuzzleType": "pyrit.converter.puzzled",
    "PuzzledConverter": "pyrit.converter.puzzled",
    "QRCodeConverter": "pyrit.converter.qr_code_converter",
    "ROT13Converter": "pyrit.converter.rot13_converter",
    "RandomCapitalLettersConverter": "pyrit.converter.random_capital_letters_converter",
    "RandomTranslationConverter": "pyrit.converter.random_translation_converter",
    "RangeSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "RegexSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "RepeatTokenConverter": "pyrit.converter.repeat_token_converter",
    "SATA_TASK_TEMPLATE": "pyrit.converter.sata_masking_converter",
    "SATAMaskingConverter": "pyrit.converter.sata_masking_converter",
    "ScientificTranslationConverter": "pyrit.converter.scientific_translation_converter",
    "SearchReplaceConverter": "pyrit.converter.search_replace_converter",
    "SelectiveTextConverter": "pyrit.converter.selective_text_converter",
    "SneakyBitsSmugglerConverter": "pyrit.converter.token_smuggling",
    "StringJoinConverter": "pyrit.converter.string_join_converter",
    "SuffixAppendConverter": "pyrit.converter.suffix_append_converter",
    "SuperscriptConverter": "pyrit.converter.superscript_converter",
    "TaskFramingConverter": "pyrit.converter.task_framing_converter",
    "TatweelConverter": "pyrit.converter.tatweel_converter",
    "TemplateSegmentConverter": "pyrit.converter.template_segment_converter",
    "TenseConverter": "pyrit.converter.tense_converter",
    "TextJailbreakConverter": "pyrit.converter.text_jailbreak_converter",
    "TextSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "TokenSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "ToneConverter": "pyrit.converter.tone_converter",
    "ToxicSentenceGeneratorConverter": "pyrit.converter.toxic_sentence_generator_converter",
    "TranslationConverter": "pyrit.converter.translation_converter",
    "TransparencyAttackConverter": "pyrit.converter.transparency_attack_converter",
    "UnicodeConfusableConverter": "pyrit.converter.unicode_confusable_converter",
    "UnicodeReplacementConverter": "pyrit.converter.unicode_replacement_converter",
    "UnicodeSubstitutionConverter": "pyrit.converter.unicode_sub_converter",
    "UrlConverter": "pyrit.converter.url_converter",
    "VariationConverter": "pyrit.converter.variation_converter",
    "VariationSelectorSmugglerConverter": "pyrit.converter.token_smuggling",
    "VigenereConverter": "pyrit.converter.vigenere_converter",
    "WordDocConverter": "pyrit.converter.word_doc_converter",
    "WordIndexSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "WordKeywordSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "WordPositionSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "WordProportionSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "WordRegexSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "WordSelectionStrategy": "pyrit.converter.text_selection_strategy",
    "ZalgoConverter": "pyrit.converter.zalgo_converter",
    "ZeroWidthConverter": "pyrit.converter.zero_width_converter",
    "get_converter_modalities": "pyrit.converter.converter",
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    return resolve_lazy_export(
        name=name,
        module_name=__name__,
        module_globals=globals(),
        exports=_LAZY_EXPORTS,
    )


def __dir__() -> list[str]:
    return get_lazy_dir(module_globals=globals(), exports=_LAZY_EXPORTS)
