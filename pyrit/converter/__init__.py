# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Converters for transforming prompts before sending them to targets in red teaming workflows.

Converters are organized into categories: Text-to-Text (encoding, obfuscation, translation, variation),
Audio (text-to-audio, audio-to-text, audio-to-audio), Image (text-to-image, image-to-image),
Video (image-to-video), File (text-to-PDF/URL), Selective Converting (partial prompt transformation),
and Human-in-the-Loop (interactive review). Converters can be stacked together to create complex
transformation pipelines for testing AI system robustness.
"""

import importlib
from typing import TYPE_CHECKING

from pyrit.converter.add_image_text_converter import AddImageTextConverter
from pyrit.converter.add_image_to_video_converter import AddImageVideoConverter
from pyrit.converter.add_text_image_converter import AddTextImageConverter
from pyrit.converter.ansi_escape.ansi_attack_converter import AnsiAttackConverter
from pyrit.converter.arabic_presentation_form_converter import ArabicPresentationFormConverter
from pyrit.converter.arabizi_converter import ArabiziConverter
from pyrit.converter.ascii_art_converter import AsciiArtConverter
from pyrit.converter.ask_to_decode_converter import AskToDecodeConverter
from pyrit.converter.atbash_converter import AtbashConverter
from pyrit.converter.azure_speech_audio_to_text_converter import AzureSpeechAudioToTextConverter
from pyrit.converter.azure_speech_text_to_audio_converter import AzureSpeechTextToAudioConverter
from pyrit.converter.base64_converter import Base64Converter
from pyrit.converter.base2048_converter import Base2048Converter
from pyrit.converter.bidi_converter import BidiConverter
from pyrit.converter.bin_ascii_converter import BinAsciiConverter
from pyrit.converter.binary_converter import BinaryConverter
from pyrit.converter.braille_converter import BrailleConverter
from pyrit.converter.caesar_converter import CaesarConverter
from pyrit.converter.character_space_converter import CharacterSpaceConverter
from pyrit.converter.charswap_attack_converter import CharSwapConverter
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
from pyrit.converter.qr_code_converter import QRCodeConverter
from pyrit.converter.random_capital_letters_converter import RandomCapitalLettersConverter
from pyrit.converter.random_translation_converter import RandomTranslationConverter
from pyrit.converter.repeat_token_converter import RepeatTokenConverter
from pyrit.converter.rot13_converter import ROT13Converter
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
from pyrit.converter.text_selection_strategy import (
    AllWordsSelectionStrategy,
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
from pyrit.converter.word_doc_converter import WordDocConverter
from pyrit.converter.zalgo_converter import ZalgoConverter
from pyrit.converter.zero_width_converter import ZeroWidthConverter

if TYPE_CHECKING:
    from pyrit.converter.audio_echo_converter import AudioEchoConverter
    from pyrit.converter.audio_frequency_converter import AudioFrequencyConverter
    from pyrit.converter.audio_speed_converter import AudioSpeedConverter
    from pyrit.converter.audio_volume_converter import AudioVolumeConverter
    from pyrit.converter.audio_white_noise_converter import AudioWhiteNoiseConverter
    from pyrit.converter.text_jailbreak_converter import TextJailbreakConverter

# Lazy imports for modules with heavy third-party dependencies (PEP 562).
# Audio converters import `scipy` which adds ~1.3s to startup.
# TextJailbreakConverter imports `pyrit.datasets` which triggers `datasets` → `pandas` (~1.6s).
_LAZY_IMPORTS: dict[str, str] = {
    "AudioEchoConverter": "pyrit.converter.audio_echo_converter",
    "AudioFrequencyConverter": "pyrit.converter.audio_frequency_converter",
    "AudioSpeedConverter": "pyrit.converter.audio_speed_converter",
    "AudioVolumeConverter": "pyrit.converter.audio_volume_converter",
    "AudioWhiteNoiseConverter": "pyrit.converter.audio_white_noise_converter",
    "TextJailbreakConverter": "pyrit.converter.text_jailbreak_converter",
}


def __getattr__(name: str) -> object:
    if name in _LAZY_IMPORTS:
        module = importlib.import_module(_LAZY_IMPORTS[name])
        attr = getattr(module, name)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AddImageTextConverter",
    "AddImageVideoConverter",
    "AddTextImageConverter",
    "AllWordsSelectionStrategy",
    "AnsiAttackConverter",
    "ArabicPresentationFormConverter",
    "ArabiziConverter",
    "AsciiArtConverter",
    "AsciiSmugglerConverter",
    "AskToDecodeConverter",
    "AtbashConverter",
    "AudioEchoConverter",
    "AudioFrequencyConverter",
    "AudioSpeedConverter",
    "AudioVolumeConverter",
    "AudioWhiteNoiseConverter",
    "AzureSpeechAudioToTextConverter",
    "AzureSpeechTextToAudioConverter",
    "Base2048Converter",
    "Base64Converter",
    "BidiConverter",
    "BinAsciiConverter",
    "BinaryConverter",
    "BrailleConverter",
    "CaesarConverter",
    "CharSwapConverter",
    "CharacterSpaceConverter",
    "CodeChameleonConverter",
    "ColloquialWordswapConverter",
    "ConverterResult",
    "DecompositionConverter",
    "DenylistConverter",
    "DiacriticConverter",
    "EcojiConverter",
    "EmojiConverter",
    "FirstLetterConverter",
    "FlipConverter",
    "ImageColorSaturationConverter",
    "ImageCompressionConverter",
    "ImageOverlayConverter",
    "ImagePromptStyleConverter",
    "ImageResizingConverter",
    "ImageRotationConverter",
    "IndexSelectionStrategy",
    "InsertPunctuationConverter",
    "IPAConverter",
    "JsonStringConverter",
    "KeywordSelectionStrategy",
    "LeetspeakConverter",
    "LLMGenericTextConverter",
    "MaliciousQuestionGeneratorConverter",
    "MathObfuscationConverter",
    "MathPromptConverter",
    "MorseConverter",
    "NatoConverter",
    "NegationTrapConverter",
    "NoiseConverter",
    "PDFConverter",
    "PersuasionConverter",
    "PolicyPuppetryConverter",
    "PolicyPuppetryTemplate",
    "PositionSelectionStrategy",
    "Converter",
    "ProportionSelectionStrategy",
    "QRCodeConverter",
    "ROT13Converter",
    "RandomCapitalLettersConverter",
    "RandomTranslationConverter",
    "RangeSelectionStrategy",
    "RegexSelectionStrategy",
    "RepeatTokenConverter",
    "ScientificTranslationConverter",
    "SearchReplaceConverter",
    "SelectiveTextConverter",
    "SneakyBitsSmugglerConverter",
    "StringJoinConverter",
    "SuffixAppendConverter",
    "SuperscriptConverter",
    "TaskFramingConverter",
    "TatweelConverter",
    "TemplateSegmentConverter",
    "TenseConverter",
    "TextJailbreakConverter",
    "TextSelectionStrategy",
    "TokenSelectionStrategy",
    "ToneConverter",
    "ToxicSentenceGeneratorConverter",
    "TranslationConverter",
    "TransparencyAttackConverter",
    "UnicodeConfusableConverter",
    "UnicodeReplacementConverter",
    "UnicodeSubstitutionConverter",
    "UrlConverter",
    "VariationConverter",
    "VariationSelectorSmugglerConverter",
    "WordDocConverter",
    "WordIndexSelectionStrategy",
    "WordKeywordSelectionStrategy",
    "WordPositionSelectionStrategy",
    "WordProportionSelectionStrategy",
    "WordRegexSelectionStrategy",
    "WordSelectionStrategy",
    "ZalgoConverter",
    "ZeroWidthConverter",
    "get_converter_modalities",
]
