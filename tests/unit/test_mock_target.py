# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Contract tests for ``unit.mocks.get_mock_target`` and the ``unit.modality_profiles`` constants.

``get_mock_target`` is load-bearing for modality validation: scenario tests pass mock targets
into ``Scenario.initialize_async``, and any code reading ``target.configuration.capabilities``
must see real ``frozenset`` values when a test asks for them — and must tolerate MagicMock
children when it does not. These tests pin both halves of that contract, prove every legal
``PromptDataType`` round-trips, and prove invalid input is rejected rather than swallowed.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from pyrit.models import ComponentIdentifier
from pyrit.models.literals import MEDIA_PATH_DATA_TYPES, PromptDataType
from pyrit.prompt_target import TargetCapabilities, TargetConfiguration
from unit.mocks import get_mock_target, modality_combos
from unit.modality_profiles import (
    IMAGE_EDIT_INPUT_MODALITIES,
    REALTIME_AUDIO_INPUT_MODALITIES,
    REALTIME_AUDIO_OUTPUT_MODALITIES,
    TEXT_ONLY_MODALITIES,
    VIDEO_GENERATION_OUTPUT_MODALITIES,
    VISION_INPUT_MODALITIES,
)

_ALL_DATA_TYPES: tuple[PromptDataType, ...] = get_args(PromptDataType)
_TEXT_AND_IMAGE = frozenset({frozenset({"text"}), frozenset({"text", "image_path"})})


# ---------------------------------------------------------------------------
# Backward-compatibility pins (green before and after the change)
# ---------------------------------------------------------------------------
def test_get_mock_target_default_returns_real_identifier():
    """The identifier is a real ComponentIdentifier, not a MagicMock, so identity-based code works."""
    identifier = get_mock_target().get_identifier()
    assert isinstance(identifier, ComponentIdentifier)
    assert identifier.class_name == "MockTarget"


def test_get_mock_target_name_override():
    """The positional ``name`` still flows into the identifier."""
    assert get_mock_target("Custom").get_identifier().class_name == "Custom"


def test_get_mock_target_default_configuration_is_mock():
    """
    Without modality kwargs the helper is unchanged: ``configuration`` is a MagicMock child.

    This documents the hazard modality validation must tolerate — capabilities on the default
    mock are *not* real frozensets, so a validator must treat them as unknown, never as a failure.
    """
    target = get_mock_target()
    assert not isinstance(target.configuration, TargetConfiguration)
    assert not isinstance(target.configuration.capabilities.input_modalities, frozenset)


def test_get_mock_target_is_spec_bound():
    """``spec=PromptTarget`` is retained so typos in attribute names fail loudly."""
    with pytest.raises(AttributeError):
        _ = get_mock_target().not_a_prompt_target_attribute


def test_get_mock_target_modalities_are_keyword_only():
    """A second positional is rejected; modalities must be passed by name."""
    with pytest.raises(TypeError):
        get_mock_target("MockTarget", [{"text"}])  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Real capabilities when modalities are supplied
# ---------------------------------------------------------------------------
def test_get_mock_target_input_modalities_sets_real_capabilities():
    """Supplying input modalities swaps in a real TargetConfiguration with real frozensets."""
    target = get_mock_target(input_modalities=[{"text"}, {"text", "image_path"}])
    assert isinstance(target.configuration, TargetConfiguration)
    assert isinstance(target.configuration.capabilities, TargetCapabilities)
    assert isinstance(target.configuration.capabilities.input_modalities, frozenset)
    assert target.configuration.capabilities.input_modalities == _TEXT_AND_IMAGE


def test_get_mock_target_input_modalities_defaults_output_to_text_only():
    """An omitted output side falls back to the production default, never to something permissive."""
    target = get_mock_target(input_modalities=[{"text", "image_path"}])
    assert target.configuration.capabilities.output_modalities == TEXT_ONLY_MODALITIES


def test_get_mock_target_output_modalities_sets_real_capabilities():
    """Supplying output modalities alone also yields a real configuration."""
    target = get_mock_target(output_modalities=[{"image_path"}])
    assert isinstance(target.configuration, TargetConfiguration)
    assert target.configuration.capabilities.output_modalities == frozenset({frozenset({"image_path"})})


def test_get_mock_target_output_modalities_defaults_input_to_text_only():
    """An omitted input side falls back to the production default."""
    target = get_mock_target(output_modalities=[{"image_path"}])
    assert target.configuration.capabilities.input_modalities == TEXT_ONLY_MODALITIES


def test_get_mock_target_both_modalities_honored_independently():
    """Input and output are set independently when both are supplied."""
    target = get_mock_target(input_modalities=[{"text"}], output_modalities=[{"audio_path"}, {"text"}])
    capabilities = target.configuration.capabilities
    assert capabilities.input_modalities == TEXT_ONLY_MODALITIES
    assert capabilities.output_modalities == frozenset({frozenset({"audio_path"}), frozenset({"text"})})


def test_get_mock_target_with_modalities_keeps_identifier_and_spec():
    """Real capabilities do not cost the real identifier or the spec binding."""
    target = get_mock_target("Vision", input_modalities=[{"text", "image_path"}])
    assert target.get_identifier().class_name == "Vision"
    with pytest.raises(AttributeError):
        _ = target.not_a_prompt_target_attribute


# ---------------------------------------------------------------------------
# Every legal PromptDataType round-trips, on both sides
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("data_type", _ALL_DATA_TYPES)
def test_get_mock_target_round_trips_every_input_data_type(data_type: PromptDataType):
    """The helper forwards every legal PromptDataType as an input modality, not just text/image."""
    target = get_mock_target(input_modalities=[{data_type}])
    assert target.configuration.capabilities.input_modalities == frozenset({frozenset({data_type})})


@pytest.mark.parametrize("data_type", _ALL_DATA_TYPES)
def test_get_mock_target_round_trips_every_output_data_type(data_type: PromptDataType):
    """The helper forwards every legal PromptDataType as an output modality."""
    target = get_mock_target(output_modalities=[{data_type}])
    assert target.configuration.capabilities.output_modalities == frozenset({frozenset({data_type})})


@pytest.mark.parametrize("media_type", sorted(MEDIA_PATH_DATA_TYPES))
def test_get_mock_target_media_with_text_combo(media_type: PromptDataType):
    """
    ``{text, <media>}`` combos round-trip for every media type.

    This is the exact shape ``_ModalityFeedbackRouter`` consults when deciding whether media may
    accompany text, so it is the shape downstream validation tests will use most.
    """
    target = get_mock_target(input_modalities=[{"text"}, {"text", media_type}])
    expected = frozenset({frozenset({"text"}), frozenset({"text", media_type})})
    assert target.configuration.capabilities.input_modalities == expected


# ---------------------------------------------------------------------------
# Input shape coercion
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "combos",
    [
        [["text"], ["text", "image_path"]],
        [("text",), ("text", "image_path")],
        ({"text"}, {"text", "image_path"}),
        _TEXT_AND_IMAGE,
    ],
    ids=["list_of_lists", "list_of_tuples", "tuple_of_sets", "canonical_frozenset"],
)
def test_get_mock_target_coerces_iterables_to_frozensets(combos):
    """Any iterable-of-iterables shape is canonicalised to frozenset-of-frozensets."""
    target = get_mock_target(input_modalities=combos)
    assert target.configuration.capabilities.input_modalities == _TEXT_AND_IMAGE


def test_modality_combos_builds_canonical_frozenset():
    """The public coercion helper produces the same canonical form the mock uses internally."""
    assert modality_combos({"text"}, ["text", "image_path"]) == _TEXT_AND_IMAGE


def test_get_mock_target_accepts_empty_modalities():
    """No combos is legal input; it yields an empty frozenset (nothing covers any request)."""
    target = get_mock_target(input_modalities=[])
    assert target.configuration.capabilities.input_modalities == frozenset()


# ---------------------------------------------------------------------------
# Invalid input is rejected by TargetCapabilities and must NOT be swallowed by the helper
# ---------------------------------------------------------------------------
def test_get_mock_target_rejects_unknown_input_modality():
    """An unknown data type propagates pydantic's ValidationError instead of yielding a bogus target."""
    with pytest.raises(ValidationError):
        get_mock_target(input_modalities=[{"foobar"}])  # type: ignore[list-item]


def test_get_mock_target_rejects_unknown_output_modality():
    """Same rejection on the output side."""
    with pytest.raises(ValidationError):
        get_mock_target(output_modalities=[{"foobar"}])  # type: ignore[list-item]


def test_get_mock_target_rejects_bare_string_combo():
    """
    ``["text"]`` (forgot the inner set) iterates the string to characters and is rejected.

    This is the most likely authoring mistake; it must fail loudly rather than build a target
    that accepts ``{"t", "e", "x"}``.
    """
    with pytest.raises(ValidationError):
        get_mock_target(input_modalities=["text"])  # type: ignore[list-item]


def test_get_mock_target_rejects_non_string_member():
    """Non-string members are rejected."""
    with pytest.raises(ValidationError):
        get_mock_target(input_modalities=[{123}])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# Named profiles — shared vocabulary for downstream modality tests
# ---------------------------------------------------------------------------
def test_profile_text_only():
    assert frozenset({frozenset({"text"})}) == TEXT_ONLY_MODALITIES
    assert TargetCapabilities().input_modalities == TEXT_ONLY_MODALITIES  # matches production default


def test_profile_vision_input_declares_text_image_and_both():
    """Matches ``_TEXT_IMAGE_INPUT`` in ``target_capabilities.py``: every accepted shape is explicit."""
    assert frozenset({frozenset({"text"}), frozenset({"image_path"}), frozenset({"text", "image_path"})}) == (
        VISION_INPUT_MODALITIES
    )
    assert frozenset({"image_path"}) in VISION_INPUT_MODALITIES


def test_profile_image_edit_input_has_no_bare_text_combo():
    """The edit-only shape: media is required on every request (the O8 case)."""
    assert frozenset({frozenset({"text", "image_path"})}) == IMAGE_EDIT_INPUT_MODALITIES
    assert frozenset({"text"}) not in IMAGE_EDIT_INPUT_MODALITIES


def test_profile_realtime_audio():
    assert (
        frozenset({frozenset({"text"}), frozenset({"audio_path"}), frozenset({"text", "audio_path"})})
        == REALTIME_AUDIO_INPUT_MODALITIES
    )
    assert frozenset({frozenset({"text"}), frozenset({"audio_path"})}) == REALTIME_AUDIO_OUTPUT_MODALITIES


def test_profile_video_generation_output():
    assert frozenset({frozenset({"video_path"})}) == VIDEO_GENERATION_OUTPUT_MODALITIES


@pytest.mark.parametrize(
    ("input_profile", "output_profile"),
    [
        (TEXT_ONLY_MODALITIES, TEXT_ONLY_MODALITIES),
        (VISION_INPUT_MODALITIES, TEXT_ONLY_MODALITIES),
        (IMAGE_EDIT_INPUT_MODALITIES, frozenset({frozenset({"image_path"})})),
        (REALTIME_AUDIO_INPUT_MODALITIES, REALTIME_AUDIO_OUTPUT_MODALITIES),
        (TEXT_ONLY_MODALITIES, VIDEO_GENERATION_OUTPUT_MODALITIES),
    ],
    ids=["text_chat", "vision", "image_edit", "realtime_audio", "video_generation"],
)
def test_get_mock_target_builds_each_profile(input_profile, output_profile):
    """Every named profile builds a real target whose capabilities equal the profile on both sides."""
    target = get_mock_target(input_modalities=input_profile, output_modalities=output_profile)
    assert target.configuration.capabilities.input_modalities == input_profile
    assert target.configuration.capabilities.output_modalities == output_profile
