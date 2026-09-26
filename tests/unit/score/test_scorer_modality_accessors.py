# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Contract tests for the public scorer modality accessors.

Scorers have long declared the data types they can score via
``ScorerPromptValidator(supported_data_types=...)``, but that declaration was only reachable
through the private ``scorer._validator._supported_data_types`` and only *enforced* at score
time. These tests pin a public, plan-time-readable surface:

* ``ScorerPromptValidator.supported_data_types`` / ``has_declared_data_types``
* ``Scorer.supported_data_types`` — ``None`` when undeclared or unknown
* ``Scorer.skips_unsupported_data_types`` — whether unsupported evidence can be ignored
* ``MessageScorer`` reads its validator; wrapper scorers delegate to their children
* the two former private reach-ins (audio transcript, video frames) use the public surface

The runtime permissive default is deliberately unchanged: plan-time strictness distinguishes
"declared nothing" from "declared everything", runtime does not.
"""

from __future__ import annotations

from typing import get_args
from unittest.mock import MagicMock, PropertyMock

import pytest

from pyrit.models import MessagePiece, PromptDataType
from pyrit.score import (
    FloatScaleScorer,
    FloatScaleThresholdScorer,
    SubStringScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
)
from pyrit.score.audio_transcript_scorer import AudioTranscriptHelper
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.manual_scorer import ManualScorer
from pyrit.score.video_scorer import VideoHelper

_ALL_DATA_TYPES: tuple[PromptDataType, ...] = get_args(PromptDataType)


def _substring_scorer(*, declared: list[PromptDataType] | None) -> SubStringScorer:
    """Build a real MessageScorer whose validator declares ``declared`` (or nothing when None)."""
    validator = (
        ScorerPromptValidator(supported_data_types=declared) if declared is not None else ScorerPromptValidator()
    )
    return SubStringScorer(substring="x", validator=validator)


def _float_scale_scorer_declaring(declared: frozenset[PromptDataType] | None) -> MagicMock:
    """A FloatScaleScorer stand-in whose ``supported_data_types`` reports ``declared``."""
    scorer = MagicMock(spec=FloatScaleScorer)
    type(scorer).supported_data_types = PropertyMock(return_value=declared)
    return scorer


# ---------------------------------------------------------------------------
# ScorerPromptValidator
# ---------------------------------------------------------------------------
def test_validator_supported_data_types_declared_returns_declared():
    """A declared sequence is reported back verbatim and marked as declared."""
    validator = ScorerPromptValidator(supported_data_types=["text", "image_path"])
    assert list(validator.supported_data_types) == ["text", "image_path"]
    assert validator.has_declared_data_types is True


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, True),
        ({"enforce_all_pieces_valid": True}, False),
        ({"raise_on_no_valid_pieces": True}, False),
    ],
)
def test_validator_skip_contract_reflects_strictness(options, expected):
    validator = ScorerPromptValidator(supported_data_types=["text"], **options)
    assert validator.skips_unsupported_data_types is expected


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, True),
        ({"raise_on_no_valid_pieces": True}, True),
        ({"enforce_all_pieces_valid": True}, False),
        ({"enforce_all_pieces_valid": True, "raise_on_no_valid_pieces": True}, False),
    ],
)
def test_validator_allows_unsupported_pieces_independently_of_raise_on_empty(options, expected):
    validator = ScorerPromptValidator(supported_data_types=["text"], **options)
    assert validator.allows_unsupported_pieces is expected


def test_validator_supported_data_types_undeclared_returns_all_types():
    """
    Undeclared falls back to every PromptDataType and is marked as *not* declared.

    Pins D10: the runtime default stays permissive; only the ``has_declared_data_types`` flag
    lets plan-time code tell "declared everything" from "declared nothing".
    """
    validator = ScorerPromptValidator()
    assert tuple(validator.supported_data_types) == _ALL_DATA_TYPES
    assert validator.has_declared_data_types is False


def test_validator_empty_sequence_treated_as_undeclared():
    """An empty declaration is falsy today (``if supported_data_types:``) and stays undeclared."""
    validator = ScorerPromptValidator(supported_data_types=[])
    assert tuple(validator.supported_data_types) == _ALL_DATA_TYPES
    assert validator.has_declared_data_types is False


def test_validator_runtime_enforcement_unchanged_for_undeclared():
    """Plan-time strictness must not leak into runtime: an undeclared validator still accepts any piece."""
    validator = ScorerPromptValidator()
    piece = MessagePiece(role="assistant", original_value="frame.png", original_value_data_type="image_path")
    assert validator.is_message_piece_supported(message_piece=piece) is True


def test_validator_runtime_enforcement_unchanged_for_declared():
    """A declared validator still rejects pieces outside its declaration at runtime."""
    validator = ScorerPromptValidator(supported_data_types=["text"])
    piece = MessagePiece(role="assistant", original_value="frame.png", original_value_data_type="image_path")
    assert validator.is_message_piece_supported(message_piece=piece) is False


# ---------------------------------------------------------------------------
# Scorer base: unknown is None, never an AttributeError
# ---------------------------------------------------------------------------
def test_scorer_base_supported_data_types_is_none_for_non_message_scorer():
    """A TrueFalseScorer that is not a MessageScorer has no validator and reports None, not AttributeError."""
    scorer = ManualScorer(value=True, rationale="r", user_identifier="u")
    assert scorer.supported_data_types is None
    assert scorer.skips_unsupported_data_types is False
    assert scorer.allows_unsupported_pieces is False


# ---------------------------------------------------------------------------
# MessageScorer reads its validator
# ---------------------------------------------------------------------------
def test_message_scorer_declared_types_returns_frozenset():
    """SubStringScorer's default validator declares ["text"]; the accessor reports exactly that."""
    scorer = SubStringScorer(substring="x")
    assert scorer.supported_data_types == frozenset({"text"})
    assert scorer.skips_unsupported_data_types is True
    assert scorer.allows_unsupported_pieces is True


def test_message_scorer_can_filter_pieces_but_cannot_skip_empty_score():
    scorer = SubStringScorer(
        substring="x",
        validator=ScorerPromptValidator(supported_data_types=["text"], raise_on_no_valid_pieces=True),
    )
    assert scorer.allows_unsupported_pieces is True
    assert scorer.skips_unsupported_data_types is False


def test_message_scorer_multi_type_declaration_round_trips():
    """Multiple declared types all appear, as a frozenset."""
    scorer = _substring_scorer(declared=["text", "image_path"])
    assert scorer.supported_data_types == frozenset({"text", "image_path"})


def test_message_scorer_undeclared_types_returns_none():
    """A MessageScorer built on a bare validator reports None — not the all-types set."""
    assert _substring_scorer(declared=None).supported_data_types is None


@pytest.mark.parametrize("data_type", _ALL_DATA_TYPES)
def test_message_scorer_round_trips_every_data_type(data_type: PromptDataType):
    """Every legal PromptDataType survives the validator → accessor path."""
    scorer = _substring_scorer(declared=[data_type])
    assert scorer.supported_data_types == frozenset({data_type})


# ---------------------------------------------------------------------------
# Wrapper scorers delegate
# ---------------------------------------------------------------------------
def test_threshold_scorer_delegates_to_wrapped_scorer():
    """FloatScaleThresholdScorer reports whatever its wrapped FloatScaleScorer reports."""
    wrapped = _float_scale_scorer_declaring(frozenset({"text", "image_path"}))
    type(wrapped).allows_unsupported_pieces = PropertyMock(return_value=True)
    scorer = FloatScaleThresholdScorer(scorer=wrapped, threshold=0.5)
    assert scorer.supported_data_types == frozenset({"text", "image_path"})
    assert scorer.allows_unsupported_pieces is True


def test_threshold_scorer_delegates_none():
    """An undeclared wrapped scorer makes the threshold scorer undeclared too."""
    scorer = FloatScaleThresholdScorer(scorer=_float_scale_scorer_declaring(None), threshold=0.5)
    assert scorer.supported_data_types is None


def test_inverter_scorer_delegates_to_wrapped_scorer():
    """TrueFalseInverterScorer reports its wrapped scorer's declaration."""
    scorer = TrueFalseInverterScorer(scorer=_substring_scorer(declared=["audio_path"]))
    assert scorer.supported_data_types == frozenset({"audio_path"})
    assert scorer.skips_unsupported_data_types is True
    assert scorer.allows_unsupported_pieces is True


def test_inverter_scorer_delegates_none():
    """An undeclared wrapped scorer makes the inverter undeclared."""
    scorer = TrueFalseInverterScorer(scorer=_substring_scorer(declared=None))
    assert scorer.supported_data_types is None


def test_composite_scorer_modality_is_unknown_pending_condition_routing_review():
    """Do not assert that unioned child types guarantee scorer compatibility."""
    scorer = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[
            _substring_scorer(declared=["text", "image_path"]),
            _substring_scorer(declared=["text", "audio_path"]),
        ],
    )
    assert scorer.supported_data_types is None
    assert scorer.skips_unsupported_data_types is False


def test_composite_scorer_identical_children_are_still_unknown():
    """Even identical declarations do not establish the compound's routing contract."""
    scorer = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.OR,
        scorers=[_substring_scorer(declared=["text"]), _substring_scorer(declared=["text"])],
    )
    assert scorer.supported_data_types is None


def test_composite_scorer_returns_none_when_any_child_undeclared():
    """One unknown child makes the whole composite unknown — a declared sibling cannot vouch for it."""
    scorer = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[_substring_scorer(declared=["text"]), _substring_scorer(declared=None)],
    )
    assert scorer.supported_data_types is None


def test_nested_wrappers_keep_composite_modality_unknown():
    """One-to-one wrappers preserve an unknown compound declaration."""
    composite = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[_substring_scorer(declared=["text", "image_path"]), _substring_scorer(declared=["image_path"])],
    )
    assert TrueFalseInverterScorer(scorer=composite).supported_data_types is None


# ---------------------------------------------------------------------------
# Former private reach-ins use the public surface
# ---------------------------------------------------------------------------
def test_audio_transcript_helper_rejects_scorer_declaring_no_text():
    """A text-capable scorer that declares types excluding text is still rejected."""
    with pytest.raises(ValueError, match="must support 'text'"):
        AudioTranscriptHelper(text_capable_scorer=_substring_scorer(declared=["image_path"]))


def test_audio_transcript_helper_accepts_scorer_declaring_text():
    """A scorer declaring text is accepted."""
    helper = AudioTranscriptHelper(text_capable_scorer=_substring_scorer(declared=["text"]))
    assert helper.text_scorer.supported_data_types == frozenset({"text"})


def test_audio_transcript_helper_accepts_undeclared_scorer():
    """Undeclared is accepted — same outcome as before, when the permissive default included text."""
    AudioTranscriptHelper(text_capable_scorer=_substring_scorer(declared=None))


def test_audio_transcript_helper_accepts_wrapper_scorer_declaring_text():
    """
    A wrapper whose delegate declares text is accepted.

    Previously this raised AttributeError because wrappers have no ``_validator``; the public
    accessor delegates, so the wrapper is now judged by what it actually scores.
    """
    wrapper = TrueFalseInverterScorer(scorer=_substring_scorer(declared=["text"]))
    AudioTranscriptHelper(text_capable_scorer=wrapper)


def test_audio_transcript_helper_rejects_wrapper_scorer_declaring_no_text():
    """The same delegation rejects a wrapper whose delegate excludes text."""
    wrapper = TrueFalseInverterScorer(scorer=_substring_scorer(declared=["audio_path"]))
    with pytest.raises(ValueError, match="must support 'text'"):
        AudioTranscriptHelper(text_capable_scorer=wrapper)


def test_video_helper_rejects_audio_scorer_declaring_no_audio():
    """The video helper's audio-scorer check rejects a scorer that declares only text."""
    with pytest.raises(ValueError, match="must support 'audio_path'"):
        VideoHelper._validate_audio_scorer(_substring_scorer(declared=["text"]))


def test_video_helper_accepts_audio_scorer_declaring_audio():
    """A scorer declaring audio_path passes the check."""
    VideoHelper._validate_audio_scorer(_substring_scorer(declared=["audio_path", "text"]))


def test_video_helper_accepts_undeclared_audio_scorer():
    """Undeclared is accepted, matching the former permissive-default behavior."""
    VideoHelper._validate_audio_scorer(_substring_scorer(declared=None))
